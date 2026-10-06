"""Settings search for Liquidity Trail Signals [BOSWaves] on BTCUSDT perpetual. No dependencies.

The indicator (open source, Pine v6): EMA(close, MA Length) is the baseline, the trail sits
Trail Distance x ATR(ATR Length) below it in an uptrend (above it in a downtrend) and only ratchets
in the trend's direction. A close through the trail flips the trend (the diamond signal). Its
Position Tool puts the stop-loss at the trail and TP1-3 at R multiples of that risk. Only MA Length,
ATR Length, Trail Distance, Entry Mode and the TP R values change the signals; the liquidity zone,
label and extend inputs are drawing options and are not tested.

Strategies (signals on the bar close, fills at the next bar's open, 0.075% cost per side, 1x, full
equity per trade; when a candle touches both the stop and a target, the stop is assumed first):
  sar        always in the market: long on the bull flip, short on the bear flip
  flip       Position Tool stop (trail at the signal) + exit on the opposite flip, no target
  tpN        same, but the whole position takes profit at N x R
  scale      1/3 off at 1R, 2R and 3R (the indicator's TP1-3), rest on the stop or the flip
Entry 'signal' enters on the flip (Signal Change); 'retest' waits for the first candle after the
flip that touches the flip-time trail and closes back on the trend side (Trail Retest).

Settings are picked on the train window and then checked on the later test window they never saw.

    python3 scripts/liquidity_trail_backtest.py              # uses/downloads ./data like ribbon_backtest.py
    python3 scripts/liquidity_trail_backtest.py --tf 4H 12H  # only some timeframes
"""
import argparse, math, os, sys, time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ribbon_backtest import DAY, load, resample  # noqa: E402

COST = 0.00075
TFS = {'15m': ('5m', 15), '30m': ('5m', 30), '1H': ('1H', 60), '2H': ('1H', 120), '4H': ('1H', 240),
       '6H': ('1H', 360), '12H': ('1H', 720), '1D': ('1H', 1440)}
MA_LENS = (10, 14, 20, 28, 35, 50, 70, 100, 150, 200)
ATR_LENS = (7, 10, 15, 20, 30, 50)
MULTS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0)
EXITS = {'sar': None, 'flip': (), 'tp1': (1,), 'tp2': (2,), 'tp3': (3,), 'tp5': (5,), 'scale': (1, 2, 3)}
DEFAULT = (28, 15, 1.25)
WARMUP = 400


def ema(src, n):  # ta.ema
    a, out, v = 2 / (n + 1), [], None
    for x in src:
        v = x if v is None else v + a * (x - v)
        out.append(v)
    return out


def atr(h, l, c, n):  # ta.atr: RMA of true range, seeded with the SMA of the first n
    out, v, s = [], None, 0.0
    for i in range(len(c)):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if v is None:
            s += tr
            if i == n - 1:
                v = s / n
        else:
            v += (tr - v) / n
        out.append(v)
    return out


def trail_engine(c, ma, at, mult):
    """Line-for-line port of the indicator's Trend Engine. Returns (trend, trail) per bar."""
    N = len(c)
    trend, trail = [0] * N, [0.0] * N
    tr_, tn, prev = None, 1, 0.0
    for i in range(N):
        if at[i] is None:
            continue
        up, dn = ma[i] - at[i] * mult, ma[i] + at[i] * mult
        if tr_ is None:
            tr_, tn = (up, 1) if c[i] > ma[i] else (dn, -1)
        if tn == 1:
            tr_ = max(up, prev)
            if c[i] < tr_:
                tn, tr_ = -1, dn
        else:
            tr_ = min(dn, prev)
            if c[i] > tr_:
                tn, tr_ = 1, up
        prev = tr_
        trend[i], trail[i] = tn, tr_
    return trend, trail


