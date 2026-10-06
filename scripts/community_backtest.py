"""Which TradingView community strategy works best on Bybit BTCUSDT perpetual? Needs numpy and
data/bybit_15m.json (scripts/bybit_data.py).

16 popular open-source / built-in TradingView strategies (ports in pf_search.py plus HalfTrend and QQE MOD
below), each on a wide settings grid, on 15m, 1H, 4H, 12H and 1D. They trade like the originals: in the
market all the time, reversing on the opposite signal. Optional: `filt` = only enter in the direction of
EMA(filt) (exit still on the opposite signal), `sl_atr` = ATR stop, `side` = 'long' to skip the shorts. Signal on the close, fill at the next
open, 0.075% per side (Bybit taker 0.055% + slippage), 1x, full equity per trade.

Settings are picked on the train window (Apr 2020 - Oct 2024) and checked on the test window
(Oct 2024 - Oct 2026).

    python3 scripts/community_backtest.py                # search -> backtests/community_strategies.md
    python3 scripts/community_backtest.py --report-only  # rebuild the report from data/community.pkl
"""
import argparse, itertools, math, os, pickle, sys, time
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_search as P  # noqa: E402

DAY = P.DAY
TFS = ('15m', '1H', '4H', '12H', '1D')
MIN_TRAIN_TRADES = 40


def ema_skipna(x, n):
    """ta.ema for a series that starts with nan values."""
    out = np.full(len(x), np.nan)
    a, v = 2 / (n + 1), None
    for i, xi in enumerate(x):
        if math.isnan(xi):
            continue
        v = xi if v is None else v + a * (xi - v)
        out[i] = v
    return out


def sig_halftrend(D, amplitude):
    """HalfTrend (everget). Only Amplitude changes the signals (Channel Deviation only draws bands)."""
    h, l, c = D['h'], D['l'], D['c']
    hp, lp = P.highest(h, amplitude), P.lowest(l, amplitude)
    hma, lma = P.sma(h, amplitude), P.sma(l, amplitude)
    N = len(c)
    trend = np.zeros(N, int)
    tr, nxt, max_low, min_high = 0, 0, l[0], h[0]
    for i in range(amplitude, N):
        if nxt == 1:
            max_low = max(lp[i], max_low)
            if hma[i] < max_low and c[i] < l[i - 1]:
                tr, nxt, min_high = 1, 0, hp[i]
        else:
            min_high = min(hp[i], min_high)
            if lma[i] > min_high and c[i] > h[i - 1]:
                tr, nxt, max_low = 0, 1, lp[i]
        trend[i] = tr
    prev = np.r_[0, trend[:-1]]
    return (trend == 0) & (prev == 1), (trend == 1) & (prev == 0)


def qqe_line(c, rsi_len, smooth, factor):
    sr = ema_skipna(P.rsi(c, rsi_len), smooth)
    d = np.abs(np.r_[np.nan, sr[:-1]] - sr)
    dar = ema_skipna(d, rsi_len * 2 - 1) * factor
    N = len(c)
    lb, sb, tr, line = np.zeros(N), np.zeros(N), np.zeros(N), np.full(N, np.nan)
    for i in range(1, N):
        if math.isnan(dar[i]):
            continue
        nl, ns = sr[i] - dar[i], sr[i] + dar[i]
        lb[i] = max(lb[i - 1], nl) if (sr[i - 1] > lb[i - 1] and sr[i] > lb[i - 1]) else nl
        sb[i] = min(sb[i - 1], ns) if (sr[i - 1] < sb[i - 1] and sr[i] < sb[i - 1]) else ns
        cross_s = (sr[i] - sb[i - 1]) * (sr[i - 1] - (sb[i - 2] if i > 1 else sb[i - 1])) < 0
        cross_l = (lb[i - 1] - sr[i]) * ((lb[i - 2] if i > 1 else lb[i - 1]) - sr[i - 1]) < 0
        tr[i] = 1 if cross_s else -1 if cross_l else tr[i - 1]
        line[i] = lb[i] if tr[i] == 1 else sb[i]
    return line, sr


def sig_qqe(D, rsi_len, factor, thr, bb_mult):
    """QQE MOD (Mihkel00): long when the histogram turns blue (both RSI filters up), short on red."""
    c = D['c']
    line, sr = qqe_line(c, rsi_len, 5, factor)
    basis, dev = P.sma(line - 50, 50), bb_mult * P.stdev(np.nan_to_num(line - 50), 50)
    up = (sr - 50 > thr) & (sr - 50 > basis + dev)
    dn = (sr - 50 < -thr) & (sr - 50 < basis - dev)
    return up & ~np.r_[False, up[:-1]], dn & ~np.r_[False, dn[:-1]]


P.COMMUNITY['halftrend'] = (sig_halftrend, {})
P.COMMUNITY['qqe'] = (sig_qqe, {})

