"""Backtest of the Trend Signals + Overlays indicator (pine/trend_signals_overlays.pine) on BTCUSDT perpetual.

The signal logic is ported from the Pine Script, including TradingView's own definitions of ta.rma, ta.ema,
ta.atr, ta.rsi, ta.dmi and ta.supertrend, so the trades follow the labels the chart shows. No dependencies.

Signals on the bar close, fills at the next bar's open, 0.075% cost per side, 1x, full equity per trade.
Drawdown is marked to market on every close.

Rules (confirmation signals, sensitivity 12, unless noted):
  flip        always in the market: Buy goes long, Sell goes short
  strong      only Buy+/Sell+ open a trade; any opposite signal closes it (and reverses if it is strong)
  exits       every signal opens a trade; the indicator's blue/orange x exit closes it, or the opposite signal
  tpsl        every signal opens a trade; take profit at TP2, stop at SL (TP/SL distance 1.5 x ATR 14),
              or the opposite signal. If TP and SL fall inside the same candle, the stop counts first
  rated 3-4   flip, but only signals the classifier rates 3 or 4 (the others are hidden)
  autopilot   flip, with the autopilot sensitivity (best of 10-20 over the last 250 bars)
  contrarian  contrarian signals; their exit closes the trade, the opposite signal reverses it

Candles: OKX BTC-USDT-SWAP, shared with scripts/ribbon_backtest.py (5m history builds 5m/15m/30m, 1H history
builds 1H to 1D).

    python3 scripts/tso_backtest.py                 # downloads into ./data the first time
    python3 scripts/tso_backtest.py --data /path    # reuse btc_5m.json / btc_1H.json
"""
import argparse, math, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ribbon_backtest import COST, DAY, TFS, load, resample  # noqa: E402

WARMUP = 300


# ───────── TradingView built-ins (None = na) ─────────
def gt(a, b):
    return a is not None and b is not None and a > b


def lt(a, b):
    return a is not None and b is not None and a < b


def change(x):
    return [None] + [None if a is None or b is None else a - b for a, b in zip(x[1:], x[:-1])]


def _smooth(src, n, alpha):
    """ta.rma / ta.ema: seeded with the SMA of the first n values, then exponential."""
    out, prev, win = [], None, []
    for x in src:
        win.append(x)
        if len(win) > n:
            win.pop(0)
        if prev is None:
            prev = sum(win) / n if len(win) == n and None not in win else None
        else:
            prev = None if x is None else alpha * x + (1 - alpha) * prev
        out.append(prev)
    return out


def rma(src, n):
    return _smooth(src, n, 1 / n)


def ema(src, n):
    return _smooth(src, n, 2 / (n + 1))


def true_range(h, l, c, handle_na=True):
    out = [h[0] - l[0] if handle_na else None]
    for i in range(1, len(c)):
        out.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return out


def atr(h, l, c, n):
    return rma(true_range(h, l, c), n)


def rsi(src, n):
    ch = change(src)
    up = rma([None if v is None else max(v, 0.0) for v in ch], n)
    dn = rma([None if v is None else -min(v, 0.0) for v in ch], n)
    out = []
    for u, d in zip(up, dn):
        if u is None or d is None:
            out.append(None)
        else:
            out.append(100.0 if d == 0 else 0.0 if u == 0 else 100 - 100 / (1 + u / d))
    return out


def adx(h, l, c, di_len=14, adx_len=14):
    up, down = change(h), [None if v is None else -v for v in change(l)]
    plus_dm = [None if u is None else (u if u > d and u > 0 else 0.0) for u, d in zip(up, down)]
    minus_dm = [None if d is None else (d if d > u and d > 0 else 0.0) for u, d in zip(up, down)]
    trur = rma(true_range(h, l, c, handle_na=False), di_len)
    p_s, m_s = rma(plus_dm, di_len), rma(minus_dm, di_len)
    ratio, lp, lm = [], None, None
    for ps, ms, tr in zip(p_s, m_s, trur):
        p = None if ps is None or tr is None else 100 * ps / tr
        m = None if ms is None or tr is None else 100 * ms / tr
        lp = p if p is not None else lp  # fixnan
        lm = m if m is not None else lm
        if lp is None or lm is None:
            ratio.append(None)
        else:
            s = lp + lm
            ratio.append(abs(lp - lm) / (s if s != 0 else 1))
    return [None if v is None else 100 * v for v in rma(ratio, adx_len)]


