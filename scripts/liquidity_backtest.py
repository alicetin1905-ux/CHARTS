"""Backtest the reading of index.html on Gate BTC_USDT perpetual, 1H, last 180 days.

Gate is the one free source with 180 days of hourly open interest, taker buy/sell ratio and real
liquidations (contract_stats). The order-book factor cannot be tested: there is no free book history.

Tests:
  1. Liquidation model: hourly correlation of the USD the model says got liquidated with Gate's real
     long / short liquidations, for several long/short splits and leverage mixes.
  2. Each signal (momentum, liquidity pull, open interest, funding, CVD) against the next 24h return:
     correlation (IC) and hit rate, first 120 days (train) and last 60 days (test) separately.
  3. Liquidity pull as a target: of the largest pool above and below (within one average day's range),
     is the one on the pull side hit first more often than the closer one?
  4. Weights: IC-based weights fitted on train, scored on test, against the page's old fixed weights.

    python3 scripts/liquidity_backtest.py [cache_dir]      (default data/)
Writes backtests/liquidity_map.md.
"""
import json
import math
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import liquidity_model as M  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'data'), 'gate_btc_1h')
OUT = os.path.join(ROOT, 'backtests', 'liquidity_map.md')
API = 'https://api.gateio.ws/api/v4/futures/usdt/'
DAYS = 179
STEP = 4          # evaluate every 4 hours
HORIZON = 24      # forward window in hours
TRAIN_DAYS = 120


def get(url):
    for i in range(5):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'curl/8.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if i == 4:
                raise
            time.sleep(2 ** i)


def load():
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, 'data.json')
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 3600:
        return json.load(open(path))
    end = int(time.time()) // 3600 * 3600
    start = end - DAYS * 86400
    candles, stats = {}, {}
    t = start
    while t < end:
        rows = get(API + f'candlesticks?contract=BTC_USDT&interval=1h&from={t}&to={min(end, t + 1999 * 3600)}')
        for r in rows:
            candles[int(r['t'])] = [int(r['t']) * 1000, float(r['o']), float(r['h']), float(r['l']), float(r['c']),
                                    float(r.get('sum') or 0)]
        t += 2000 * 3600
    t = start
    while t < end:
        rows = get(API + f'contract_stats?contract=BTC_USDT&interval=1h&from={t}&limit=1000')
        if not rows:
            break
        for r in rows:
            stats[r['time']] = {'oi': r['open_interest_usd'], 'taker': r['lsr_taker'],
                                'long_liq': r['long_liq_usd'], 'short_liq': r['short_liq_usd']}
        t = rows[-1]['time'] + 3600
    funding = {}
    t = start
    while t < end:  # the endpoint returns at most 90 rows (30 days of 8h funding) per call
        for r in get(API + f'funding_rate?contract=BTC_USDT&limit=1000&from={t}&to={min(t + 25 * 86400, end - 1)}'):
            funding[r['t']] = float(r['r'])
        t += 25 * 86400
    funding = sorted(funding.items())
    data = {'candles': [candles[k] for k in sorted(candles) if k < end],
            'stats': {str(k): v for k, v in stats.items()}, 'funding': funding}
    json.dump(data, open(path, 'w'))
    return data


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return float('nan')
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else float('nan')


def calibrate(candles, stats):
    """Hourly correlation of model-liquidated USD with Gate's real liquidations."""
    old = [(10, .35), (25, .30), (50, .20), (100, .15)]
    variants = {
        'candle split, mix 10/25/50/100× = 35/30/20/15%': ('candle', old),
        'candle + taker split, same mix': ('taker', old),
        'candle + taker split, low leverage (5/10/25/50× = 30/35/25/10%)': ('taker', [(5, .3), (10, .35), (25, .25), (50, .1)]),
        'candle + taker split, high leverage (25/50/100× = 40/35/25%)': ('taker', [(25, .4), (50, .35), (100, .25)]),
    }
    res = {}
    for name, (split, lev) in variants.items():
        book = M.LiqBook(split, lev)
        est_l, est_s, real_l, real_s = [], [], [], []
        for k, c in enumerate(candles):
            t = c[0] // 1000
            a, b = stats.get(str(t)), stats.get(str(t + 3600))
            tk = a['taker'] / (1 + a['taker']) if a else None
            sw = book.step(k, c, a and a['oi'], b and b['oi'], tk)
            if k >= M.WINDOW and a:
                est_l.append(sw['long']); est_s.append(sw['short'])
                real_l.append(a['long_liq']); real_s.append(a['short_liq'])
        res[name] = (corr(est_l, real_l), corr(est_s, real_s), corr([x + y for x, y in zip(est_l, est_s)],
                                                                    [x + y for x, y in zip(real_l, real_s)]))
    return res


