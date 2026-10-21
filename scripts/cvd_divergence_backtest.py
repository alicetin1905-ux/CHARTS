"""Backtest of the BUY / SELL signals of the Delta Volume indicator (indicators/delta_volume.pine) on BTCUSDT.P.

Binance USD-M BTCUSDT 1m candles, Oct 2020 - Sep 2026 (6 years). Needs numpy.

The signals follow the indicator's default settings exactly:
  - delta per bar from 1m intrabars (5m on the daily chart), each intrabar buy if it closed up, sell if down,
    a flat one takes the direction of the change from the previous intrabar close, else the last direction;
  - CVD = running sum of delta, no reset;
  - pivots on price: 5 bars left, 3 right; divergence with the previous pivot of the same kind 5-60 bars earlier
    (bullish: lower low in price, higher CVD; bearish: higher high, lower CVD; hidden divergences off);
  - a divergence arms a trigger (highest high / lowest low from the pivot to the bar it was found); BUY when a
    close is above it with positive delta within 10 bars, cancelled if the low breaks the pivot low first or a
    bearish divergence appears. SELL is the mirror image.
Delta is also computed two other ways, to see whether the estimate matters:
  - chart bar : buy share = (close - low) / (high - low) of the chart candle, what the indicator falls back to on
                bars older than TradingView's intrabar history (most of a 6-year chart on 1H and below);
  - taker     : Binance's real taker buy volume (buy - sell aggressor volume), not available in Pine.

Trading: enter at the open of the next bar, 1x, full equity, 0.075 % cost per side (taker fee + slippage).
Stop at the divergence pivot (the indicator's invalidation level), checked on 1m candles; an opposite signal
closes and reverses the trade, a repeat signal in the same direction is ignored. Exit variants:
  - reverse : no target, the trade runs until the stop or the opposite signal;
  - 2R      : take profit at 2x the stop distance.
If the stop and the target are hit in the same minute, the stop counts.

    python3 scripts/binance_1m.py is not needed: this script downloads its own 6-year file once (~3 minutes).
    python3 scripts/cvd_divergence_backtest.py            writes backtests/cvd_divergence.md
"""
import os, sys, time
from multiprocessing import Pool

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_1m  # noqa: E402

SRC = os.path.join('data', 'binance_1m', 'BTCUSDT_2020-10.npz')
MONTHS = [f'{y}-{m:02d}' for y in range(2020, 2027) for m in range(1, 13) if '2020-10' <= f'{y}-{m:02d}' <= '2026-09']
OUT = os.path.join('backtests', 'cvd_divergence.md')

COST = 0.00075
TFS = [('15m', 15, 1), ('1H', 60, 1), ('4H', 240, 1), ('1D', 1440, 5)]  # chart TF, minutes, intrabar minutes
PIV_L, PIV_R, RANGE_MIN, RANGE_MAX, CONFIRM = 5, 3, 5, 60, 10
DELTAS = ['intrabar', 'chart bar', 'taker']
EXITS = ['reverse', '2R']


def load():
    if not os.path.exists(SRC):
        os.makedirs(os.path.dirname(SRC), exist_ok=True)
        with Pool(8) as pool:
            parts = [p for p in pool.map(binance_1m.month, [('BTCUSDT', m) for m in MONTHS]) if p is not None]
        a = np.concatenate(parts)
        a = a[np.argsort(a[:, 0], kind='stable')]
        a = a[np.concatenate([[True], np.diff(a[:, 0]) > 0])]
        np.savez(SRC, t=a[:, 0].astype(np.int64), o=a[:, 1], h=a[:, 2], l=a[:, 3], c=a[:, 4], v=a[:, 5], tb=a[:, 6])
    return dict(np.load(SRC))


def groups(t, minutes):
    """Start index of each bar of `minutes` (UTC aligned) in the 1m arrays."""
    k = t // (minutes * 60_000)
    return np.flatnonzero(np.concatenate([[True], k[1:] != k[:-1]]))


