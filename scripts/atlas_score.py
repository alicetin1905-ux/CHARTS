"""TradeBot's ATLAS score (src/atlasScore.js, 'classic' mode) on every candle of a series, in numba.

Same indicators and votes as analyse(), with the inputs the bot's own backtest has: price and volume only
(funding, open interest, long/short ratio, order book and taker tape are live-only), plus the daily pivot from the
last closed UTC day. Indicators run over the whole history instead of the bot's last 400 candles; check_score.js
compares the two.

    score(t, o, h, l, c, v, pivot) -> int array, -100..100 (NaN-free from ~bar 260 on)
"""
import numpy as np
from numba import njit

NaN = np.nan


@njit(cache=True)
def sma(v, p):
    n = len(v); o = np.full(n, NaN); s = 0.0
    for i in range(n):
        s += v[i]
        if i >= p: s -= v[i - p]
        if i >= p - 1: o[i] = s / p
    return o


@njit(cache=True)
def ema(v, p):
    # seeded with the SMA of the first p values; leading NaNs are skipped (as macd() slices them off)
    n = len(v); o = np.full(n, NaN); k = 2.0 / (p + 1); s = 0.0; cnt = 0
    for i in range(n):
        if np.isnan(v[i]) and cnt == 0: continue
        if cnt < p - 1: s += v[i]; cnt += 1; continue
        if cnt == p - 1: s += v[i]; o[i] = s / p; cnt += 1; continue
        o[i] = v[i] * k + o[i - 1] * (1 - k)
    return o


@njit(cache=True)
def rma(v, p):
    n = len(v); o = np.full(n, NaN); s = 0.0; cnt = 0; started = False
    for i in range(n):
        if np.isnan(v[i]): continue
        if not started:
            s += v[i]; cnt += 1
            if cnt == p: o[i] = s / p; started = True
            continue
        o[i] = (o[i - 1] * (p - 1) + v[i]) / p
    return o


@njit(cache=True)
def stdev(v, p):
    n = len(v); o = np.full(n, NaN); m = sma(v, p)
    for i in range(p - 1, n):
        s = 0.0
        for k in range(i - p + 1, i + 1): s += (v[k] - m[i]) ** 2
        o[i] = np.sqrt(s / p)
    return o


@njit(cache=True)
def highest(v, p):
    n = len(v); o = np.full(n, NaN)
    for i in range(p - 1, n):
        m = -np.inf
        for k in range(i - p + 1, i + 1): m = max(m, v[k])
        o[i] = m
    return o


@njit(cache=True)
def lowest(v, p):
    n = len(v); o = np.full(n, NaN)
    for i in range(p - 1, n):
        m = np.inf
        for k in range(i - p + 1, i + 1): m = min(m, v[k])
        o[i] = m
    return o


@njit(cache=True)
def linreg(v, p):
    n = len(v); o = np.full(n, NaN)
    for i in range(p - 1, n):
        sx = 0.0; sy = 0.0; sxy = 0.0; sxx = 0.0; ok = True
        for k in range(p):
            y = v[i - p + 1 + k]
            if np.isnan(y): ok = False; break
            sx += k; sy += y; sxy += k * y; sxx += k * k
        if not ok: continue
        d = p * sxx - sx * sx
        if d == 0: continue
        slope = (p * sxy - sx * sy) / d; icpt = (sy - slope * sx) / p
        o[i] = icpt + slope * (p - 1)
    return o


@njit(cache=True)
def true_range(h, l, c):
    n = len(c); o = np.empty(n)
    for i in range(n):
        o[i] = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return o


@njit(cache=True)
def atr(h, l, c, p):
    return rma(true_range(h, l, c), p)


@njit(cache=True)
def rsi(v, p):
    n = len(v); g = np.full(n, NaN); ls = np.full(n, NaN)
    for i in range(1, n):
        d = v[i] - v[i - 1]; g[i] = max(d, 0.0); ls[i] = max(-d, 0.0)
    ag = rma(g, p); al = rma(ls, p); o = np.full(n, NaN)
    for i in range(n):
        if np.isnan(ag[i]): continue
        o[i] = 100.0 if al[i] == 0 else 100 - 100 / (1 + ag[i] / al[i])
    return o


@njit(cache=True)
def supertrend_dir(h, l, c, p, mult):
    n = len(c); a = atr(h, l, c, p); d = np.full(n, NaN)
    fu = NaN; fl = NaN; prev = 1.0
    for i in range(n):
        if np.isnan(a[i]): continue
        basis = (h[i] + l[i]) / 2; up = basis + mult * a[i]; lo = basis - mult * a[i]
        fu = up if (np.isnan(fu) or up < fu or c[i - 1] > fu) else fu
        fl = lo if (np.isnan(fl) or lo > fl or c[i - 1] < fl) else fl
        x = prev
        if c[i] > fu: x = 1.0
        elif c[i] < fl: x = -1.0
        d[i] = x; prev = x
    return d


