"""Search for a BTCUSDT perpetual strategy with a high profit factor and at least 250 trades a year.

Strategy families (each long and short, signal on the bar close, fill at the next bar's open, one
position at a time, 0.075% cost per side, 1x, full equity per trade, stop before target when a candle
touches both):

  rsi      RSI(n) below lo -> long, above 100-lo -> short; exit when RSI crosses back past 50, on the
           stop, or after max bars. Optional EMA trend filter (only trade with the trend).
  bb       close outside the Bollinger band (n, k) -> fade it; exit at the middle band / stop / time.
  sweep    liquidity grab: the candle trades below the lowest low of the previous n candles but closes
           back above it -> long (mirrored for highs); stop just past the sweep wick, target R x risk.
  donch    close breaks the n-candle high/low -> follow it; ATR stop, R target or time exit.
  orb      opening range breakout: first close outside the range of the first candles after a session
           open (00, 08 or 13 UTC); stop at the other side or middle of the range, R target, out by the
           end of the session.
  trail    Liquidity Trail Signals [BOSWaves] flip, only in the direction of a slower EMA.
  hour     time of day: enter at a fixed UTC hour (long only, or with the EMA trend), hold N hours.
  vspike   volume spike (volume > k x average and a big candle): follow or fade it for N candles.
The trend filter `filt` is an EMA length on the tested timeframe (0 = none: both sides trade).

Bybit BTCUSDT perpetual candles from scripts/bybit_data.py (data/bybit_15m.json). Timeframes 15m, 1H, 4H, 12H, 1D.
Picked on the train window (Apr 2020 -> Oct 2024), checked on the test window (Oct 2024 -> Oct 2026).

    python3 scripts/bybit_data.py                       # once: builds data/bybit_15m.json
    python3 scripts/pf_search.py --tf 15m 1H 4H 12H 1D
"""
import argparse, json, math, os, sys, time
from itertools import product
from multiprocessing import Pool

import numpy as np

COST = 0.00075   # per side: Bybit taker 0.055% + slippage
COSTS = {'taker': COST, 'maker': 0.0002}  # maker: Bybit limit-order fee, no slippage (best case)
DAY = 86_400_000
MIN_PER_YEAR = 250


# ---------------------------------------------------------------- data

def load(data_dir, tf):
    """Bybit candles [t, o, h, l, c, v] at tf, built from data/bybit_15m.json (scripts/bybit_data.py)."""
    mins = {'15m': 15, '1H': 60, '4H': 240, '12H': 720, '1D': 1440}[tf]
    raw = np.array(json.load(open(os.path.join(data_dir, 'bybit_15m.json'))), dtype=float)
    if mins == 15:
        return raw
    ms, need = mins * 60_000, mins // 15
    k = raw[:, 0] - raw[:, 0] % ms
    out, s = [], 0
    for e in range(1, len(raw) + 1):
        if e == len(raw) or k[e] != k[s]:
            if e - s == need:
                g = raw[s:e]
                out.append([k[s], g[0, 1], g[:, 2].max(), g[:, 3].min(), g[-1, 4], g[:, 5].sum()])
            s = e
    return np.array(out)


# ---------------------------------------------------------------- indicators

def ema(x, n):
    a, out, v = 2 / (n + 1), np.empty(len(x)), x[0]
    for i, xi in enumerate(x):
        v += a * (xi - v)
        out[i] = v
    return out


def rma(x, n):
    out, v = np.empty(len(x)), x[:n].mean()
    out[:n] = np.nan
    for i in range(n, len(x)):
        v += (x[i] - v) / n
        out[i] = v
    return out


def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    return rma(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))), n)


def rsi(c, n):
    d = np.diff(c, prepend=c[0])
    up, dn = rma(np.maximum(d, 0), n), rma(np.maximum(-d, 0), n)
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(dn == 0, 100.0, 100 - 100 / (1 + up / dn))


def sma(x, n):  # windows that contain a nan give nan (instead of poisoning every later value)
    bad = np.cumsum(np.r_[0, np.isnan(x)])
    cs = np.cumsum(np.r_[0, np.nan_to_num(x)])
    out = np.full(len(x), np.nan)
    out[n - 1:] = np.where(bad[n:] - bad[:-n] > 0, np.nan, (cs[n:] - cs[:-n]) / n)
    return out


def stdev(x, n):
    m = sma(x, n)
    m2 = sma(x * x, n)
    return np.sqrt(np.maximum(m2 - m * m, 0))


def roll_min_prev(x, n):  # min of the previous n values (excluding the current one)
    out = np.full(len(x), np.nan)
    w = np.lib.stride_tricks.sliding_window_view(x, n).min(axis=1)
    out[n:] = w[:-1]
    return out