def simulate(o, h, l, c, trend, trail, exit_rs, entry, cost=COST):
    """Returns (equity at every close, list of (exit bar, trade return), bars in market)."""
    N = len(c)
    eq_curve = [1.0] * N
    trades = []
    cash, units, px, sl, tps = 1.0, 0.0, 0.0, 0.0, []   # units > 0 long, < 0 short
    entry_cash = 1.0
    go, go_sl, out = 0, 0.0, False                      # orders for the next open
    pend, pend_trail, in_mkt = 0, 0.0, 0

    def close_units(k, x):
        nonlocal cash, units
        cash += k * (x - px) - cost * abs(k) * x
        units -= k

    def flat(i):
        nonlocal units, tps
        units, tps = 0.0, []
        trades.append((i, cash / entry_cash - 1))

    for i in range(WARMUP, N):
        # 1) orders from the previous close fill at this open
        if out and units:
            close_units(units, o[i])
            flat(i)
        if go and (exit_rs is None or go * (o[i] - go_sl) > 0):
            entry_cash, px, sl = cash, o[i], go_sl
            units = go * cash / px
            cash -= cost * abs(units) * px
            tps = [px + go * r * abs(px - sl) for r in exit_rs] if exit_rs else []
        go, out = 0, False
        # 2) stop / targets inside the candle; the stop wins when both are touched
        if units and exit_rs is not None:
            d = 1 if units > 0 else -1
            if (l[i] <= sl) if d == 1 else (h[i] >= sl):
                close_units(units, min(sl, o[i]) if d == 1 else max(sl, o[i]))
                flat(i)
            else:
                part = abs(units) / len(tps) if tps else 0
                while tps and ((h[i] >= tps[0]) if d == 1 else (l[i] <= tps[0])):
                    tp = tps.pop(0)
                    close_units(units if not tps else d * part, max(tp, o[i]) if d == 1 else min(tp, o[i]))
                if not tps and exit_rs and abs(units) < 1e-12:
                    flat(i)
        if units:
            in_mkt += 1
        eq_curve[i] = cash + units * (c[i] - px)
        if i == N - 1:
            break
        # 3) signals on this close
        flip = trend[i] if trend[i] != trend[i - 1] else 0
        if flip:
            pend, pend_trail = flip, trail[i]
            if units and (units > 0) != (flip > 0):
                out = True
        if exit_rs is None or entry == 'signal':
            if flip:
                go, go_sl = flip, trail[i]
        elif pend and (l[i] <= pend_trail <= c[i] if pend == 1 else h[i] >= pend_trail >= c[i]):
            go, go_sl, pend = pend, trail[i], 0
    if units:
        close_units(units, c[-1])
        flat(N - 1)
    return eq_curve, trades, in_mkt


def stats(t, eq, trades, s, e):
    """Window [s, e) of bar indexes."""
    base = eq[s - 1] if s > 0 else 1.0
    peak, mdd = base, 0.0
    for v in eq[s:e]:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    ret = eq[e - 1] / base - 1
    years = (t[e - 1] - t[s]) / DAY / 365
    tr = [r for k, r in trades if s <= k < e]
    wins = [r for r in tr if r > 0]
    loss = -sum(r for r in tr if r <= 0)
    cagr = (1 + ret) ** (1 / years) - 1 if years > 0 and ret > -1 else -1
    return {'return': ret * 100, 'cagr': cagr * 100, 'max_dd': mdd * 100, 'trades': len(tr),
            'per_year': len(tr) / years if years else 0, 'win_rate': 100 * len(wins) / max(1, len(tr)),
            'pf': sum(wins) / loss if loss else float('inf'),
            'mar': cagr / max(mdd, 0.01) if cagr > -1 else -9}


def yearly(t, eq, s, e):
    out, y0, base = {}, None, eq[s - 1]
    for i in range(s, e):
        y = time.gmtime(t[i] / 1000).tm_year
        if y != y0:
            if y0 is not None:
                out[y0] = (eq[i - 1] / base - 1) * 100
                base = eq[i - 1]
            y0 = y
    out[y0] = (eq[e - 1] / base - 1) * 100
    return out


G = {}


def init(series, windows):
    G['s'], G['w'] = series, windows


def run_combo(args):
    ma_len, atr_len, mult = args
    t, o, h, l, c, MA, AT = G['s']
    trend, trail = trail_engine(c, MA[ma_len], AT[atr_len], mult)
    out = []
    for ex, rs in EXITS.items():
        for entry in (('signal',) if rs is None else ('signal', 'retest')):
            eq, trades, _ = simulate(o, h, l, c, trend, trail, rs, entry)
            out.append(((ma_len, atr_len, mult, ex, entry),
                        {w: stats(t, eq, trades, s, e) for w, (s, e) in G['w'].items()}))
    return out


def prep(candles):
    t, o, h, l, c = map(list, zip(*candles))
    MA = {n: ema(c, n) for n in MA_LENS}
    AT = {n: atr(h, l, c, n) for n in ATR_LENS}
    return t, o, h, l, c, MA, AT


def windows_for(t, split_ms):
    first = WARMUP
    s = next(i for i in range(len(t)) if t[i] >= split_ms)
    return {'train': (first, s), 'test': (s, len(t))}


