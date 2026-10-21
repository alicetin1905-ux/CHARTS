"""Can filters and exits lift the profit factor of the Delta Volume divergence signals? Train / test check.

Same data, signals and costs as scripts/cvd_divergence_backtest.py (intrabar delta, default indicator settings).
Rule sets are ranked on train (Oct 2020 - Dec 2023) and judged on test (Jan 2024 - Sep 2026), which they never saw.

  side     : both / long only (SELL signals then only close longs)
  trend    : off / chart EMA 200 (longs only above it, shorts only below) / daily EMA 200 (last closed day)
  exit     : opposite signal only / take profit at 1.5, 2 or 3 x the stop distance
  min stop : any / at least 0.6 % from entry (4 x the round-trip cost), so fees are a small share of the risk
A signal a filter rejects still closes an open trade in the other direction; it just does not open one.

Also: how much the intrabar timeframe (1m / 5m / 15m / 1H) used for delta changes the result.

    python3 scripts/cvd_divergence_improve.py        writes backtests/cvd_divergence_improve.md
"""
import calendar, itertools, os, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cvd_divergence_backtest import COST, agg, deltas, first_exit, groups, load, signals  # noqa: E402

OUT = os.path.join('backtests', 'cvd_divergence_improve.md')
SPLIT = calendar.timegm((2024, 1, 1, 0, 0, 0)) * 1000
TFS = [('15m', 15, 1), ('1H', 60, 1), ('4H', 240, 1), ('12H', 720, 1), ('1D', 1440, 5)]
SIDES = ['both', 'long']
TRENDS = ['off', 'EMA 200', '1D EMA 200']
EXITS = [None, 1.5, 2.0, 3.0]
MIN_STOPS = [0.0, 0.006]
MIN_TRAIN_TRADES = 20
LTF_TEST = {'15m': [1, 5], '1H': [1, 5, 15], '4H': [1, 5, 15, 60], '12H': [1, 5, 15, 60], '1D': [5, 15, 60, 240]}


def ema(x, n):
    a, out, e = 2 / (n + 1), np.empty(len(x)), x[0]
    for i, v in enumerate(x):
        e += a * (v - e)
        out[i] = e
    return out


def trade(m, starts, sigs, rr):
    """sigs: (bar, side, stop, may_open). Trades: (entry minute, exit minute, side, entry, exit)."""
    trades, pos = [], None

    def close(j, px, _why=None):
        trades.append((pos[1], j, pos[0], pos[2], px))

    for t, side, stop, may_open in sigs:
        if t + 1 >= len(starts):
            break
        ei = starts[t + 1]
        if pos:
            hit = first_exit(m, pos[5], ei, pos[0], pos[3], pos[4])
            if hit:
                close(*hit)
                pos = None
            else:
                pos[5] = ei
        if pos and pos[0] == side:
            continue
        if pos:
            close(ei, m['o'][ei])
            pos = None
        e = m['o'][ei]
        if not may_open or (e - stop) * side <= 0:
            continue
        pos = [side, ei, e, stop, e + side * rr * abs(e - stop) if rr else None, ei]
    if pos:
        hit = first_exit(m, pos[5], len(m['t']), pos[0], pos[3], pos[4])
        close(*(hit or (len(m['t']) - 1, m['c'][-1])))
    return trades


def summary(m, trades):
    out = {}
    for name, keep in (('train', lambda t: t < SPLIT), ('test', lambda t: t >= SPLIT)):
        r = np.array([s * (x / e - 1) - 2 * COST for i0, _, s, e, x in trades if keep(m['t'][i0])])
        loss = -r[r < 0].sum()
        eq = np.cumprod(1 + r) if len(r) else np.ones(1)
        out[name] = dict(n=len(r), pf=r[r > 0].sum() / loss if loss > 0 else np.inf, net=eq[-1] - 1,
                         mdd=(1 - eq / np.maximum.accumulate(np.r_[1, eq])[1:]).max())
    return out