def roll_max_prev(x, n):
    out = np.full(len(x), np.nan)
    w = np.lib.stride_tricks.sliding_window_view(x, n).max(axis=1)
    out[n:] = w[:-1]
    return out


# ---------------------------------------------------------------- simulator

def simulate(o, h, l, c, sig, stop, tgt, ex_long, ex_short, max_bars, cost=0.0):
    """sig[i] = +1/-1 on the close of bar i. stop[i]/tgt[i] = stop and target PRICES for a trade
    signalled on bar i (nan = none). ex_long/ex_short[i] = exit on the close of bar i (filled at the
    next open). An opposite signal also exits, and so does max_bars (a number or one per signal bar). Returns (entry bar, exit bar, direction, return)."""
    N = len(c)
    idx = np.flatnonzero(sig)
    trades, nxt, p = [], 0, 0
    while True:
        while p < len(idx) and idx[p] < nxt:
            p += 1
        if p >= len(idx):
            break
        i = idx[p]
        if i + 1 >= N:
            break
        d, j = int(sig[i]), i + 1
        px, sl, tp = o[j], stop[i], tgt[i]
        mb = max_bars[i] if isinstance(max_bars, np.ndarray) else max_bars
        if not math.isnan(sl) and d * (px - sl) <= 0:   # gapped through the stop: skip
            nxt = i + 1
            continue
        ex = exb = None
        for k in range(j, N):
            if not math.isnan(sl) and ((l[k] <= sl) if d == 1 else (h[k] >= sl)):
                ex, exb = (min(sl, o[k]) if d == 1 else max(sl, o[k])), k
                break
            if not math.isnan(tp) and ((h[k] >= tp) if d == 1 else (l[k] <= tp)):
                ex, exb = (max(tp, o[k]) if d == 1 else min(tp, o[k])), k
                break
            if k + 1 >= N:
                ex, exb = c[k], k
                break
            if (ex_long[k] if d == 1 else ex_short[k]) or sig[k] == -d or k - j + 1 >= mb:
                ex, exb = o[k + 1], k + 1
                break
        trades.append((j, exb, d, d * (ex / px - 1) - 2 * cost))
        nxt = k  # a signal on the exit bar's close may enter at the next open
    return trades


def stats(t, trades, s, e, cost=COST):
    """Trades from simulate() with cost=0; the round-trip cost is taken off here."""
    r = np.array([x[3] for x in trades if s <= x[0] < e]) - 2 * cost
    years = (t[e - 1] - t[s]) / DAY / 365
    if len(r) == 0:
        return dict(n=0, per_year=0, pf=0, win=0, ret=0, dd=0, avg=0)
    eq = np.cumprod(1 + r)
    dd = 1 - eq / np.maximum.accumulate(np.r_[1, eq])[1:]
    wins, loss = r[r > 0].sum(), -r[r <= 0].sum()
    return dict(n=len(r), per_year=len(r) / years, pf=wins / loss if loss else 99.0, win=100 * (r > 0).mean(),
                ret=100 * (eq[-1] - 1), dd=100 * dd.max(), avg=100 * r.mean())


# ---------------------------------------------------------------- strategies

def strat_rsi(D, n, lo, filt, sl_atr, max_bars):
    c, A = D['c'], D['atr']
    R = D[f'rsi{n}']
    trend = np.zeros(len(c)) if filt == 0 else np.sign(c - D[f'ema{filt}'])
    sig = np.where((R < lo) & (trend >= 0), 1, np.where((R > 100 - lo) & (trend <= 0), -1, 0))
    stop = np.where(sig == 1, c - sl_atr * A, c + sl_atr * A)
    return sig, stop, np.full(len(c), np.nan), R > 50, R < 50, max_bars


def strat_bb(D, n, k, filt, sl_atr, max_bars):
    c, A = D['c'], D['atr']
    m, sd = D[f'sma{n}'], D[f'sd{n}']
    trend = np.zeros(len(c)) if filt == 0 else np.sign(c - D[f'ema{filt}'])
    sig = np.where((c < m - k * sd) & (trend >= 0), 1, np.where((c > m + k * sd) & (trend <= 0), -1, 0))
    stop = np.where(sig == 1, c - sl_atr * A, c + sl_atr * A)
    return sig, stop, np.full(len(c), np.nan), c > m, c < m, max_bars


def strat_sweep(D, n, filt, rr, buf, max_bars):
    o, h, l, c, A = D['o'], D['h'], D['l'], D['c'], D['atr']
    lo, hi = D[f'minl{n}'], D[f'maxh{n}']
    trend = np.zeros(len(c)) if filt == 0 else np.sign(c - D[f'ema{filt}'])
    bull = (l < lo) & (c > lo) & (trend >= 0)
    bear = (h > hi) & (c < hi) & (trend <= 0)
    sig = np.where(bull & ~bear, 1, np.where(bear & ~bull, -1, 0))
    stop = np.where(sig == 1, l - buf * A, h + buf * A)
    risk = np.abs(c - stop)
    tgt = np.where(sig == 1, c + rr * risk, c - rr * risk)
    f = np.zeros(len(c), bool)
    return sig, stop, tgt, f, f, max_bars