def fmt(r):
    return (f"{r['return']:+.0f}% / {r['max_dd']:.0f}% / {r['trades']} / {r['win_rate']:.0f}% / "
            f"{min(r['pf'], 99):.2f}")


def neighbours(key, idx):
    """Same exit/entry, every grid point within one step of each tested setting."""
    m, n, k, ex, en = key
    im, jn, kk = MA_LENS.index(m), ATR_LENS.index(n), MULTS.index(k)
    for a in (-1, 0, 1):
        for b in (-1, 0, 1):
            for d in (-1, 0, 1):
                if 0 <= im + a < len(MA_LENS) and 0 <= jn + b < len(ATR_LENS) and 0 <= kk + d < len(MULTS):
                    r = idx.get((MA_LENS[im + a], ATR_LENS[jn + b], MULTS[kk + d], ex, en))
                    if r:
                        yield r


def pick(rows, min_trades):
    """Best train MAR (CAGR / max drawdown), averaged with the neighbouring settings so a lone spike
    does not win. Only uses the train window."""
    idx = dict(rows)
    best = []
    for key, r in rows:
        if r['train']['trades'] < min_trades:
            continue
        nb = [x['train']['mar'] for x in neighbours(key, idx)]
        best.append((sum(nb) / len(nb), key, r))
    best.sort(key=lambda x: -x[0])
    return best


def label(key):
    m, n, k, ex, en = key
    return f"{m} / {n} / {k:g}, {ex}, {en}"


def report(results, series):
    print('# Liquidity Trail Signals [BOSWaves] - BTCUSDT perpetual settings search\n')
    print('Generated by `scripts/liquidity_trail_backtest.py`. Settings = MA Length / ATR Length / Trail Distance.')
    print(f'{len(MA_LENS) * len(ATR_LENS) * len(MULTS)} settings x 13 strategy variants per timeframe. '
          '1x, full equity per trade, 0.075% cost per side, signal on the close, fill at the next open.\n')
    head = ('| TF | Settings | Exit | Entry | Train: return / DD / trades / win / PF | '
            'Test: return / DD / trades / win / PF |\n|---|---|---|---|---|---|')
    picks = {}
    print('## Picked on the train window, checked on the test window\n')
    print('Train and test dates per timeframe:\n')
    for tf, R in results.items():
        w, (t, c) = R['windows'], series[tf][0::4]
        s = next(i for i in range(len(t)) if time.strftime('%Y-%m-%d', time.gmtime(t[i] / 1000)) >= w['test'][0])
        print(f"- {tf}: train {w['train'][0]} to {w['train'][1]} (buy & hold {(c[s - 1] / c[WARMUP - 1] - 1) * 100:+.0f}%), "
              f"test {w['test'][0]} to {w['test'][1]} (buy & hold {(c[-1] / c[s - 1] - 1) * 100:+.0f}%)")
    print('\n' + head)
    for tf, R in results.items():
        yrs = 1 if TFS[tf][0] == '5m' else 4
        ranked = pick(R['rows'], min_trades=max(10, 3 * yrs))
        picks[tf] = ranked[0][1]
        for score, key, r in ranked[:3]:
            m, n, k, ex, en = key
            print(f"| {tf} | {m} / {n} / {k:g} | {ex} | {en} | {fmt(r['train'])} | {fmt(r['test'])} |")
    print('\n## Your current settings (28 / 15 / 1.25)\n\n' + head)
    for tf, R in results.items():
        for key, r in sorted(R['rows']):
            if key[:3] == DEFAULT and key[3] in ('sar', 'flip', 'tp1', 'tp3', 'scale') and key[4] == 'signal':
                print(f"| {tf} | 28 / 15 / 1.25 | {key[3]} | {key[4]} | {fmt(r['train'])} | {fmt(r['test'])} |")
    print('\n## Settings that held up in both windows\n')
    print('Ranked by the weaker of train and test MAR, averaged over the neighbouring settings. This uses the test '
          'window, so it is a robustness check, not an out-of-sample result.\n\n' + head)
    for tf, R in results.items():
        idx, out = dict(R['rows']), []
        for key, r in R['rows']:
            if key[4] == 'signal' and r['train']['trades'] >= 15:
                nb = list(neighbours(key, idx))
                out.append((sum(min(x['train']['mar'], x['test']['mar']) for x in nb) / len(nb), key, r))
        for _, key, r in sorted(out, key=lambda x: -x[0])[:3]:
            m, n, k, ex, en = key
            print(f"| {tf} | {m} / {n} / {k:g} | {ex} | {en} | {fmt(r['train'])} | {fmt(r['test'])} |")
    print('\n## Best on the test window with hindsight (for reference only, not a fair pick)\n\n' + head)
    for tf, R in results.items():
        rows = [x for x in R['rows'] if x[1]['test']['trades'] >= 8]
        key, r = max(rows, key=lambda x: x[1]['test']['mar'])
        m, n, k, ex, en = key
        print(f"| {tf} | {m} / {n} / {k:g} | {ex} | {en} | {fmt(r['train'])} | {fmt(r['test'])} |")
    print('\n## How each exit does across all settings (median test return, % of settings that made money)\n')
    exits = [(ex, en) for ex in EXITS for en in (('signal',) if ex == 'sar' else ('signal', 'retest'))]
    print('| TF | ' + ' | '.join(f'{ex} {en}' for ex, en in exits) + ' |\n|---|' + '---|' * len(exits))
    for tf, R in results.items():
        cells = []
        for ex, en in exits:
            v = sorted(r['test']['return'] for k, r in R['rows'] if k[3] == ex and k[4] == en)
            cells.append(f"{v[len(v) // 2]:+.0f}% ({100 * sum(x > 0 for x in v) / len(v):.0f}%)")
        print(f'| {tf} | ' + ' | '.join(cells) + ' |')
    return picks