GRID = {
    'utbot':       dict(a=(0.5, 1, 1.5, 2, 3, 4), c=(5, 10, 14, 20, 30), ha=(False, True)),
    'rangefilter': dict(per=(20, 50, 100, 150, 200, 300), mult=(1.5, 2.0, 2.5, 3.0, 4.0, 5.0)),
    'chandelier':  dict(length=(10, 14, 22, 30, 44, 60), mult=(1.5, 2.0, 2.5, 3.0, 4.0, 5.0)),
    'supertrend':  dict(atr_len=(7, 10, 14, 20, 30), factor=(1.5, 2.0, 2.5, 3.0, 4.0, 5.0)),
    'ssl':         dict(n=(5, 10, 14, 20, 30, 50, 100)),
    'squeeze':     dict(n=(10, 14, 20, 30, 40, 50), kc=(1.0, 1.25, 1.5, 2.0)),
    'macd_tv':     dict(fast=(8, 12, 16), slow=(21, 26, 34), sig=(5, 9, 13)),
    'rsi_tv':      dict(n=(7, 14, 21), os_=(20, 25, 30, 35)),
    'bb_tv':       dict(n=(10, 20, 30, 50, 100), k=(1.5, 2.0, 2.5, 3.0)),
    'keltner_tv':  dict(n=(10, 20, 30, 50, 100), k=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0)),
    'psar_tv':     dict(start=(0.005, 0.01, 0.02, 0.03), mx=(0.05, 0.1, 0.2)),
    'hull':        dict(n=(9, 14, 21, 34, 55, 80, 100, 150, 200)),
    'macross_tv':  dict(fast=(5, 9, 20, 50), slow=(18, 50, 100, 200)),
    'stoch_tv':    dict(n=(5, 14, 21), ob=(70, 80, 90)),
    'halftrend':   dict(amplitude=(1, 2, 3, 4, 5, 8, 12)),
    'qqe':         dict(rsi_len=(6, 10, 14), factor=(2.0, 3.0, 4.0), thr=(3, 5), bb_mult=(0.35, 0.7)),
}
COMMON = dict(filt=(0, 50, 200), sl_atr=(0, 2.0, 3.0, 5.0), side=('both', 'long'))
NAMES = {'utbot': 'UT Bot (QuantNomad)', 'rangefilter': 'Range Filter (guikroth)', 'chandelier': 'Chandelier Exit (everget)',
         'supertrend': 'Supertrend', 'ssl': 'SSL Channel (ErwinBeckers)', 'squeeze': 'Squeeze Momentum (LazyBear)',
         'macd_tv': 'MACD Strategy (built-in)', 'rsi_tv': 'RSI Strategy (built-in)', 'bb_tv': 'Bollinger Bands Strategy (built-in)',
         'keltner_tv': 'Keltner Channels Strategy (built-in)', 'psar_tv': 'Parabolic SAR Strategy (built-in)',
         'hull': 'Hull Suite', 'macross_tv': 'MovingAvg2Line Cross (built-in)', 'stoch_tv': 'Stochastic Slow (built-in)',
         'halftrend': 'HalfTrend (everget)', 'qqe': 'QQE MOD (Mihkel00)'}


def grid(name):
    return dict(GRID[name], **COMMON)


def jobs():
    for name in GRID:
        g = grid(name)
        for vals in itertools.product(*g.values()):
            p = dict(zip(g, vals))
            if name == 'macross_tv' and p['fast'] >= p['slow']:
                continue
            yield name, p


G = {}


def init(D, W):
    G['D'], G['W'] = D, W


def backtest(D, name, p, cost=P.COST):
    """Trades of one strategy/setting: list of (entry bar, exit bar, direction, gross return)."""
    sig, stop, tgt, exl, exs, mb = P.make_comm(name)(D, **p)
    sig = sig.copy()
    sig[:300] = 0
    return P.simulate(D['o'], D['h'], D['l'], D['c'], sig, stop, tgt, exl, exs, mb)


def run(job):
    name, p = job
    D, W = G['D'], G['W']
    tr = backtest(D, name, p)
    return name, p, {w: P.stats(D['t'], tr, s, e) for w, (s, e) in W.items()}


def smoothed(res, window):
    """PF of each setting averaged (median) with its one-step neighbours on the grid."""
    by = {(n, tuple(sorted(p.items()))): r for n, p, r in res}
    out = {}
    for n, p, r in res:
        g, vals = grid(n), [r[window]['pf']]
        for k, v in p.items():
            i = g[k].index(v)
            for j in (i - 1, i + 1):
                if 0 <= j < len(g[k]):
                    x = by.get((n, tuple(sorted(dict(p, **{k: g[k][j]}).items()))))
                    if x and x[window]['n'] >= 10:
                        vals.append(x[window]['pf'])
        out[(n, tuple(sorted(p.items())))] = float(np.median(vals))
    return out


def ps(p):
    return ', '.join(f'{k}={v}' for k, v in p.items())