def strat_donch(D, n, filt, sl_atr, rr, max_bars):
    c, A = D['c'], D['atr']
    lo, hi = D[f'minl{n}'], D[f'maxh{n}']
    trend = np.zeros(len(c)) if filt == 0 else np.sign(c - D[f'ema{filt}'])
    sig = np.where((c > hi) & (trend >= 0), 1, np.where((c < lo) & (trend <= 0), -1, 0))
    stop = np.where(sig == 1, c - sl_atr * A, c + sl_atr * A)
    tgt = np.where(sig == 1, c + rr * sl_atr * A, c - rr * sl_atr * A)
    f = np.zeros(len(c), bool)
    return sig, stop, tgt, f, f, max_bars


def trail_engine(c, ma, at, mult):
    N = len(c)
    trend = np.zeros(N)
    trail = np.zeros(N)
    tr_, tn, prev = None, 1, 0.0
    for i in range(N):
        if math.isnan(at[i]):
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


def strat_trail(D, ma_len, mult, filt, rr, max_bars):
    c = D['c']
    key = ('trail', ma_len, mult)
    if key not in D:
        D[key] = trail_engine(c, D[f'ema{ma_len}'], D['atr15'], mult)
    trend, trail = D[key]
    flip = np.r_[0, np.diff(trend)] != 0
    tf = np.zeros(len(c)) if filt == 0 else np.sign(c - D[f'ema{filt}'])
    sig = np.where(flip & (trend > 0) & (tf >= 0), 1, np.where(flip & (trend < 0) & (tf <= 0), -1, 0))
    risk = np.abs(c - trail)
    tgt = np.where(rr > 0, np.where(sig == 1, c + rr * risk, c - rr * risk), np.nan)
    return sig, trail, tgt, trend < 0, trend > 0, max_bars


def strat_orb(D, hour, rbars, stop_mode, rr, filt):
    """Opening range breakout: the range is the first rbars candles after `hour` UTC; the first close
    outside it that day is the signal. Stop at the other side of the range (or its middle); target
    rr x risk; anything still open exits at the end of that 24h session."""
    t, h, l, c = D['t'], D['h'], D['l'], D['c']
    key = ('orb', hour, rbars)
    if key not in D:
        day = ((t - hour * 3_600_000) // DAY).astype(np.int64)
        start = np.r_[True, day[1:] != day[:-1]]
        pos = np.zeros(len(t), np.int64)
        rh, rl = np.full(len(t), np.nan), np.full(len(t), np.nan)
        first = np.zeros(len(t), bool)
        p = 0
        cur_h = cur_l = np.nan
        done = False
        for i in range(len(t)):
            if start[i]:
                p, cur_h, cur_l, done = 0, -np.inf, np.inf, False
            if p < rbars:
                cur_h, cur_l = max(cur_h, h[i]), min(cur_l, l[i])
            elif not done:
                rh[i], rl[i] = cur_h, cur_l
                if c[i] > cur_h or c[i] < cur_l:
                    first[i], done = True, True
            pos[i] = p
            p += 1
        bpd = int(round(DAY / np.median(np.diff(t))))
        D[key] = (rh, rl, first, np.maximum(1, bpd - pos - 1))
    rh, rl, first, left = D[key]
    trend = np.zeros(len(c)) if filt == 0 else np.sign(c - D[f'ema{filt}'])
    sig = np.where(first & (c > rh) & (trend >= 0), 1, np.where(first & (c < rl) & (trend <= 0), -1, 0))
    mid = (rh + rl) / 2
    stop = np.where(sig == 1, rl if stop_mode == 'range' else mid, rh if stop_mode == 'range' else mid)
    risk = np.abs(c - stop)
    tgt = np.where(rr > 0, np.where(sig == 1, c + rr * risk, c - rr * risk), np.nan)
    f = np.zeros(len(c), bool)
    return sig, stop, tgt, f, f, left


def strat_hour(D, hour, hold_h, side, filt, sl_atr):
    """Time of day: enter on the close of the candle that ends at `hour` UTC, long only or with the
    trend (EMA filt), hold hold_h hours, ATR stop."""
    t, c, A = D['t'], D['c'], D['atr']
    step = D['tfmin'] * 60_000
    at_hour = ((t + step) % DAY) == hour * 3_600_000
    trend = np.ones(len(c)) if filt == 0 else np.sign(c - D[f'ema{filt}'])
    d = np.ones(len(c)) if side == 'long' else trend
    sig = np.where(at_hour, d, 0).astype(int)
    stop = np.where(sig == 1, c - sl_atr * A, c + sl_atr * A)
    f = np.zeros(len(c), bool)
    return sig, stop, np.full(len(c), np.nan), f, f, max(1, hold_h * 60 // D['tfmin'])


def strat_vspike(D, vk, rk, mode, hold, sl_atr):
    """Volume spike: volume > vk x its 20-candle average and range > rk x ATR. Follow or fade the
    candle's direction; hold `hold` candles, ATR stop."""
    o, h, l, c, v, A = D['o'], D['h'], D['l'], D['c'], D['v'], D['atr']
    spike = (v > vk * D['vsma20']) & (h - l > rk * A)
    d = np.sign(c - o) * (1 if mode == 'follow' else -1)
    sig = np.where(spike, d, 0).astype(int)
    stop = np.where(sig == 1, c - sl_atr * A, c + sl_atr * A)
    f = np.zeros(len(c), bool)
    return sig, stop, np.full(len(c), np.nan), f, f, hold


# ---------------------------------------------------------------- community / built-in TradingView strategies
# Ports of popular open-source TradingView scripts and TradingView's built-in strategies. Each gives raw
# buy/sell signals and trades them like the original: in the market all the time, reversing on the
# opposite signal. Options: `filt` = only enter in the direction of EMA(filt) (the position still exits on
# the opposite signal; 0 = no filter, both sides), `sl_atr` = ATR stop (0 = none), `side` = 'both' or 'long'
# (long only: the sell signal just closes the long).

def cross_up(a, b):
    return (a > b) & (np.r_[np.nan, a[:-1]] <= np.r_[np.nan, b[:-1]])


def wma(x, n):
    w = np.arange(1, n + 1, dtype=float)
    out = np.full(len(x), np.nan)
    out[n - 1:] = np.convolve(x, w[::-1], 'valid') / w.sum()
    return out


def highest(x, n):
    out = np.full(len(x), np.nan)
    out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).max(axis=1)
    return out


def lowest(x, n):
    out = np.full(len(x), np.nan)
    out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).min(axis=1)
    return out