def supertrend_dir(h, l, c, atr10, factor):
    """ta.supertrend direction (-1 up, 1 down). `factor` may change bar to bar, as with autopilot."""
    n = len(c)
    fac = factor if isinstance(factor, list) else [factor] * n
    dirs, st = [1] * n, [None] * n
    pl = pu = None
    for i in range(n):
        a = atr10[i]
        src = (h[i] + l[i]) / 2
        lo = None if a is None else src - fac[i] * a
        up = None if a is None else src + fac[i] * a
        ppl = 0.0 if pl is None else pl
        ppu = 0.0 if pu is None else pu
        c1 = c[i - 1] if i else None
        lo = lo if gt(lo, ppl) or lt(c1, ppl) else ppl
        up = up if lt(up, ppu) or gt(c1, ppu) else ppu
        if i == 0 or atr10[i - 1] is None:
            d = 1
        elif st[i - 1] is not None and st[i - 1] == ppu:
            d = -1 if gt(c[i], up) else 1
        else:
            d = 1 if lt(c[i], lo) else -1
        dirs[i], st[i] = d, (lo if d == -1 else up)
        pl, pu = lo, up
    return dirs


def trail(src, dist):
    """f_trail from the Pine script. Returns directions (1 up / -1 down)."""
    t, d, out = None, 1, []
    for s, k in zip(src, dist):
        up = None if s is None or k is None else s - k
        dn = None if s is None or k is None else s + k
        if t is None:
            t = up
        elif d == 1:
            t = None if up is None else max(t, up)
            if lt(s, t):
                d, t = -1, dn
        else:
            t = None if dn is None else min(t, dn)
            if gt(s, t):
                d, t = 1, up
        out.append(d)
    return out


def xover(a, lvl, i):
    return gt(a[i], lvl) and a[i - 1] is not None and a[i - 1] <= lvl


def xunder(a, lvl, i):
    return lt(a[i], lvl) and a[i - 1] is not None and a[i - 1] >= lvl


def percentile(vals, p):
    v = sorted(vals)
    k = (len(v) - 1) * p / 100
    f = math.floor(k)
    return v[f] + (v[min(f + 1, len(v) - 1)] - v[f]) * (k - f)


def classify(hist, x):
    """f_rate: 1-4 by quartile of x among the last 200 signals (needs 8). Warm-up na values are skipped."""
    if x is None:
        return None
    r = None
    if len(hist) >= 8:
        q1, q2, q3 = (percentile(hist, p) for p in (25, 50, 75))
        r = 1 if x < q1 else 2 if x < q2 else 3 if x < q3 else 4
    hist.append(x)
    if len(hist) > 200:
        hist.pop(0)
    return r


def autopilot_score(c, dirs):
    """f_score: log return of the long/short flip strategy over the last 250 bars."""
    cum, out = [0.0] * len(c), []
    for i in range(len(c)):
        if i:
            cum[i] = cum[i - 1] + (1 if dirs[i - 1] < 0 else -1) * math.log(c[i] / c[i - 1])
        out.append(cum[i] - (cum[i - 250] if i >= 250 else 0.0))
    return out


# ───────── Indicator events ─────────
def events(candles, sens=12, autopilot=False, mode='conf', allowed=(1, 2, 3, 4)):
    _, _, h, l, c = (list(x) for x in zip(*candles))
    n = len(c)
    a10 = atr(h, l, c, 10)
    if autopilot:
        sc = [autopilot_score(c, supertrend_dir(h, l, c, a10, k / 4)) for k in range(10, 21)]
        opt = [10 + max(range(11), key=lambda j: sc[j][i]) for i in range(n)]
        st = supertrend_dir(h, l, c, a10, [s / 4 for s in opt])
    else:
        opt, st = None, supertrend_dir(h, l, c, a10, sens / 4)
    tr = trail(ema(c, sens), [None if a is None else 2 * a for a in atr(h, l, c, 50)])
    r14, osc, ax = rsi(c, 14), rsi(c, sens), adx(h, l, c)

    side, strong, rate, bx, sx = [0] * n, [False] * n, [None] * n, [False] * n, [False] * n
    hist, cpos = [], 0
    for i in range(1, n):
        bull_flip = st[i] < 0 < st[i - 1]
        bear_flip = st[i] > 0 > st[i - 1]
        if mode == 'conf':
            bx[i] = st[i] < 0 and xunder(r14, 70, i) and not bull_flip
            sx[i] = st[i] > 0 and xover(r14, 30, i) and not bear_flip
            trig = 1 if bull_flip else -1 if bear_flip else 0
        else:
            if cpos == 1 and xover(osc, 70, i):
                bx[i], cpos = True, 0
            if cpos == -1 and xunder(osc, 30, i):
                sx[i], cpos = True, 0
            trig = 1 if xover(osc, 30, i) else -1 if xunder(osc, 70, i) else 0
        if not trig:
            continue
        r = rate[i] = classify(hist, ax[i])
        if r is not None and r not in allowed:
            continue
        side[i] = trig
        if mode == 'conf':
            strong[i] = tr[i] == trig
        else:
            w = [v for v in osc[max(0, i - 4):i + 1] if v is not None]
            strong[i] = bool(w) and (min(w) < 20 if trig == 1 else max(w) > 80)
            cpos = trig
    return dict(side=side, strong=strong, rate=rate, bx=bx, sx=sx, st=st, tr=tr, opt=opt, atr14=atr(h, l, c, 14))


