"""Longer backtest of the GlassSphere reading on a Binance USDT perpetual (BTC, ETH or SOL), 1H, Jan 2022 - Sep 2026.

Same evaluation as glasssphere_backtest.py (stops, liquidations from hourly OI growth, signals, first touch of the
largest pool above / below), on the free Binance archive at data.binance.vision:
  klines   1H candles with taker buy volume (monthly files)
  metrics  open interest every 5 minutes (daily files, from Dec 2021)
  funding  funding rate (monthly files)
The archive has no liquidations, so the liquidation model cannot be calibrated here (that is done on Gate).

    python3 scripts/glasssphere_backtest_long.py [cache_dir] [--symbol BTCUSDT|ETHUSDT|SOLUSDT]   (default data/)
Writes a section per symbol into backtests/glasssphere.md (replacing that symbol's earlier one), and prints the numbers the
page's BT constants take (BT_JSON line).
"""
import csv
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glasssphere_backtest as BT  # noqa: E402
import glasssphere_model as M  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYM = sys.argv[sys.argv.index('--symbol') + 1] if '--symbol' in sys.argv else 'BTCUSDT'
_args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith('--') and sys.argv[i - 1] != '--symbol']
CACHE = os.path.join(_args[0] if _args else os.path.join(ROOT, 'data'),
                     'binance_btc_archive' if SYM == 'BTCUSDT' else f'binance_{SYM[:-4].lower()}_archive')
OUT = os.path.join(ROOT, 'backtests', 'glasssphere.md')
ARCHIVE = 'https://data.binance.vision/data/futures/um/'
FIRST, LAST = (2022, 1), (2026, 9)
STEP = 6          # evaluate every 6 hours
MARK = f'## 4. Longer test: Binance {SYM}, 2022 – 2026'
HORIZONS = [(1, 0.0), (1, 0.3), (3, 0.0), (3, 0.3), (3, 0.5), (7, 0.3), (7, 0.5)]   # (days, min distance / range)
FEE = 0.001       # round trip, taker in and out


def months():
    y, m = FIRST
    while (y, m) <= LAST:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_csv(url):
    """Rows of the one CSV inside a zip from the archive, cached; [] when the file does not exist."""
    path = os.path.join(CACHE, url.rsplit('/', 1)[1])
    if not os.path.exists(path):
        for i in range(4):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'curl/8.0'})
                with urllib.request.urlopen(req, timeout=60) as r:
                    open(path, 'wb').write(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return []
                time.sleep(2 ** i)
            except Exception:
                time.sleep(2 ** i)
        else:
            return []
    z = zipfile.ZipFile(path)
    rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), 'utf-8')))
    return rows[1:] if rows and not rows[0][0][:1].isdigit() else rows