def linreg_end(x, n):  # ta.linreg(x, n, 0)
    out = np.full(len(x), np.nan)
    w = np.lib.stride_tricks.sliding_window_view(np.nan_to_num(x), n)
    xs = np.arange(n, dtype=float)
    xm = xs.mean()
    b = ((w - w.mean(axis=1, keepdims=True)) * (xs - xm)).sum(axis=1) / ((xs - xm) ** 2).sum()
    out[n - 1:] = w.mean(axis=1) + b * (n - 1 - xm)
    return out


def sig_utbot(D, a, c, ha):
    """UT Bot Strategy (QuantNomad): ATR trailing stop, buy when the source crosses above it."""
    src = (D['o'] + D['h'] + D['l'] + D['c']) / 4 if ha else D['c']
    nl = a * atr(D['h'], D['l'], D['c'], c)
    st = np.zeros(len(src))
    for i in range(1, len(src)):
        p = st[i - 1]
        if math.isnan(nl[i]):
            st[i] = src[i]
        elif src[i] > p and src[i - 1] > p:
            st[i] = max(p, src[i] - nl[i])
        elif src[i] < p and src[i - 1] < p:
            st[i] = min(p, src[i] + nl[i])
        else:
            st[i] = src[i] - nl[i] if src[i] > p else src[i] + nl[i]
    return cross_up(src, st), cross_up(st, src)


def sig_rangefilter(D, per, mult):
    """Range Filter Buy and Sell (guikroth / DonovanWall)."""
    x = D['c']
    smrng = ema(ema(np.abs(np.diff(x, prepend=x[0])), per), per * 2 - 1) * mult
    filt = np.zeros(len(x))
    filt[0] = x[0]
    for i in range(1, len(x)):
        p, r = filt[i - 1], smrng[i]
        filt[i] = (p if x[i] - r < p else x[i] - r) if x[i] > p else (p if x[i] + r > p else x[i] + r)
    up = np.zeros(len(x))
    dn = np.zeros(len(x))
    for i in range(1, len(x)):
        up[i] = up[i - 1] + 1 if filt[i] > filt[i - 1] else 0 if filt[i] < filt[i - 1] else up[i - 1]
        dn[i] = dn[i - 1] + 1 if filt[i] < filt[i - 1] else 0 if filt[i] > filt[i - 1] else dn[i - 1]
    lc, sc = (x > filt) & (up > 0), (x < filt) & (dn > 0)
    ini = np.zeros(len(x))
    for i in range(1, len(x)):
        ini[i] = 1 if lc[i] else -1 if sc[i] else ini[i - 1]
    prev = np.r_[0, ini[:-1]]
    return lc & (prev == -1), sc & (prev == 1)