def agg(m, starts):
    return dict(t=m['t'][starts], o=m['o'][starts], h=np.maximum.reduceat(m['h'], starts),
                l=np.minimum.reduceat(m['l'], starts), c=m['c'][np.r_[starts[1:], len(m['t'])] - 1],
                v=np.add.reduceat(m['v'], starts), tb=np.add.reduceat(m['tb'], starts))


def intrabar_dir(o, c):
    """Pine's rule: up / down candle, else the change from the previous intrabar close, else the last direction."""
    d = np.sign(c - o)
    flat = d == 0
    d[flat] = np.sign(c - np.r_[np.nan, c[:-1]])[flat]
    d = np.nan_to_num(d)
    idx = np.where(d != 0, np.arange(len(d)), -1)
    np.maximum.accumulate(idx, out=idx)
    return np.where(idx >= 0, d[np.maximum(idx, 0)], 1.0)  # Pine starts with lastDir = 1


def deltas(m, bars, starts, ib_minutes):
    if ib_minutes == 1:
        ib, ib_bar = m, np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(m['t'])]))
    else:
        s5 = groups(m['t'], ib_minutes)
        ib = agg(m, s5)
        ib_bar = np.searchsorted(bars['t'], ib['t'], side='right') - 1
    d_ib = np.bincount(ib_bar, intrabar_dir(ib['o'], ib['c']) * ib['v'], len(starts))
    o, h, l, c, v = bars['o'], bars['h'], bars['l'], bars['c'], bars['v']
    share = np.where(h > l, (c - l) / np.where(h > l, h - l, 1), np.where(c > o, 1.0, np.where(c < o, 0.0, 0.5)))
    return {'intrabar': d_ib, 'chart bar': v * (2 * share - 1), 'taker': 2 * bars['tb'] - v}


def pivots(x, low):
    """ta.pivotlow / ta.pivothigh: value at bar i strictly beyond the PIV_L bars before it, at least equal to the
    PIV_R bars after it. Returned on the confirmation bar i + PIV_R (NaN elsewhere)."""
    n = len(x)
    out = np.full(n, np.nan)
    w = sliding_window_view(x, PIV_L + PIV_R + 1)
    mid = w[:, PIV_L]
    if low:
        ok = (mid < w[:, :PIV_L].min(1)) & (mid <= w[:, PIV_L + 1:].min(1))
    else:
        ok = (mid > w[:, :PIV_L].max(1)) & (mid >= w[:, PIV_L + 1:].max(1))
    conf = np.arange(len(w)) + PIV_L + PIV_R
    out[conf[ok]] = mid[ok]
    return out


def signals(bars, delta):
    """BUY / SELL bars of the indicator: list of (bar index, side, stop)."""
    h, l, c = bars['h'], bars['l'], bars['c']
    cvd = np.cumsum(delta)
    pl, ph = pivots(l, True), pivots(h, False)
    n = len(c)
    prev_low = prev_high = None  # (bar, price, cvd)
    buy = sell = None             # (level, stop, expire bar)
    out, divs = [], 0
    for t in range(PIV_L + PIV_R, n):
        piv_bar = t - PIV_R
        bull = bear = False
        if not np.isnan(pl[t]):
            if prev_low and RANGE_MIN <= piv_bar - prev_low[0] <= RANGE_MAX:
                bull = pl[t] < prev_low[1] and cvd[piv_bar] > prev_low[2]
            prev_low = (piv_bar, pl[t], cvd[piv_bar])
        if not np.isnan(ph[t]):
            if prev_high and RANGE_MIN <= piv_bar - prev_high[0] <= RANGE_MAX:
                bear = ph[t] > prev_high[1] and cvd[piv_bar] < prev_high[2]
            prev_high = (piv_bar, ph[t], cvd[piv_bar])
        divs += bull + bear
        if buy:
            if l[t] < buy[1] or t > buy[2]:
                buy = None
            elif c[t] > buy[0] and delta[t] > 0:
                out.append((t, 1, buy[1]))
                buy = None
        if sell:
            if h[t] > sell[1] or t > sell[2]:
                sell = None
            elif c[t] < sell[0] and delta[t] < 0:
                out.append((t, -1, sell[1]))
                sell = None
        if bull:
            buy = (h[piv_bar:t + 1].max(), pl[t], t + CONFIRM)
            if not bear:
                sell = None
        if bear:
            sell = (l[piv_bar:t + 1].min(), ph[t], t + CONFIRM)
            if not bull:
                buy = None
    return out, divs