def main():
    m = load()
    day = groups(m['t'], 1440)
    d_close = m['c'][np.r_[day[1:], len(m['t'])] - 1]
    d_ema = ema(d_close, 200)
    d_time = m['t'][day]
    rows, ltf_rows = [], []
    for tf, minutes, ib in TFS:
        starts = groups(m['t'], minutes)
        bars = agg(m, starts)
        sigs, _ = signals(bars, deltas(m, bars, starts, ib)['intrabar'])
        c = bars['c']
        e200 = ema(c, 200)
        # Daily trend known at each chart bar's close: the last day that closed by then.
        bar_end = bars['t'] + minutes * 60_000
        di = np.searchsorted(d_time + 86_400_000, bar_end, side='right') - 1
        d_up = np.where(di >= 0, d_close[np.maximum(di, 0)] > d_ema[np.maximum(di, 0)], False)
        d_up_ok = di >= 199
        for side_f, trend_f, rr, min_stop in itertools.product(SIDES, TRENDS, EXITS, MIN_STOPS):
            fs = []
            for t, s, stop in sigs:
                ok = side_f == 'both' or s > 0
                if trend_f == 'EMA 200':
                    ok &= t >= 199 and (c[t] > e200[t]) == (s > 0)
                elif trend_f == '1D EMA 200':
                    ok &= bool(d_up_ok[t]) and bool(d_up[t]) == (s > 0)
                ok &= abs(c[t] - stop) / c[t] >= min_stop
                fs.append((t, s, stop, ok))
            rows.append((tf, side_f, trend_f, rr, min_stop, summary(m, trade(m, starts, fs, rr))))
        print(tf, 'done', flush=True)
        if tf in LTF_TEST:
            for ltf in LTF_TEST[tf]:
                d = deltas(m, bars, starts, ltf)['intrabar']
                s2, _ = signals(bars, d)
                for rr in (None, 2.0):
                    ltf_rows.append((tf, ltf, rr, summary(m, trade(m, starts, [(*x, True) for x in s2], rr))))
    write(rows, ltf_rows)


def ex(rr):
    return 'opposite signal' if rr is None else f'{rr:g}R'


def write(rows, ltf_rows):
    ok = [r for r in rows if r[5]['train']['n'] >= MIN_TRAIN_TRADES]
    both = [r for r in ok if r[5]['train']['pf'] > 1 and r[5]['test']['pf'] > 1]
    L = ['# Delta Volume divergence signals: filters and exits, train / test', '',
         'Generated by `scripts/cvd_divergence_improve.py`. Binance USDⓈ-M BTCUSDT 1m candles. Signals, entries, '
         'stops and costs as in [`cvd_divergence.md`](cvd_divergence.md) (intrabar delta, default indicator settings). '
         '**Train Oct 2020 – Dec 2023, test Jan 2024 – Sep 2026.** Profit factor and net return from closed trades, '
         '1×, full equity, compounded within each period.', '',
         f'{len(rows)} rule sets; {len(ok)} with ≥ {MIN_TRAIN_TRADES} train trades; profitable on train '
         f'{sum(r[5]["train"]["pf"] > 1 for r in ok)}, on test {sum(r[5]["test"]["pf"] > 1 for r in ok)}, '
         f'on both {len(both)}.', '',
         '## What each choice does', '',
         f'Median test profit factor over the rule sets with that value and ≥ {MIN_TRAIN_TRADES} train trades '
         '(share with test PF > 1 in brackets).', '']

    def effect(title, key, values, fmt=str):
        L.extend([f'| {title} | Rule sets | Median train PF | Median test PF |', '|---|---|---|---|'])
        for v in values:
            g = [r for r in ok if key(r) == v]
            if g:
                te = np.array([r[5]['test']['pf'] for r in g])
                L.append(f'| {fmt(v)} | {len(g)} | {np.median([r[5]["train"]["pf"] for r in g]):.2f} | '
                         f'{np.median(te):.2f} ({(te > 1).mean():.0%}) |')
        L.append('')

    effect('Chart', lambda r: r[0], [t[0] for t in TFS])
    effect('Side', lambda r: r[1], SIDES)
    effect('Trend filter', lambda r: r[2], TRENDS)
    effect('Exit', lambda r: r[3], EXITS, ex)
    effect('Min stop', lambda r: r[4], MIN_STOPS, lambda v: 'any' if v == 0 else f'≥ {v:.1%}')
    L += ['## Best 15 on train, and how they did on test', '',
          '| Chart | Side | Trend | Exit | Min stop | Train trades | Train PF | Train net | Test trades | Test PF | Test net | Test max DD |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for tf, sd, tr, rr, ms, s in sorted(ok, key=lambda r: -r[5]['train']['pf'])[:15]:
        a, b = s['train'], s['test']
        L.append(f'| {tf} | {sd} | {tr} | {ex(rr)} | {"any" if ms == 0 else f"{ms:.1%}"} | {a["n"]} | {a["pf"]:.2f} | '
                 f'{a["net"]:+.0%} | {b["n"]} | {b["pf"]:.2f} | {b["net"]:+.0%} | {b["mdd"]:.0%} |')
    L += ['', '## Intrabar timeframe used for delta (both sides, no filters)', '',
          '| Chart | Intrabars | Exit | Train trades | Train PF | Train net | Test trades | Test PF | Test net |',
          '|---|---|---|---|---|---|---|---|---|']
    for tf, ltf, rr, s in ltf_rows:
        L.append(f'| {tf} | {ltf}m | {ex(rr)} | {s["train"]["n"]} | {s["train"]["pf"]:.2f} | {s["train"]["net"]:+.0%} | '
                 f'{s["test"]["n"]} | {s["test"]["pf"]:.2f} | {s["test"]["net"]:+.0%} |')
    open(OUT, 'w').write('\n'.join(L) + '\n')
    print('wrote', OUT)


if __name__ == '__main__':
    main()