def sig_chandelier(D, length, mult):
    """Chandelier Exit (everget), close-based extremes."""
    c = D['c']
    a = mult * atr(D['h'], D['l'], c, length)
    ls0, ss0 = highest(c, length) - a, lowest(c, length) + a
    ls, ss = ls0.copy(), ss0.copy()
    d = np.ones(len(c))
    for i in range(1, len(c)):
        lp = ls[i - 1] if not math.isnan(ls[i - 1]) else ls0[i]
        sp = ss[i - 1] if not math.isnan(ss[i - 1]) else ss0[i]
        ls[i] = max(ls0[i], lp) if c[i - 1] > lp else ls0[i]
        ss[i] = min(ss0[i], sp) if c[i - 1] < sp else ss0[i]
        d[i] = 1 if c[i] > sp else -1 if c[i] < lp else d[i - 1]
    pd_ = np.r_[1, d[:-1]]
    return (d == 1) & (pd_ == -1), (d == -1) & (pd_ == 1)


def sig_supertrend(D, atr_len, factor):
    """ta.supertrend / TradingView's built-in Supertrend Strategy."""
    h, l, c = D['h'], D['l'], D['c']
    a = atr(h, l, c, atr_len)
    hl2 = (h + l) / 2
    up0, dn0 = hl2 - factor * a, hl2 + factor * a
    lo, hi = up0.copy(), dn0.copy()
    d = np.ones(len(c))  # 1 = down, -1 = up (Pine convention)
    st = np.full(len(c), np.nan)
    for i in range(1, len(c)):
        if math.isnan(a[i]):
            continue
        pl = lo[i - 1] if not math.isnan(lo[i - 1]) else 0.0
        pu = hi[i - 1] if not math.isnan(hi[i - 1]) else 0.0
        lo[i] = up0[i] if (up0[i] > pl or c[i - 1] < pl) else pl
        hi[i] = dn0[i] if (dn0[i] < pu or c[i - 1] > pu) else pu
        if math.isnan(a[i - 1]):
            d[i] = 1
        elif st[i - 1] == pu:
            d[i] = -1 if c[i] > hi[i] else 1
        else:
            d[i] = 1 if c[i] < lo[i] else -1
        st[i] = lo[i] if d[i] == -1 else hi[i]
    pd_ = np.r_[1, d[:-1]]
    return (d == -1) & (pd_ == 1), (d == 1) & (pd_ == -1)


def sig_ssl(D, n):
    """SSL Channel (ErwinBeckers)."""
    c = D['c']
    sh, sl = sma(D['h'], n), sma(D['l'], n)
    hlv = np.zeros(len(c))
    for i in range(1, len(c)):
        hlv[i] = 1 if c[i] > sh[i] else -1 if c[i] < sl[i] else hlv[i - 1]
    p = np.r_[0, hlv[:-1]]
    return (hlv == 1) & (p == -1), (hlv == -1) & (p == 1)


def sig_squeeze(D, n, kc):
    """Squeeze Momentum (LazyBear): trade the squeeze release in the momentum's direction."""
    h, l, c = D['h'], D['l'], D['c']
    basis, dev = sma(c, n), 2.0 * stdev(c, n)
    pc = np.r_[c[0], c[:-1]]
    rng = sma(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))), n)
    on = (basis - dev > basis - kc * rng) & (basis + dev < basis + kc * rng)
    val = linreg_end(c - ((highest(h, n) + lowest(l, n)) / 2 + sma(c, n)) / 2, n)
    rel = np.r_[False, on[:-1]] & ~on
    return rel & (val > 0), rel & (val < 0)


def sig_macd(D, fast, slow, sig):
    """TradingView built-in MACD Strategy: MACD - signal crossing zero."""
    m = ema(D['c'], fast) - ema(D['c'], slow)
    dl = m - ema(m, sig)
    z = np.zeros(len(dl))
    return cross_up(dl, z), cross_up(z, dl)


def sig_rsi_tv(D, n, os_):
    """TradingView built-in RSI Strategy: RSI crosses up through oversold -> long, down through
    overbought -> short."""
    r = rsi(D['c'], n)
    lo, hi = np.full(len(r), float(os_)), np.full(len(r), 100.0 - os_)
    return cross_up(r, lo), cross_up(hi, r)


def sig_bb_tv(D, n, k):
    """TradingView built-in Bollinger Bands Strategy (close-based)."""
    c = D['c']
    m, sd = sma(c, n), stdev(c, n)
    return cross_up(c, m - k * sd), cross_up(m + k * sd, c)