# ───────── Trading simulation ─────────
def simulate(candles, ev, start_ms=0, rule='flip', cost=COST, tp_mult=2, unit=1.5):
    t, o, h, l, c = zip(*candles)
    n = len(c)
    first = max(WARMUP, next((i for i in range(n) if t[i] >= start_ms), n))
    side, strong, bx, sx, a14 = ev['side'], ev['strong'], ev['bx'], ev['sx'], ev['atr14']
    pos, entry, tp, sl = 0, 0.0, None, None
    eq = peak = 1.0
    mdd, trades, bars_in = 0.0, [], 0
    year_end, year_trades = {}, {}

    def close(px, i):
        nonlocal eq, pos
        r = pos * (px / entry - 1) - 2 * cost
        eq *= 1 + r
        trades.append(r)
        y = time.gmtime(t[i] / 1000).tm_year
        year_trades[y] = year_trades.get(y, 0) + 1
        pos = 0

    for i in range(first, n - 1):
        if pos:
            bars_in += 1
            if rule == 'tpsl':
                if pos == 1:
                    px = (o[i] if o[i] <= sl else sl if l[i] <= sl else
                          o[i] if o[i] >= tp else tp if h[i] >= tp else None)
                else:
                    px = (o[i] if o[i] >= sl else sl if h[i] >= sl else
                          o[i] if o[i] <= tp else tp if l[i] <= tp else None)
                if px is not None:
                    close(px, i)
        mtm = eq * (1 + pos * (c[i] / entry - 1)) if pos else eq
        peak = max(peak, mtm)
        mdd = max(mdd, 1 - mtm / peak)
        year_end[time.gmtime(t[i] / 1000).tm_year] = mtm

        s = side[i]
        if s and s != pos:
            if pos:
                close(o[i + 1], i + 1)
            if rule != 'strong' or strong[i]:
                pos, entry = s, o[i + 1]
                if rule == 'tpsl':
                    d = unit * a14[i]
                    tp, sl = c[i] + s * tp_mult * d, c[i] - s * d
        elif pos and rule in ('exits', 'contra') and (bx[i] if pos == 1 else sx[i]):
            close(o[i + 1], i + 1)
    if pos:
        close(c[-1], n - 1)
    year_end[time.gmtime(t[-1] / 1000).tm_year] = eq

    wins = [r for r in trades if r > 0]
    loss = -sum(r for r in trades if r <= 0)
    years = (t[-1] - t[first]) / DAY / 365
    yr, prev = {}, 1.0
    for y in sorted(year_end):
        yr[y] = (year_end[y] / prev - 1) * 100
        prev = year_end[y]
    return {
        'from': time.strftime('%Y-%m-%d', time.gmtime(t[first] / 1000)),
        'return': (eq - 1) * 100,
        'buy_hold': (c[-1] / o[first + 1] - 1) * 100,
        'trades': len(trades),
        'per_month': len(trades) / (years * 12) if years else 0,
        'win_rate': 100 * len(wins) / max(1, len(trades)),
        'profit_factor': sum(wins) / loss if loss else float('inf'),
        'max_dd': mdd * 100,
        'time_in_market': 100 * bars_in / max(1, n - 1 - first),
        'years': yr,
        'year_trades': year_trades,
    }


VARIANTS = {
    'flip':       (dict(), 'flip'),
    'strong':     (dict(), 'strong'),
    'exits':      (dict(), 'exits'),
    'tpsl':       (dict(), 'tpsl'),
    'rated 3-4':  (dict(allowed=(3, 4)), 'flip'),
    'autopilot':  (dict(autopilot=True), 'flip'),
    'contrarian': (dict(mode='contra'), 'contra'),
}
SWEEP = (6, 9, 12, 16, 20, 30)
HIGHER = ('1H', '2H', '4H', '6H', '12H', '1D')
HEAD = ('| TF | Rule | Return | Buy & hold | Trades | Trades/mo | Win rate | Profit factor | Max DD | In market |\n'
        '|---|---|---|---|---|---|---|---|---|---|')