def fmt(r):
    return f"PF {min(r['pf'], 99):.2f}, {r['per_year']:.0f}/yr, {r['ret']:+.0f}%, DD {r['dd']:.0f}%"


def windows(t):
    split = int(np.searchsorted(t, t[-1] - 2 * 365 * DAY))
    return {'train': (300, split), 'test': (split, len(t))}


def report(out):
    print('# Community strategies on Bybit BTCUSDT perpetual\n')
    print('Generated by `scripts/community_backtest.py`. 16 TradingView community / built-in strategies, wide settings '
          'grids, optional EMA trend filter and ATR stop. 0.075% per side (taker + slippage), 1x. Picked on train '
          f'(Apr 2020 – Oct 2024, at least {MIN_TRAIN_TRADES} trades, PF smoothed over neighbouring settings), checked on '
          'test (Oct 2024 – Oct 2026).\n')
    allpicks = []
    for tf, res in out.items():
        sm = smoothed(res, 'train')
        key = lambda x: (x[0], tuple(sorted(x[1].items())))
        best = {}
        for x in res:
            if x[2]['train']['n'] < MIN_TRAIN_TRADES:
                continue
            if x[0] not in best or sm[key(x)] > sm[key(best[x[0]])]:
                best[x[0]] = x
        print(f'## {tf}\n\n| Strategy | Settings | Train | Test |\n|---|---|---|---|')
        for x in sorted(best.values(), key=lambda x: -sm[key(x)]):
            n, p, r = x
            allpicks.append((tf, n, p, r, sm[key(x)]))
            print(f"| {NAMES[n]} | {ps(p)} | {fmt(r['train'])} | {fmt(r['test'])} |")
        print()
    print('## Ranking: train pick per strategy and timeframe, by the weaker of train and test PF\n')
    print('| # | TF | Strategy | Settings | Train | Test |\n|---|---|---|---|---|---|')
    ranked = sorted(allpicks, key=lambda x: -min(x[3]['train']['pf'], x[3]['test']['pf']))
    for i, (tf, n, p, r, s) in enumerate(ranked[:15], 1):
        print(f"| {i} | {tf} | {NAMES[n]} | {ps(p)} | {fmt(r['train'])} | {fmt(r['test'])} |")
    robustness(out, ranked[:10])
    return ranked


def robustness(out, picks, data_dir='data'):
    """Whole-period checks for the top picks: t-stat of the trade returns, losing calendar years, and the
    test PF of every neighbouring setting."""
    print('\n## Robustness of the top 10 (whole period, after fees)\n')
    print('| TF | Strategy | Settings | Trades | PF | Return | Max DD | t-stat | Losing years | Neighbours test PF (median / worst) |')
    print('|---|---|---|---|---|---|---|---|---|---|')
    cache = {}
    for tf, n, p, r, _ in picks:
        if tf not in cache:
            candles = P.load(data_dir, tf)
            cache[tf] = (candles[:, 0], P.prepare(candles))
        t, D = cache[tf]
        tr = backtest(D, n, p)
        x = np.array([q[3] for q in tr]) - 2 * P.COST
        tstat = x.mean() / x.std(ddof=1) * np.sqrt(len(x))
        years = {}
        for q, xr in zip(tr, x):
            years.setdefault(time.gmtime(t[q[0]] / 1000).tm_year, []).append(xr)
        losing = sum(np.prod(1 + np.array(v)) < 1 for v in years.values())
        by = {(a, tuple(sorted(b.items()))): rr for a, b, rr in out[tf]}
        g, nb = grid(n), []
        for k, v in p.items():
            i = g[k].index(v)
            for j in (i - 1, i + 1):
                if 0 <= j < len(g[k]):
                    y = by.get((n, tuple(sorted(dict(p, **{k: g[k][j]}).items()))))
                    if y:
                        nb.append(y['test']['pf'])
        f = P.stats(t, tr, 300, len(t))
        print(f"| {tf} | {NAMES[n]} | {ps(p)} | {len(x)} | {f['pf']:.2f} | {f['ret']:+.0f}% | {f['dd']:.0f}% | {tstat:.2f} | "
              f"{losing} of {len(years)} | {np.median(nb):.2f} / {min(nb):.2f} |")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data')
    ap.add_argument('--tf', nargs='*', default=list(TFS))
    ap.add_argument('--report-only', action='store_true')
    a = ap.parse_args()
    cache = os.path.join(a.data, 'community.pkl')
    if a.report_only:
        out = pickle.load(open(cache, 'rb'))
    else:
        out, J = {}, list(jobs())
        for tf in a.tf:
            candles = P.load(a.data, tf)
            t0 = time.time()
            with Pool(os.cpu_count(), initializer=init, initargs=(P.prepare(candles), windows(candles[:, 0]))) as pool:
                out[tf] = pool.map(run, J, chunksize=4)
            print(f'{tf}: {len(J)} runs in {time.time() - t0:.0f}s', file=sys.stderr, flush=True)
            pickle.dump(out, open(cache, 'wb'))
    report(out)