def yearly_table(results, series, picks):
    print('\n## Picked settings per calendar year (whole history)\n')
    years = sorted({time.gmtime(x / 1000).tm_year for s in series.values() for x in s[0][WARMUP:]})
    print('| TF | Settings | ' + ' | '.join(map(str, years)) + ' |\n|---|---|' + '---|' * len(years))
    for tf, key in picks.items():
        t, o, h, l, c = series[tf]
        m, n, k, ex, en = key
        trend, trail = trail_engine(c, ema(c, m), atr(h, l, c, n), k)
        eq, _, _ = simulate(o, h, l, c, trend, trail, EXITS[ex], en)
        y = yearly(t, eq, WARMUP, len(t))
        print(f'| {tf} | {label(key)} | ' + ' | '.join(f'{y[x]:+.0f}%' if x in y else '' for x in years) + ' |')
    t, c = max(series.values(), key=lambda x: len(x[0]) * (x[0][1] - x[0][0]))[0::4]
    bh = yearly(t, c, WARMUP, len(t))
    print('| BTC | buy & hold | ' + ' | '.join(f'{bh[x]:+.0f}%' if x in bh else '' for x in years) + ' |')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data', default='data')
    p.add_argument('--tf', nargs='*', default=list(TFS))
    p.add_argument('--test-years', type=float, default=2, help='1H and up: last N years are the test window')
    p.add_argument('--report-only', action='store_true', help='reuse data/lts_results.pkl')
    a = p.parse_args()
    import pickle
    base, series = {}, {}
    for tf in a.tf:
        b = TFS[tf][0]
        if b not in base:
            base[b] = load(a.data, b, 211_000 if b == '5m' else 60_000)
        series[tf] = list(map(list, zip(*resample(base[b], 5 if b == '5m' else 60, TFS[tf][1]))))
    cache = os.path.join(a.data, 'lts_results.pkl')
    if a.report_only:
        results = pickle.load(open(cache, 'rb'))
    else:
        combos = [(m, n, k) for m in MA_LENS for n in ATR_LENS for k in MULTS]
        if DEFAULT not in combos:
            combos.append(DEFAULT)
        results = {}
        for tf in a.tf:
            t = series[tf][0]
            if TFS[tf][0] == '5m':  # ~2 years of 5m history: first half trains, second half tests
                split = t[WARMUP] + (t[-1] - t[WARMUP]) // 2
            else:
                split = t[-1] - int(a.test_years * 365 * DAY)
            W = windows_for(t, split)
            t0 = time.time()
            with Pool(os.cpu_count(), initializer=init, initargs=(prep(list(zip(*series[tf]))), W)) as pool:
                rows = [r for chunk in pool.imap_unordered(run_combo, combos, chunksize=4) for r in chunk]
            fmt_d = lambda i: time.strftime('%Y-%m-%d', time.gmtime(t[i] / 1000))
            results[tf] = {'rows': rows, 'windows': {w: [fmt_d(s), fmt_d(e - 1)] for w, (s, e) in W.items()}}
            print(f'{tf}: {len(rows)} runs in {time.time() - t0:.0f}s', file=sys.stderr, flush=True)
        pickle.dump(results, open(cache, 'wb'))
    picks = report(results, series)
    yearly_table(results, series, picks)