def sig_keltner_tv(D, n, k):
    """TradingView built-in Keltner Channels Strategy (close-based breakout)."""
    c = D['c']
    m = ema(c, n)
    r = ema(D['h'] - D['l'], n)
    return cross_up(c, m + k * r), cross_up(m - k * r, c)


def sig_psar(D, start, mx):
    """TradingView built-in Parabolic SAR Strategy (flip of ta.sar)."""
    h, l = D['h'], D['l']
    N = len(h)
    up, sar, ep, af = True, l[0], h[0], start
    d = np.ones(N)
    for i in range(1, N):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < sar:
                up, sar, ep, af = False, ep, l[i], start
            elif h[i] > ep:
                ep, af = h[i], min(mx, af + start)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], start
            elif l[i] < ep:
                ep, af = l[i], min(mx, af + start)
        d[i] = 1 if up else -1
    pd_ = np.r_[1, d[:-1]]
    return (d == 1) & (pd_ == -1), (d == -1) & (pd_ == 1)


def sig_hull(D, n):
    """Hull Suite: HMA rising vs falling (HMA vs HMA two bars ago)."""
    c = D['c']
    hma = wma(2 * wma(c, n // 2) - wma(c, n), int(round(math.sqrt(n))))
    d = np.sign(hma - np.r_[np.nan, np.nan, hma[:-2]])
    pd_ = np.r_[0, d[:-1]]
    return (d == 1) & (pd_ == -1), (d == -1) & (pd_ == 1)


def sig_macross(D, fast, slow):
    """TradingView built-in MovingAvg2Line Cross."""
    f, s_ = sma(D['c'], fast), sma(D['c'], slow)
    return cross_up(f, s_), cross_up(s_, f)


def sig_stoch_tv(D, n, ob):
    """TradingView built-in Stochastic Slow Strategy."""
    h, l, c = D['h'], D['l'], D['c']
    hh, ll = highest(h, n), lowest(l, n)
    with np.errstate(divide='ignore', invalid='ignore'):
        k = sma(np.nan_to_num(100 * (c - ll) / (hh - ll), nan=50.0), 3)
    d = sma(k, 3)
    return cross_up(k, d) & (k < 100 - ob), cross_up(d, k) & (k > ob)


COMMUNITY = {
    'utbot':       (sig_utbot,       dict(a=(1, 2, 3), c=(10, 20), ha=(False, True))),
    'rangefilter': (sig_rangefilter, dict(per=(50, 100, 200), mult=(2.0, 3.0, 4.0))),
    'chandelier':  (sig_chandelier,  dict(length=(14, 22, 44), mult=(2.0, 3.0, 4.0))),
    'supertrend':  (sig_supertrend,  dict(atr_len=(10, 14, 20), factor=(2.0, 3.0, 4.0))),
    'ssl':         (sig_ssl,         dict(n=(10, 20, 50))),
    'squeeze':     (sig_squeeze,     dict(n=(20, 30), kc=(1.5, 2.0))),
    'macd_tv':     (sig_macd,        dict(fast=(12,), slow=(26,), sig=(9,))),
    'rsi_tv':      (sig_rsi_tv,      dict(n=(7, 14), os_=(20, 30))),
    'bb_tv':       (sig_bb_tv,       dict(n=(20, 50), k=(2.0, 2.5))),
    'keltner_tv':  (sig_keltner_tv,  dict(n=(20, 50), k=(1.0, 2.0))),
    'psar_tv':     (sig_psar,        dict(start=(0.01, 0.02), mx=(0.1, 0.2))),
    'hull':        (sig_hull,        dict(n=(21, 55, 100))),
    'macross_tv':  (sig_macross,     dict(fast=(9, 20), slow=(18, 50))),
    'stoch_tv':    (sig_stoch_tv,    dict(n=(14,), ob=(80, 70))),
}


def make_comm(name):
    fn = COMMUNITY[name][0]

    def strat(D, filt, sl_atr, side='both', **p):
        key = ('comm', name, tuple(sorted(p.items())))
        if key not in D:
            D[key] = fn(D, **p)
        buy, sell = D[key]
        c, A = D['c'], D['atr']
        trend = np.zeros(len(c)) if filt == 0 else np.sign(c - D[f'ema{filt}'])
        sig = np.where(buy & (trend >= 0), 1, np.where(sell & (trend <= 0) & (side == 'both'), -1, 0))
        stop = np.where(sl_atr > 0, np.where(sig == 1, c - sl_atr * A, c + sl_atr * A), np.nan)
        return sig, stop, np.full(len(c), np.nan), sell, buy, 10 ** 9
    return strat


GRIDS = {
    'rsi':   (strat_rsi,   dict(n=(2, 3, 5, 7, 14), lo=(5, 10, 15, 20, 25, 30), filt=(0, 50, 200, 800),
                                sl_atr=(1.5, 2.5, 4.0), max_bars=(6, 12, 24, 48))),
    'bb':    (strat_bb,    dict(n=(10, 20, 30, 50), k=(1.5, 2.0, 2.5, 3.0), filt=(0, 50, 200, 800),
                                sl_atr=(1.5, 2.5, 4.0), max_bars=(6, 12, 24, 48))),
    'sweep': (strat_sweep, dict(n=(5, 10, 20, 40), filt=(0, 50, 200, 800), rr=(0.75, 1.0, 1.5, 2.0, 3.0),
                                buf=(0.0, 0.1, 0.25), max_bars=(6, 12, 24, 48))),
    'donch': (strat_donch, dict(n=(10, 20, 40, 80), filt=(0, 50, 200, 800), sl_atr=(1.0, 1.5, 2.5),
                                rr=(1.0, 1.5, 2.0, 3.0), max_bars=(12, 24, 48, 96))),
    'orb':   (strat_orb,   dict(hour=(0, 8, 13), rbars=(1, 2, 4, 8), stop_mode=('range', 'mid'),
                                rr=(0, 1.0, 1.5, 2.0, 3.0), filt=(0, 200, 800))),
    'hour':  (strat_hour,  dict(hour=tuple(range(24)), hold_h=(1, 2, 4, 8, 12), side=('long', 'trend'),
                                filt=(50, 200, 800), sl_atr=(2.0, 4.0))),
    'vspike': (strat_vspike, dict(vk=(2.0, 3.0, 5.0), rk=(1.0, 1.5, 2.5), mode=('follow', 'fade'),
                                  hold=(1, 2, 4, 8, 16), sl_atr=(1.5, 3.0))),
    'trail': (strat_trail, dict(ma_len=(10, 14, 20, 28, 50), mult=(0.5, 0.75, 1.0, 1.25, 1.5, 2.0),
                                filt=(0, 50, 200, 800), rr=(0, 1.0, 2.0, 3.0), max_bars=(24, 96, 10 ** 6))),
}


for _n, (_f, _g) in COMMUNITY.items():
    GRIDS[_n] = (make_comm(_n), dict(_g, filt=(0, 200), sl_atr=(0, 3.0)))


def prepare(candles):
    t, o, h, l, c, v = candles.T
    D = dict(t=t, o=o, h=h, l=l, c=c, v=v, atr=atr(h, l, c, 14), atr15=atr(h, l, c, 15), vsma20=sma(v, 20),
             tfmin=int(round(np.median(np.diff(t)) / 60_000)))
    for n in (2, 3, 5, 7, 14):
        D[f'rsi{n}'] = rsi(c, n)
    for n in (10, 14, 20, 28, 50, 200, 800):
        D[f'ema{n}'] = ema(c, n)
    for n in (10, 20, 30, 50):
        D[f'sma{n}'], D[f'sd{n}'] = sma(c, n), stdev(c, n)
    for n in (5, 10, 20, 40, 80):
        D[f'minl{n}'], D[f'maxh{n}'] = roll_min_prev(l, n), roll_max_prev(h, n)
    return D


G = {}


def init(D, W):
    G['D'], G['W'] = D, W


def run(job):
    name, params = job
    D, W = G['D'], G['W']
    fn = GRIDS[name][0]
    sig, stop, tgt, exl, exs, mb = fn(D, **params)
    sig = sig.copy()
    sig[:W['train'][0]] = 0
    tr = simulate(D['o'], D['h'], D['l'], D['c'], sig, stop, tgt, exl, exs, mb)
    return name, params, {f'{w}_{ck}': stats(D['t'], tr, s, e, cv) for w, (s, e) in W.items()
                          for ck, cv in COSTS.items()}


def jobs():
    for name, (_, grid) in GRIDS.items():
        keys = list(grid)
        for vals in product(*grid.values()):
            yield name, dict(zip(keys, vals))


def neighbour_pf(res, window):
    """Median PF of each setting and its one-step neighbours (same strategy, one parameter moved
    one grid step), so a lone spike scores lower than a plateau."""
    by = {(n, tuple(sorted(p.items()))): r for n, p, r in res}
    out = {}
    for n, p, r in res:
        grid, vals = GRIDS[n][1], [r[window]['pf']]
        for k, v in p.items():
            g = grid[k]
            i = g.index(v)
            for j in (i - 1, i + 1):
                if 0 <= j < len(g):
                    q = dict(p, **{k: g[j]})
                    x = by.get((n, tuple(sorted(q.items()))))
                    if x:
                        vals.append(x[window]['pf'])
        out[(n, tuple(sorted(p.items())))] = float(np.median(vals))
    return out


def fmt(r):
    return f"PF {r['pf']:.2f}, {r['per_year']:.0f}/yr, {r['ret']:+.0f}%, DD {r['dd']:.0f}%"


def report(out):
    print('# Profit-factor search, BTCUSDT perpetual (Bybit)\n')
    print('Generated by `scripts/pf_search.py`. Signal on the close, fill at the next open, one position at a time, '
          '1x, full equity per trade. **taker** = 0.075% per side (Bybit taker 0.055% + slippage); '
          '**maker** = 0.02% per side (limit orders, assumes they fill at the same price: best case).\n')
    for tf, R in out.items():
        res = R['res']
        w = R['windows']
        print(f"## {tf}  (train {w['train'][0]} to {w['train'][1]}, test {w['test'][0]} to {w['test'][1]})\n")
        floor = MIN_PER_YEAR
        ok = [x for x in res if x[2]['train_taker']['per_year'] >= floor and x[2]['test_taker']['per_year'] >= floor]
        if not ok:
            best_f = max(x[2]['train_taker']['per_year'] for x in res)
            floor = 0.3 * best_f if tf in ('12H', '1D') else floor
            ok = [x for x in res if x[2]['train_taker']['per_year'] >= floor and x[2]['test_taker']['per_year'] >= floor]
            print(f'No setting reaches {MIN_PER_YEAR} trades/year on {tf} (max {best_f:.0f}); '
                  f'shown with at least {floor:.0f}/year instead.\n')
        print(f'{len(ok)} of {len(res)} runs trade at least {floor:.0f} times a year in both windows.\n')
        nb = neighbour_pf(res, 'train_taker')
        key = lambda x: (x[0], tuple(sorted(x[1].items())))
        head = ('| Strategy | Settings | Train (taker) | Test (taker) | Test (maker) |\n|---|---|---|---|---|')
        print('Best per strategy, picked on train PF (taker) of the setting and its neighbours:\n\n' + head)
        for fam in GRIDS:
            f = [x for x in ok if x[0] == fam]
            if not f:
                continue
            n, p, r = max(f, key=lambda x: nb[key(x)])
            ps = ', '.join(f'{k}={v}' for k, v in p.items())
            print(f"| {n} | {ps} | {fmt(r['train_taker'])} | {fmt(r['test_taker'])} | {fmt(r['test_maker'])} |")
        print('\nTop 5 by the weaker of train and test PF (taker); uses the test window, so a robustness view:\n\n' + head)
        for n, p, r in sorted(ok, key=lambda x: -min(x[2]['train_taker']['pf'], x[2]['test_taker']['pf']))[:5]:
            ps = ', '.join(f'{k}={v}' for k, v in p.items())
            print(f"| {n} | {ps} | {fmt(r['train_taker'])} | {fmt(r['test_taker'])} | {fmt(r['test_maker'])} |")
        print('\nSame with maker costs (limit orders):\n\n| Strategy | Settings | Train (maker) | Test (maker) |\n|---|---|---|---|')
        for n, p, r in sorted(ok, key=lambda x: -min(x[2]['train_maker']['pf'], x[2]['test_maker']['pf']))[:5]:
            ps = ', '.join(f'{k}={v}' for k, v in p.items())
            print(f"| {n} | {ps} | {fmt(r['train_maker'])} | {fmt(r['test_maker'])} |")
        print()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data')
    ap.add_argument('--tf', nargs='*', default=['15m', '1H', '4H', '12H', '1D'])
    ap.add_argument('--test-years', type=float, default=2)
    ap.add_argument('--report-only', action='store_true')
    a = ap.parse_args()
    import pickle
    cache = os.path.join(a.data, 'pf_results.pkl')
    if a.report_only:
        report(pickle.load(open(cache, 'rb')))
        sys.exit()
    out = {}
    for tf in a.tf:
        candles = load(a.data, tf)
        t = candles[:, 0]
        split = int(np.searchsorted(t, t[-1] - a.test_years * 365 * DAY))
        W = {'train': (300, split), 'test': (split, len(t))}
        D = prepare(candles)
        t0 = time.time()
        J = list(jobs())
        with Pool(os.cpu_count(), initializer=init, initargs=(D, W)) as pool:
            res = pool.map(run, J, chunksize=8)
        out[tf] = dict(res=res, windows={w: [time.strftime('%Y-%m-%d', time.gmtime(t[s] / 1000)),
                                              time.strftime('%Y-%m-%d', time.gmtime(t[e - 1] / 1000))]
                                         for w, (s, e) in W.items()})
        print(f'{tf}: {len(J)} runs in {time.time() - t0:.0f}s', file=sys.stderr, flush=True)
        pickle.dump(out, open(cache, 'wb'))
    report(out)