@njit(cache=True)
def chandelier_dir(h, l, c, ln, mult):
    n = len(c); a = atr(h, l, c, ln); hh = highest(c, ln); ll = lowest(c, ln); d = np.full(n, NaN)
    pl = NaN; ps = NaN; pd = 1.0
    for i in range(n):
        if np.isnan(a[i]) or np.isnan(hh[i]): continue
        lstop = hh[i] - a[i] * mult; sstop = ll[i] + a[i] * mult
        if not np.isnan(pl): lstop = max(lstop, pl) if c[i - 1] > pl else lstop
        if not np.isnan(ps): sstop = min(sstop, ps) if c[i - 1] < ps else sstop
        x = pd
        if not np.isnan(ps) and c[i] > ps: x = 1.0
        elif not np.isnan(pl) and c[i] < pl: x = -1.0
        d[i] = x; pl = lstop; ps = sstop; pd = x
    return d


@njit(cache=True)
def adx(h, l, c, p):
    n = len(c); pdm = np.zeros(n); ndm = np.zeros(n)
    for i in range(1, n):
        up = h[i] - h[i - 1]; dn = l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
    tr = rma(true_range(h, l, c), p); sp = rma(pdm, p); sn = rma(ndm, p)
    pdi = np.full(n, NaN); ndi = np.full(n, NaN); dx = np.full(n, NaN)
    for i in range(n):
        if np.isnan(tr[i]) or tr[i] == 0: continue
        pdi[i] = 100 * sp[i] / tr[i]; ndi[i] = 100 * sn[i] / tr[i]
        s = pdi[i] + ndi[i]
        dx[i] = 0.0 if s == 0 else 100 * abs(pdi[i] - ndi[i]) / s
    return rma(dx, p), pdi, ndi


@njit(cache=True)
def stoch_rsi(v, rlen, slen, k, d):
    r = rsi(v, rlen); n = len(v)
    r0 = np.where(np.isnan(r), 0.0, r)
    hi = highest(r0, slen); lo = lowest(r0, slen); raw = np.full(n, NaN)
    for i in range(n):
        if np.isnan(r[i]) or np.isnan(hi[i]): continue
        raw[i] = 50.0 if hi[i] == lo[i] else 100 * (r[i] - lo[i]) / (hi[i] - lo[i])
    first = -1
    for i in range(n):
        if not np.isnan(raw[i]): first = i; break
    kk = np.full(n, NaN); dd = np.full(n, NaN)
    if first < 0: return kk, dd
    kk[first:] = sma(raw[first:], k)
    ks = kk[first:].copy(); ks[np.isnan(ks)] = 0.0
    dd[first:] = sma(ks, d)
    return kk, dd


@njit(cache=True)
def cci(h, l, c, p):
    n = len(c); tp = (h + l + c) / 3; m = sma(tp, p); o = np.full(n, NaN)
    for i in range(p - 1, n):
        dev = 0.0
        for k in range(i - p + 1, i + 1): dev += abs(tp[k] - m[i])
        dev /= p
        o[i] = 0.0 if dev == 0 else (tp[i] - m[i]) / (0.015 * dev)
    return o


@njit(cache=True)
def mfi(h, l, c, v, p):
    n = len(c); tp = (h + l + c) / 3; pos = np.zeros(n); neg = np.zeros(n); o = np.full(n, NaN)
    for i in range(1, n):
        f = tp[i] * v[i]
        if tp[i] > tp[i - 1]: pos[i] = f
        elif tp[i] < tp[i - 1]: neg[i] = f
    for i in range(p, n):
        sp = 0.0; sn = 0.0
        for k in range(i - p + 1, i + 1): sp += pos[k]; sn += neg[k]
        o[i] = 100.0 if sn == 0 else 100 - 100 / (1 + sp / sn)
    return o


@njit(cache=True)
def vote(x, up, dn):
    # JS compares null as 0; only matters in a series' first candles
    if np.isnan(x): x = 0.0
    return 1 if x > up else (-1 if x < dn else 0)