def first_exit(m, i0, i1, side, stop, tp):
    """First minute in [i0, i1) where the stop or target trades, and the fill price. None if neither."""
    lo, hi, op = m['l'], m['h'], m['o']
    for a in range(i0, i1, 50_000):
        b = min(a + 50_000, i1)
        sl_hit = lo[a:b] <= stop if side > 0 else hi[a:b] >= stop
        tp_hit = (hi[a:b] >= tp if side > 0 else lo[a:b] <= tp) if tp is not None else np.zeros(b - a, bool)
        any_hit = sl_hit | tp_hit
        if any_hit.any():
            j = a + int(any_hit.argmax())
            if sl_hit[j - a]:  # stop first when both trade in the same minute
                return j, (min(stop, op[j]) if side > 0 else max(stop, op[j])), 'stop'
            return j, (max(tp, op[j]) if side > 0 else min(tp, op[j])), 'target'
    return None


def trade(m, starts, sigs, exit_mode):
    """Run the signals. Returns trades as (entry minute, exit minute, side, entry, exit, reason)."""
    trades, pos = [], None  # pos: [side, entry minute, entry price, stop, target, scanned up to]

    def close(j, px, why):
        trades.append((pos[1], j, pos[0], pos[2], px, why))

    for t, side, stop in sigs:
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
            close(ei, m['o'][ei], 'reverse')
            pos = None
        e = m['o'][ei]
        if (e - stop) * side <= 0:
            continue  # opened beyond the stop
        tp = e + side * 2 * abs(e - stop) if exit_mode == '2R' else None
        pos = [side, ei, e, stop, tp, ei]
    if pos:
        hit = first_exit(m, pos[5], len(m['t']), pos[0], pos[3], pos[4])
        close(*(hit or (len(m['t']) - 1, m['c'][-1], 'end')))
    return trades


def stats(m, trades):
    gross = np.array([s * (x / e - 1) for _, _, s, e, x, _ in trades])
    r = gross - 2 * COST
    side = np.array([s for _, _, s, _, _, _ in trades])
    # Daily mark-to-market equity, for drawdown and calendar years.
    day_end = np.r_[groups(m['t'], 1440)[1:], len(m['t'])] - 1
    eq_day = np.ones(len(day_end))
    eq = 1.0
    last = 0
    for (i0, i1, s, e, x, _), rr in zip(trades, r):
        d0, d1 = np.searchsorted(day_end, i0), np.searchsorted(day_end, i1)
        eq_day[last:d0] = eq
        mtm = eq * (1 + s * (m['c'][day_end[d0:d1]] / e - 1) - COST)
        eq_day[d0:d1] = mtm
        eq *= 1 + rr
        last = d1
    eq_day[last:] = eq
    dd = 1 - eq_day / np.maximum.accumulate(eq_day)
    years = (m['t'][-1] - m['t'][0]) / (365.25 * 86_400_000)
    yr = np.array([int(time.strftime('%Y', time.gmtime(x / 1000))) for x in m['t'][day_end]])
    by_year = {}
    for y in np.unique(yr):
        idx = np.flatnonzero(yr == y)
        start = eq_day[idx[0] - 1] if idx[0] > 0 else 1.0
        by_year[int(y)] = eq_day[idx[-1]] / start - 1

    def pf(x):
        loss = -x[x < 0].sum()
        return x[x > 0].sum() / loss if loss > 0 else float('inf')

    return dict(n=len(r), per_year=len(r) / years, win=(r > 0).mean() if len(r) else 0, pf=pf(r), pf_gross=pf(gross),
                pf_long=pf(r[side > 0]), pf_short=pf(r[side < 0]), net=eq - 1, cagr=eq ** (1 / years) - 1,
                mdd=dd.max(), years=by_year, avg=r.mean() if len(r) else 0)