def row(tf, name, r):
    return (f"| {tf} | {name} | {r['return']:+.1f}% | {r['buy_hold']:+.1f}% | {r['trades']} | {r['per_month']:.1f} "
            f"| {r['win_rate']:.0f}% | {r['profit_factor']:.2f} | {r['max_dd']:.1f}% | {r['time_in_market']:.0f}% |")


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data', default='data')
    p.add_argument('--years', type=float, default=2, help='common test window for every timeframe')
    a = p.parse_args()
    base = {'5m': load(a.data, '5m', 211_000), '1H': load(a.data, '1H', 60_000)}
    end = min(base['5m'][-1][0], base['1H'][-1][0])
    start = end - int(a.years * 365 * DAY)
    series = {tf: resample(base[b], 5 if b == '5m' else 60, m) for tf, (b, m) in TFS.items()}
    cache = {}

    def ev(tf, **kw):
        key = (tf, tuple(sorted(kw.items())))
        if key not in cache:
            cache[key] = events(series[tf], **kw)
        return cache[key]

    def run(tf, name, start_ms=start, **extra):
        kw, rule = VARIANTS[name]
        return simulate(series[tf], ev(tf, **kw), start_ms, rule, **extra)

    span = time.strftime('%Y-%m-%d', time.gmtime(start / 1000)), time.strftime('%Y-%m-%d', time.gmtime(end / 1000))
    print(f'## Same window for every timeframe: last {a.years:g} years ({span[0]} to {span[1]})\n\n' + HEAD)
    for tf in series:
        for name in VARIANTS:
            print(row(tf, name, run(tf, name)), flush=True)

    print(f'\n## Sensitivity (flip rule, last {a.years:g} years): return after costs\n')
    print('| TF | ' + ' | '.join(f'sens {s}' for s in SWEEP) + ' |\n|---|' + '---|' * len(SWEEP))
    for tf in series:
        cells = [simulate(series[tf], ev(tf, sens=s), start, 'flip')['return'] for s in SWEEP]
        print(f'| {tf} | ' + ' | '.join(f'{x:+.0f}%' for x in cells) + ' |', flush=True)

    print(f'\n## Before vs after costs (last {a.years:g} years)\n\n| TF | flip before | flip after | tpsl before | tpsl after |\n|---|---|---|---|---|')
    for tf in series:
        g = [run(tf, r, cost=cst)['return'] for r in ('flip', 'tpsl') for cst in (0, COST)]
        print(f'| {tf} | {g[0]:+.1f}% | {g[1]:+.1f}% | {g[2]:+.1f}% | {g[3]:+.1f}% |', flush=True)

    since = time.strftime('%Y-%m', time.gmtime((base['1H'][0][0] + WARMUP * 3_600_000) / 1000))
    print(f'\n## Full history, 1H and up (from about {since})\n\n' + HEAD)
    full = {}
    for tf in HIGHER:
        for name in VARIANTS:
            full[tf, name] = run(tf, name, start_ms=0)
            print(row(tf, name, full[tf, name]), flush=True)

    yrs = sorted({y for r in full.values() for y in r['years']})
    print('\n## Full history: per calendar year (return, trades closed)\n')
    print('| TF | Rule | ' + ' | '.join(map(str, yrs)) + ' | Losing years |\n|---|---|' + '---|' * (len(yrs) + 1))
    bh, prev = {}, series['1H'][WARMUP][1]
    for tt, _, _, _, cc in series['1H'][WARMUP:]:
        bh[time.gmtime(tt / 1000).tm_year] = cc
    bh_ret = {}
    for y in sorted(bh):
        bh_ret[y], prev = (bh[y] / prev - 1) * 100, bh[y]
    print('| BTC | buy & hold | ' + ' | '.join(f'{bh_ret[y]:+.0f}%' if y in bh_ret else '' for y in yrs)
          + f' | {sum(1 for y in yrs if bh_ret.get(y, 0) < 0)} |')
    for tf in HIGHER:
        for name in ('flip', 'strong', 'tpsl', 'autopilot'):
            r = full[tf, name]
            cells = [f"{r['years'][y]:+.0f}% ({r['year_trades'].get(y, 0)})" if y in r['years'] else '' for y in yrs]
            losing = sum(1 for y in yrs if r['years'].get(y, 0) < 0)
            print(f'| {tf} | {name} | ' + ' | '.join(cells) + f' | {losing} |', flush=True)