@njit(cache=True)
def score(t, o, h, l, c, v, pivot):
    """pivot[i]: the previous closed UTC day's (H+L+C)/3 at candle i's close (NaN = no daily pivot vote)."""
    n = len(c)
    e20 = ema(c, 20); e50 = ema(c, 50); e200 = ema(c, 200)
    st = supertrend_dir(h, l, c, 10, 3.0)
    ce = chandelier_dir(h, l, c, 4, 2.0)
    l1 = linreg(c, 32); l2 = linreg(l1, 32); zl = l1 + (l1 - l2)
    ax, pdi, ndi = adx(h, l, c, 14)
    hi9 = highest(h, 9); lo9 = lowest(l, 9); hi26 = highest(h, 26); lo26 = lowest(l, 26)
    hi52 = highest(h, 52); lo52 = lowest(l, 52)
    span_a = ((hi9 + lo9) / 2 + (hi26 + lo26) / 2) / 2; span_b = (hi52 + lo52) / 2
    r14 = rsi(c, 14)
    ef = ema(c, 12); es = ema(c, 26); line = ef - es; sig = ema(line, 9)
    sk, sd = stoch_rsi(c, 14, 14, 3, 3)
    cc = cci(h, l, c, 20)
    hi14 = highest(h, 14); lo14 = lowest(l, 14)
    bm = sma(c, 20); bs = stdev(c, 20)
    mf = mfi(h, l, c, v, 14)
    # VWAP anchored at each UTC day; OBV; 5-bar fractal structure
    vwap = np.full(n, NaN); pv = 0.0; vv = 0.0; day = -1
    obv = np.zeros(n)
    out = np.zeros(n, dtype=np.int64)
    fh1 = NaN; fh2 = NaN; fl1 = NaN; fl2 = NaN  # last two fractal highs / lows (2 = newest)
    for i in range(n):
        d = (t[i] // 86400000)
        if d != day: pv = 0.0; vv = 0.0; day = d
        tp = (h[i] + l[i] + c[i]) / 3; pv += tp * v[i]; vv += v[i]
        vwap[i] = pv / vv if vv > 0 else NaN
        if i > 0: obv[i] = obv[i - 1] + (v[i] if c[i] > c[i - 1] else (-v[i] if c[i] < c[i - 1] else 0.0))
        j = i - 2
        if j >= 2:
            if h[j] > h[j - 1] and h[j] > h[j - 2] and h[j] > h[j + 1] and h[j] > h[j + 2]: fh1 = fh2; fh2 = h[j]
            if l[j] < l[j - 1] and l[j] < l[j - 2] and l[j] < l[j + 1] and l[j] < l[j + 2]: fl1 = fl2; fl2 = l[j]
        if i < 260: continue
        price = c[i]; tot = 0.0; ws = 0.0
        # trend
        stk = 1 if (e20[i] > e50[i] and e50[i] > e200[i]) else (-1 if (e20[i] < e50[i] and e50[i] < e200[i]) else 0)
        tot += 8 * stk; ws += 8
        tot += 6 * (1 if price > e200[i] else -1); ws += 6
        tot += 7 * st[i]; ws += 7
        tot += 7 * ce[i]; ws += 7
        zd = 1 if price > zl[i] else -1; zs = 1 if zl[i] > zl[i - 3] else -1
        tot += 5 * (zd if zd == zs else 0); ws += 5
        tot += 5 * (0 if ax[i] < 20 else (1 if pdi[i] > ndi[i] else -1)); ws += 5
        s = i - 26; ich = 0
        if not np.isnan(span_a[s]) and not np.isnan(span_b[s]):
            top = max(span_a[s], span_b[s]); bot = min(span_a[s], span_b[s])
            ich = 1 if price > top else (-1 if price < bot else 0)
        tot += 5 * ich; ws += 5
        # momentum
        tot += 6 * vote(r14[i], 55, 45); ws += 6
        md = 1 if line[i] > sig[i] else -1
        hd = 1 if (line[i] - sig[i]) > 0 else -1
        tot += 7 * (md if hd == md else 0); ws += 7
        k_ = sk[i]; d_ = sd[i]
        tot += 4 * (1 if (k_ > d_ and k_ < 80) else (-1 if (k_ < d_ and k_ > 20) else 0)); ws += 4
        tot += 3 * vote(cc[i], 100, -100); ws += 3
        wr = NaN if hi14[i] == lo14[i] else -100 * (hi14[i] - c[i]) / (hi14[i] - lo14[i])
        tot += 3 * (1 if (np.isnan(wr) or wr > -50) else -1); ws += 3
        roc = (c[i] / c[i - 10] - 1) * 100
        tot += 3 * vote(roc, 0.3, -0.3); ws += 3
        # structure
        up = bm[i] + 2 * bs[i]; dn = bm[i] - 2 * bs[i]
        pb = (price - dn) / (up - dn)
        tot += 3 * vote(pb, 0.55, 0.45); ws += 3
        sdir = 0
        if not np.isnan(fh1) and not np.isnan(fl1):
            hh = fh2 > fh1; hl = fl2 > fl1
            sdir = 1 if (hh and hl) else (-1 if (not hh and not hl) else 0)
        tot += 6 * sdir; ws += 6
        tot += 4 * (1 if price > vwap[i] else -1); ws += 4
        if not np.isnan(pivot[i]):
            tot += 3 * (1 if price > pivot[i] else -1); ws += 3
        # volume & flow
        ws += 3  # volume vs 20-avg: never votes, still counts in the weight sum
        tot += 4 * (1 if obv[i] > obv[i - 10] else -1); ws += 4
        tot += 3 * vote(mf[i], 55, 45); ws += 3
        out[i] = int(np.floor(tot / ws * 100 + 0.5))
    return out