def main():
    t0 = time.time()
    m = load()
    span = f"{time.strftime('%Y-%m-%d', time.gmtime(m['t'][0] / 1000))} – {time.strftime('%Y-%m-%d', time.gmtime(m['t'][-1] / 1000))}"
    print(len(m['t']), 'candles', span)
    rows, yearly, counts = [], [], []
    for tf, minutes, ib in TFS:
        starts = groups(m['t'], minutes)
        bars = agg(m, starts)
        ds = deltas(m, bars, starts, ib)
        for dname in DELTAS:
            sigs, divs = signals(bars, ds[dname])
            counts.append((tf, dname, divs, sum(s > 0 for _, s, _ in sigs), sum(s < 0 for _, s, _ in sigs)))
            for ex in EXITS:
                st = stats(m, trade(m, starts, sigs, ex))
                rows.append((tf, dname, ex, st))
                print(f'{tf:>4} {dname:>9} {ex:>7}  trades {st["n"]:4d}  PF {st["pf"]:.2f}  net {st["net"]:+.0%}  '
                      f'mdd {st["mdd"]:.0%}  ({time.time() - t0:.0f}s)', flush=True)
    hold = m['c'][-1] / m['o'][0] - 1
    write(rows, counts, span, hold)


def pct(x):
    return f'{x:+.0%}' if abs(x) < 10 else f'{x:+,.0%}'


def write(rows, counts, span, hold):
    years = sorted(rows[0][3]['years'])
    L = ['# Delta Volume indicator: price / CVD divergence BUY / SELL signals, 6 years', '',
         f'Generated by `scripts/cvd_divergence_backtest.py`. Binance USDⓈ-M BTCUSDT 1m candles, **{span}**. '
         'Signals from [`indicators/delta_volume.pine`](../indicators/delta_volume.pine) at its default settings '
         '(pivots 5 / 3, 5–60 bars apart, 10 bars to confirm, confirmation candle needs matching delta, no hidden '
         'divergences, no CVD reset).', '',
         '- Entry at the next bar\'s open, 1×, full equity, 0.075 % cost per side. Stop at the divergence pivot, '
         'checked on 1m candles. An opposite signal closes and reverses; a repeat signal is ignored.',
         '- **reverse**: no target. **2R**: take profit at twice the stop distance.',
         '- Delta: **intrabar** = the indicator\'s 1m intrabar estimate (5m on 1D), as TradingView computes it live; '
         '**chart bar** = the fallback the indicator uses on bars older than TradingView\'s intrabar history; '
         '**taker** = Binance\'s real taker buy − sell volume (not available in Pine).',
         '- Max drawdown on daily closes, open trades marked to market. Returns compound.',
         f'- Buy & hold over the same period: **{pct(hold)}**.', '',
         '## Results', '',
         '| Chart | Delta | Exit | Trades / yr | Win % | PF before costs | Profit factor | Net return | CAGR | Max DD | PF long | PF short |',
         '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for tf, d, ex, s in rows:
        L.append(f'| {tf} | {d} | {ex} | {s["per_year"]:.0f} | {s["win"]:.0%} | {s["pf_gross"]:.2f} | {s["pf"]:.2f} | {pct(s["net"])} | '
                 f'{s["cagr"]:+.1%} | {s["mdd"]:.0%} | {s["pf_long"]:.2f} | {s["pf_short"]:.2f} |')
    L += ['', '## By calendar year (intrabar delta)', '',
          '| Chart | Exit | ' + ' | '.join(str(y) for y in years) + ' |', '|---|---|' + '---|' * len(years)]
    for tf, d, ex, s in rows:
        if d == 'intrabar':
            L.append(f'| {tf} | {ex} | ' + ' | '.join(pct(s['years'][y]) for y in years) + ' |')
    L += ['', f'{years[0]} starts in October, {years[-1]} ends in September.', '',
          '## Divergences and signals', '',
          '| Chart | Delta | Divergences | BUY signals | SELL signals |', '|---|---|---|---|---|']
    for tf, d, n, b, s in counts:
        L.append(f'| {tf} | {d} | {n} | {b} | {s} |')
    open(OUT, 'w').write('\n'.join(L) + '\n')
    print('wrote', OUT)


if __name__ == '__main__':
    main()