def load():
    os.makedirs(CACHE, exist_ok=True)
    ms = list(months())
    days = []
    t = time.mktime((FIRST[0], FIRST[1], 1, 12, 0, 0, 0, 0, 0)) - time.timezone
    end = time.mktime((LAST[0] + (LAST[1] == 12), LAST[1] % 12 + 1, 1, 12, 0, 0, 0, 0, 0)) - time.timezone
    while t < end:
        days.append(time.strftime('%Y-%m-%d', time.gmtime(t)))
        t += 86400
    with ThreadPoolExecutor(16) as ex:
        kl = ex.map(lambda ym: fetch_csv(f'{ARCHIVE}monthly/klines/{SYM}/1h/{SYM}-1h-{ym[0]}-{ym[1]:02d}.zip'), ms)
        fr = ex.map(lambda ym: fetch_csv(f'{ARCHIVE}monthly/fundingRate/{SYM}/{SYM}-fundingRate-{ym[0]}-{ym[1]:02d}.zip'), ms)
        me = ex.map(lambda d: fetch_csv(f'{ARCHIVE}daily/metrics/{SYM}/{SYM}-metrics-{d}.zip'), days)
        kl, fr, me = list(kl), list(fr), list(me)
    candles, stats = [], {}
    for rows in kl:
        for r in rows:
            t, q, tbq = int(r[0]), float(r[7]), float(r[10])
            candles.append([t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), q])
            sell = q - tbq
            stats.setdefault(str(t // 1000), {})['taker'] = tbq / sell if sell > 0 else 1.0
    for rows in me:
        for r in rows:
            ts = time.strptime(r[0], '%Y-%m-%d %H:%M:%S')
            if ts.tm_min == 0:
                sec = int(time.mktime(ts) - time.timezone)
                stats.setdefault(str(sec), {})['oi'] = float(r[3])
    funding = sorted((int(r[0]) // 1000, float(r[2])) for rows in fr for r in rows)
    candles.sort()
    # keep hours that have both OI and taker data; others count as missing
    stats = {k: v for k, v in stats.items() if v.get('oi', 0) > 0 and 'taker' in v}
    return candles, stats, funding


def horizon_test(candles, stats, step=12):
    """Targets for longer horizons: the largest pool above / below between lo x and 1 x the expected range of
    `days` (one average day's range x sqrt(days)). Records which is hit first within `days`, and the result of a
    trade toward the more likely target with the stop at the other one (closed at the horizon if neither)."""
    book = M.LiqBook('taker')
    out = {h: [] for h in HORIZONS}
    n = len(candles)
    for k, c in enumerate(candles):
        t = c[0] // 1000
        a, b = stats.get(str(t)), stats.get(str(t + 3600))
        book.step(k, c, a and a['oi'], b and b['oi'], a['taker'] / (1 + a['taker']) if a else None)
        if k < M.WINDOW or k % step or k + 24 * 7 >= n:
            continue
        win = candles[k - M.WINDOW + 1:k + 1]
        P = c[4]
        pl = M.pools(M.estimate_stops(win, P), [(q[0], q[1], q[2]) for q in book.pos], P, rng=0.15)
        r24 = M.r24_of(win)
        for days, lo in HORIZONS:
            rh = r24 * days ** 0.5
            ups = [q for q in pl if P + lo * rh < q[0] <= P + rh]
            dns = [q for q in pl if P - rh <= q[0] < P - lo * rh]
            if not ups or not dns:
                continue
            tu, td = max(ups, key=lambda q: q[1])[0], max(dns, key=lambda q: q[1])[0]
            p_up = (P - td) / (tu - td)
            first = None
            for x in candles[k + 1:k + 1 + 24 * days]:
                if x[2] >= tu or x[3] <= td:
                    first = 'both' if x[2] >= tu and x[3] <= td else 'up' if x[2] >= tu else 'down'
                    break
            end = candles[k + 24 * days][4]
            go_up = p_up >= 0.5                       # trade toward the more likely (nearer) target
            tgt, stp = (tu / P - 1, 1 - td / P) if go_up else (1 - td / P, tu / P - 1)
            if first in ('up', 'down'):
                pnl = tgt if (first == 'up') == go_up else -stp
            elif first == 'both':
                pnl = -stp                            # same candle: assume the stop came first
            else:
                pnl = (end / P - 1) * (1 if go_up else -1)
            out[(days, lo)].append({'t': c[0], 'p_up': p_up, 'first': first, 'tgt': tgt, 'stp': stp, 'pnl': pnl - FEE,
                                    'gross': pnl})
    return out


def horizon_lines(out, step=12):
    lines = ['', '### Bigger targets: which is hit first, and trading toward the more likely one', '',
             'Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon '
             '(average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, '
             f'close at the horizon if neither is reached; {FEE:.1%} round-trip cost. Evaluated every {step}h.', '',
             '| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | '
             'Avg trade (gross) | Avg trade (after cost) | t |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for (days, lo), rs in out.items():
        if not rs:
            continue
        pred = sum(max(r['p_up'], 1 - r['p_up']) for r in rs) / len(rs)
        hit = [r for r in rs if r['first'] in ('up', 'down', 'both')]
        act = sum(1 for r in hit if r['first'] != 'both' and (r['first'] == 'up') == (r['p_up'] >= 0.5)) / max(len(hit), 1)
        neither = 1 - len(hit) / len(rs)
        tg = sorted(r['tgt'] for r in rs)[len(rs) // 2]
        sp = sorted(r['stp'] for r in rs)[len(rs) // 2]
        g = sum(r['gross'] for r in rs) / len(rs)
        m = sum(r['pnl'] for r in rs) / len(rs)
        sd = (sum((r['pnl'] - m) ** 2 for r in rs) / len(rs)) ** 0.5
        neff = max(len(rs) * step / (24 * days), 2)
        lines.append(f'| {days} d | {lo:.1f} × | {len(rs)} | {tg:.2%} / {sp:.2%} | {pred:.0%} | {act:.0%} | {neither:.0%} | '
                     f'{g:+.3%} | {m:+.3%} | {m / sd * neff ** 0.5 if sd else 0:+.1f} |')
    return lines


def main():
    candles, stats, funding = load()
    print(f'{len(candles)} candles, {len(stats)} hours with OI, {len(funding)} funding points')
    rows = BT.evaluate(candles, stats, funding, 'taker', M.LEV, STEP)
    keys = ['mom', 'pull', 'oi', 'fund', 'cvd']
    names = {'mom': 'Momentum', 'pull': 'Liquidity pull', 'oi': 'Open interest', 'fund': 'Funding (contrarian)',
             'cvd': 'CVD (taker buying)'}
    years = sorted({time.gmtime(r['t'] / 1000).tm_year for r in rows})
    by_year = {y: [r for r in rows if time.gmtime(r['t'] / 1000).tm_year == y] for y in years}
    lines = [MARK, '',
             f'Binance {SYM} perpetual from the free archive at data.binance.vision: 1H candles, open interest '
             f'(5-minute, sampled on the hour), taker buy volume and funding. {len(candles)} hours, evaluated every '
             f'{STEP}h after the {M.WINDOW}-bar warm-up ({len(rows)} points), the same model and leverage mix as above. '
             'No liquidation data in the archive, so section 1 cannot be repeated here.', '',
             '### Signals against the next 24h return, IC per year', '',
             '| Signal | ' + ' | '.join(str(y) for y in years) + ' | All |',
             '|---|' + '---|' * (len(years) + 1)]
    for k in keys:
        cells = [f'{BT.stats_for(by_year[y], k, STEP)[0]:+.3f}' for y in years]
        a = BT.stats_for(rows, k, STEP)
        lines.append(f'| {names[k]} | ' + ' | '.join(cells) + f' | {a[0]:+.3f} (t {a[1]:+.1f}) |')
    lines += ['', '### Which pool is hit first, per year', '',
              '| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |',
              '|---|---|---|---|---|---|']
    for label, rs in [(str(y), by_year[y]) for y in years] + [('All', rows)]:
        hits = [r for r in rs if r['touch'] and r['touch'][0] in ('up', 'down')]
        tot = [r for r in rs if r['touch']]
        if not hits:
            continue
        pull_ok = sum((r['pull'] > 0) == (r['touch'][0] == 'up') for r in hits) / len(hits)
        near_ok = sum(r['touch'][1] == r['touch'][0] for r in hits) / len(hits)
        brier = sum((r['touch'][2] - (r['touch'][0] == 'up')) ** 2 for r in hits) / len(hits)
        none = sum(1 for r in tot if r['touch'][0] is None) / len(tot)
        lines.append(f'| {label} | {len(hits)} | {pull_ok:.0%} | {near_ok:.0%} | {brier:.3f} | {none:.0%} |')
    lines += ['', '| Predicted P(up first) | Points | Actually up first |', '|---|---|---|']
    hits = [r for r in rows if r['touch'] and r['touch'][0] in ('up', 'down')]
    for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
        b = [r for r in hits if lo <= r['touch'][2] < hi]
        if b:
            lines.append(f'| {lo:.0%}–{min(hi, 1):.0%} (mean {sum(r["touch"][2] for r in b) / len(b):.0%}) | {len(b)} | '
                         f'{sum(r["touch"][0] == "up" for r in b) / len(b):.0%} |')
    hz = horizon_test(candles, stats)
    lines += horizon_lines(hz)
    # the page's BT constants for this asset
    tot = [r for r in rows if r['touch']]
    bt = {'neither': round(sum(1 for r in tot if r['touch'][0] is None) / len(tot), 2), 'calib': [],
          'ic': {k: [round(BT.stats_for(rows, k, STEP)[0], 3),
                     sum(BT.stats_for(by_year[y], k, STEP)[0] > 0 for y in years)] for k in keys}, 'hz': {}}
    for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
        b = [r for r in hits if lo <= r['touch'][2] < hi]
        if b:
            bt['calib'].append([f'{lo:.0%}–{min(hi, 1):.0%}', round(100 * sum(r['touch'][2] for r in b) / len(b)),
                                round(100 * sum(r['touch'][0] == 'up' for r in b) / len(b))])
    for days in (1, 3, 7):
        rs = hz[(days, 0.3)]
        hit = [r for r in rs if r['first'] in ('up', 'down', 'both')]
        m = sum(r['pnl'] for r in rs) / len(rs)
        sd = (sum((r['pnl'] - m) ** 2 for r in rs) / len(rs)) ** 0.5
        bt['hz'][days] = {'tgt': round(100 * sorted(r['tgt'] for r in rs)[len(rs) // 2], 2),
                          'stp': round(100 * sorted(r['stp'] for r in rs)[len(rs) // 2], 2),
                          'pred': round(100 * sum(max(r['p_up'], 1 - r['p_up']) for r in rs) / len(rs)),
                          'act': round(100 * sum(1 for r in hit if r['first'] != 'both' and
                                                 (r['first'] == 'up') == (r['p_up'] >= 0.5)) / max(len(hit), 1)),
                          'neither': round(100 * (1 - len(hit) / len(rs))), 'ev': round(100 * m, 3),
                          't': round(m / sd * max(len(rs) * 12 / (24 * days), 2) ** 0.5, 1) if sd else 0}
    print('BT_JSON', json.dumps(bt, ensure_ascii=False))
    text = open(OUT).read()
    section = '\n'.join(lines) + '\n'
    if MARK in text:      # replace this symbol's section, keep the others
        a = text.index(MARK)
        b = text.find('\n## ', a + len(MARK))
        text = text[:a] + section + (text[b:] if b >= 0 else '')
    else:
        text = text.rstrip() + '\n\n' + section
    open(OUT, 'w').write(text)
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