def run():
    data = load()
    candles, stats, funding = data['candles'], data['stats'], data['funding']
    n = len(candles)
    print(f'{n} candles, {len(stats)} stat hours, {len(funding)} funding points')

    calib = calibrate(candles, stats)
    best = max(calib, key=lambda k: calib[k][2])
    split, lev = ('taker', M.LEV)
    old = [(10, .35), (25, .30), (50, .20), (100, .15)]
    for name, (s, l) in {'candle split, mix 10/25/50/100× = 35/30/20/15%': ('candle', old),
                         'candle + taker split, same mix': ('taker', old),
                         'candle + taker split, low leverage (5/10/25/50× = 30/35/25/10%)': ('taker', [(5, .3), (10, .35), (25, .25), (50, .1)]),
                         'candle + taker split, high leverage (25/50/100× = 40/35/25%)': ('taker', [(25, .4), (50, .35), (100, .25)])}.items():
        if name == best:
            split, lev = s, l

    book = M.LiqBook(split, lev)
    rows = []
    t_end_train = candles[0][0] + TRAIN_DAYS * 86400e3
    for k in range(n - HORIZON):
        c = candles[k]
        t = c[0] // 1000
        a, b = stats.get(str(t)), stats.get(str(t + 3600))
        tk = a['taker'] / (1 + a['taker']) if a else None
        book.step(k, c, a and a['oi'], b and b['oi'], tk)
        if k < M.WINDOW or k % STEP:
            continue
        win = candles[k - M.WINDOW + 1:k + 1]
        P = c[4]
        stops = M.estimate_stops(win, P)
        liqs = [(q[0], q[1], q[2]) for q in book.pos]
        pl = M.pools(stops, liqs, P)
        pull = M.pull_signal(pl, P)
        closes = [x[4] for x in win]
        mom, ret24 = M.momentum_signal(closes)
        o_now, o_then = stats.get(str(t + 3600)), stats.get(str(t - 23 * 3600))
        oi_chg = o_now['oi'] / o_then['oi'] - 1 if o_now and o_then else None
        oi_s = M.oi_signal(oi_chg, ret24)
        fr = [r for ft, r in funding if ft <= t + 3600]
        fund = M.funding_signal(fr[-1]) if fr else 0.0
        last = [stats.get(str(x[0] // 1000)) for x in win[-24:]]
        cvd = M.cvd_signal([x[5] for x in win[-24:]], [s['taker'] / (1 + s['taker']) if s else 0.5 for s in last])
        fwd = candles[k + HORIZON][4] / P - 1
        # first touch of the biggest pool above / below within one average day's range
        rngs = [max(x[2] for x in win[e - 24:e]) - min(x[3] for x in win[e - 24:e]) for e in range(len(win), 23, -24)][:14]
        r24 = sum(rngs) / len(rngs)
        ups = [q for q in pl if P < q[0] <= P + r24]
        dns = [q for q in pl if P - r24 <= q[0] < P]
        touch = None
        if ups and dns:
            tu, td = max(ups, key=lambda q: q[1])[0], max(dns, key=lambda q: q[1])[0]
            for x in candles[k + 1:k + 1 + HORIZON]:
                hu, hd = x[2] >= tu, x[3] <= td
                if hu and hd:
                    touch = 'both'
                    break
                if hu or hd:
                    touch = 'up' if hu else 'down'
                    break
            touch = (touch, 'up' if tu - P < P - td else 'down', (P - td) / (tu - td))  # random-walk P(up first)
        rows.append({'t': c[0], 'train': c[0] < t_end_train, 'fwd': fwd, 'mom': mom, 'pull': pull, 'oi': oi_s,
                     'fund': fund, 'cvd': cvd, 'touch': touch})
    return calib, best, rows


def stats_for(rows, key):
    xs, ys = [r[key] for r in rows], [r['fwd'] for r in rows]
    ic = corr(xs, ys)
    sel = [r for r in rows if abs(r[key]) > 0.2]
    hit = sum((r[key] > 0) == (r['fwd'] > 0) for r in sel) / len(sel) if sel else float('nan')
    neff = len(rows) * STEP / HORIZON
    return ic, ic * math.sqrt(max(neff - 2, 1)) / math.sqrt(max(1 - ic * ic, 1e-9)), hit, len(sel)


def main():
    calib, best, rows = run()
    train = [r for r in rows if r['train']]
    test = [r for r in rows if not r['train']]
    keys = ['mom', 'pull', 'oi', 'fund', 'cvd']
    names = {'mom': 'Momentum', 'pull': 'Liquidity pull', 'oi': 'Open interest', 'fund': 'Funding (contrarian)',
             'cvd': 'CVD (taker buying)'}
    old_w = {'mom': .30, 'pull': .30, 'oi': .15, 'fund': 0, 'cvd': 0}
    # IC-weighted: keep the measured sign, weight by |IC| on train when its t-stat is above 1
    fit = {}
    for k in keys:
        ic, tt, _, _ = stats_for(train, k)
        fit[k] = ic if abs(tt) > 1 else 0.0
    tot = sum(abs(v) for v in fit.values()) or 1
    fit_w = {k: v / tot for k, v in fit.items()}

    def score(rs, w):
        return [sum(w[k] * r[k] for k in keys) for r in rs]

    lines = ['# Liquidity map reading — backtest', '',
             f'Gate BTC_USDT perpetual, 1H, {len(rows) * STEP // 24} days evaluated every {STEP}h after a '
             f'{M.WINDOW}-bar warm-up ({rows[0]["t"] and time.strftime("%Y-%m-%d", time.gmtime(rows[0]["t"] / 1000))} → '
             f'{time.strftime("%Y-%m-%d", time.gmtime(rows[-1]["t"] / 1000))}). Train = first {TRAIN_DAYS} days of data, '
             f'test = the rest ({len(train)} / {len(test)} points). Forward return over {HORIZON}h. '
             'Overlapping windows: the t-stat uses n / 6 effective points.', '',
             'The order book factor is not tested (no free order book history).', '',
             '## 1. Liquidation model against real liquidations', '',
             'Hourly correlation of the USD the model liquidates when price sweeps its levels with Gate\'s real liquidations '
             '(Gate OI only, so the level is not comparable, only the timing).', '',
             '| Variant | Longs | Shorts | Total |', '|---|---|---|---|']
    for k, (a, b, c) in calib.items():
        lines.append(f'| {k}{" **(used)**" if k == best else ""} | {a:.2f} | {b:.2f} | {c:.2f} |')
    lines += ['', '## 2. Signals against the next 24h return', '',
              '| Signal | IC train | t | Hit train | IC test | t | Hit test | n test (|s|>0.2) |', '|---|---|---|---|---|---|---|---|']
    for k in keys:
        a, b = stats_for(train, k), stats_for(test, k)
        lines.append(f'| {names[k]} | {a[0]:+.3f} | {a[1]:+.1f} | {a[2]:.0%} | {b[0]:+.3f} | {b[1]:+.1f} | {b[2]:.0%} | {b[3]} |')
    for label, w in (('Old weights (30/30/15, book left out)', old_w), ('IC-fitted on train', fit_w)):
        for part, rs in (('train', train), ('test', test)):
            s = score(rs, w)
            ic = corr(s, [r['fwd'] for r in rs])
            sel = [(x, r['fwd']) for x, r in zip(s, rs) if abs(x) > 0.1]
            hit = sum((x > 0) == (f > 0) for x, f in sel) / len(sel) if sel else float('nan')
            lines.append(f'| Score: {label}, {part} | {ic:+.3f} | | {hit:.0%} | | | | {len(sel)} |')
    lines += ['', 'Fitted weights: ' + ', '.join(f'{names[k]} {fit_w[k]:+.2f}' for k in keys), '']
    # 3. first touch
    lines += ['## 3. Which pool is hit first', '',
              'At each point: the largest stop + liquidation pool above and below the price within one average day\'s '
              'range. Which is reached first within 24h?', '',
              '| Part | Points with a hit | Pull side first | Closer side first | When they disagree: pull side wins |',
              '|---|---|---|---|---|']
    for part, rs in (('train', train), ('test', test), ('all', rows)):
        hits = [r for r in rs if r['touch'] and r['touch'][0] in ('up', 'down')]
        pull_ok = sum((r['pull'] > 0) == (r['touch'][0] == 'up') for r in hits)
        near_ok = sum(r['touch'][1] == r['touch'][0] for r in hits)
        dis = [r for r in hits if (r['pull'] > 0) != (r['touch'][1] == 'up')]
        dis_ok = sum((r['pull'] > 0) == (r['touch'][0] == 'up') for r in dis)
        lines.append(f'| {part} | {len(hits)} | {pull_ok / max(len(hits), 1):.0%} | {near_ok / max(len(hits), 1):.0%} | '
                     f'{dis_ok}/{len(dis)} = {dis_ok / max(len(dis), 1):.0%} |')
    lines += ['', 'A random walk without drift reaches the upper of two levels first with probability '
              '(distance to lower) / (sum of distances). Does that hold for these pools?', '',
              '| Predicted P(up first) | Points | Actually up first |', '|---|---|---|']
    hits = [r for r in rows if r['touch'] and r['touch'][0] in ('up', 'down')]
    for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
        b = [r for r in hits if lo <= r['touch'][2] < hi]
        if b:
            lines.append(f'| {lo:.0%}–{min(hi, 1):.0%} (mean {sum(r["touch"][2] for r in b) / len(b):.0%}) | {len(b)} | '
                         f'{sum(r["touch"][0] == "up" for r in b) / len(b):.0%} |')
    brier = sum((r['touch'][2] - (r['touch'][0] == 'up')) ** 2 for r in hits) / max(len(hits), 1)
    none = sum(1 for r in rows if r['touch'] and r['touch'][0] is None)
    lines += ['', f'Brier score {brier:.3f} (0.25 = coin flip). Neither pool reached within 24h: {none} of '
              f'{sum(1 for r in rows if r["touch"])} points.', '']
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    print(json.dumps({'fit_w': fit_w, 'best': best}))


if __name__ == '__main__':
    main()
