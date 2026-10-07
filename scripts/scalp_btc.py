"""BTCUSDT.P multi-timeframe LONG / SHORT score scalper: full grid backtest.

Grid (all combinations, 269,568 configs x 3 ZLSMA exit modes):
    entry TF 3m/5m/15m · setup TF 15m/30m/1H · HTF filter 1H/4H (entry < setup < HTF)
    min score 60/65/70/75/80/85, the same for LONG and SHORT or separate (36 pairs)
    volume multiplier 1.1/1.25/1.5/2 · ATR expansion 1.02/1.05/1.10
    SL 0.5/0.7/1% · TP1 1/1.5% · TP2 2/2.5/3% · TP3 3/4/5% (TP1 < TP2 < TP3)

Flow per closed entry candle (mirrored for SHORT):
    HTF filter   last closed HTF candle: close > EMA200 and EMA50 > EMA200
    setup        last closed setup candle: Chandelier Exit (22, 3) long and close > ZLSMA(32)
    entry        the entry candle closes green and the LONG score >= the LONG min score
    LONG score 0-100 (weights; the order-book part has no history and is left out, the rest scaled to 100):
        30m trend bullish: close > EMA50 and EMA20 > EMA50                                         10
        1H trend bullish (same)                                 10
        resistance breakout: close > highest high of the 20 candles before                        15
        volume expansion: volume >= multiplier x average of the 20 candles before                10
        ATR expansion: ATR(14) >= expansion x its 20-candle average                               10
        buy-side order-book imbalance                                                (no data)   10
        aggressive market buys: taker-buy volume >= 55% of the candle's volume                    10
        momentum bullish: MACD(12,26,9) line > signal and RSI(14) > 50                            15
        liquidity sweep / reclaim: a low in the last 5 candles broke the 20-candle low before
            them and the close is back above it                                                   10
    trade        market order at the entry candle's close (0.055% taker + 0.02% slippage)
    exits        SL; TP1 / TP2 / TP3 close 40 / 35 / 25% of the ORIGINAL size (each only out of what is left);
                 after TP1 the stop moves to entry + 0.2%, after TP2 to TP1 (break-even / lock);
                 ZLSMA exit: an entry-TF candle closes below ZLSMA(32) -> the rest closes at market
                 (always / only after TP1 / off: a 4th exit dimension, so 808,704 configs);
                 stops pay taker + slippage (at the open if price gaps through), targets 0.02% maker on a touch.
                 Replayed on 1m candles: stop, then targets, then the ZLSMA check at candle close.
    One position at a time. P&L in R (1R = the SL distance = 1% of the account at 1% risk, no compounding).
    Train Jan 2023 - Dec 2024, test Jan 2025 - Sep 2026 (Binance USD-M 1m candles, scripts/binance_1m.py).

    python3 scripts/scalp_btc.py      -> backtests/scalp_btc.md, backtests/scalp_btc_all.csv.gz, backtests/scalp_btc.html
"""
import csv, gzip, json, os, sys, time

import numpy as np
from numba import njit, prange

sys.path.insert(0, os.path.dirname(__file__))
from atlas_score import ema, atr, sma, highest, lowest, linreg, rsi  # noqa: E402

SYM = 'BTCUSDT'
DATA = os.path.join('data', 'binance_1m', f'{SYM}.npz')
OUT_MD = os.path.join('backtests', 'scalp_btc.md')
OUT_CSV = os.path.join('backtests', 'scalp_btc_all.csv.gz')
OUT_HTML = os.path.join('backtests', 'scalp_btc.html')
MIN = 60000
T_START = 1672531200000  # 2023-01-01
T_SPLIT = 1735689600000  # 2025-01-01
TRAIN_Y, TEST_Y = 2.0, 1.75

ENTRY_TFS = [3, 5, 15]
SETUP_TFS = [15, 30, 60]
HTFS = [60, 240]
SCORES = [60, 65, 70, 75, 80, 85]
VOLS = [1.1, 1.25, 1.5, 2.0]
ATRX = [1.02, 1.05, 1.10]
ZMODES = ['always', 'after TP1', 'off']  # when the ZLSMA exit is active
EXITS = [(sl, t1, t2, t3, z) for z in range(3) for sl in (0.5, 0.7, 1.0) for t1 in (1.0, 1.5) for t2 in (2.0, 2.5, 3.0)
         for t3 in (3.0, 4.0, 5.0) if t1 < t2 < t3]
TF_COMBOS = [(e, s, h) for e in ENTRY_TFS for s in SETUP_TFS for h in HTFS if e < s < h]
W = dict(t30=10, t60=10, brk=15, vol=10, atr=10, taker=10, mom=15, sweep=10)  # order book (10) has no history
W_SUM = sum(W.values())
TAKER, MAKER, SLIP, BE_BUFFER = 0.00055, 0.0002, 0.0002, 0.002
SPLIT = (0.40, 0.35, 0.25)
REASONS = ['SL', 'BE / lock stop', 'ZLSMA', 'TP3', 'end of data']


@njit(cache=True)
def chandelier(h, l, c, ln, mult):
    """Chandelier Exit (everget, close-based extremes) direction."""
    n = len(c); a = atr(h, l, c, ln); hh = highest(c, ln); ll = lowest(c, ln); d = np.zeros(n)
    pl = np.nan; ps = np.nan; pd = 1.0
    for i in range(n):
        if np.isnan(a[i]) or np.isnan(hh[i]): continue
        ls = hh[i] - a[i] * mult; ss = ll[i] + a[i] * mult
        if not np.isnan(pl) and c[i - 1] > pl: ls = max(ls, pl)
        if not np.isnan(ps) and c[i - 1] < ps: ss = min(ss, ps)
        x = pd
        if not np.isnan(ps) and c[i] > ps: x = 1.0
        elif not np.isnan(pl) and c[i] < pl: x = -1.0
        d[i] = x; pl = ls; ps = ss; pd = x
    return d


def bars(m, tf):
    t = m['t']
    st = np.r_[0, np.nonzero(np.diff(t // (tf * MIN)))[0] + 1]
    en = np.r_[st[1:] - 1, len(t) - 1]
    return dict(t=(t[st] // (tf * MIN)) * tf * MIN, o=m['o'][st], h=np.maximum.reduceat(m['h'], st),
                l=np.minimum.reduceat(m['l'], st), c=m['c'][en], v=np.add.reduceat(m['v'], st),
                tb=np.add.reduceat(m['tb'], st), last=en)


def zlsma(c):
    l1 = linreg(c, 32); l2 = linreg(l1, 32)
    return l1 + (l1 - l2)


def trend(b):
    """+1 / -1 / 0 per candle: close > EMA50 and EMA20 > EMA50 (or both below)."""
    e20, e50 = ema(b['c'], 20), ema(b['c'], 50)
    return np.where((b['c'] > e50) & (e20 > e50), 1, np.where((b['c'] < e50) & (e20 < e50), -1, 0))


def at_close(tf_close_t, vals, when):
    """Value of the last tf candle closed at or before `when`."""
    j = np.searchsorted(tf_close_t, when, side='right') - 1
    return np.where(j >= 0, vals[np.maximum(j, 0)], 0)


def prepare(m):
    B = {tf: bars(m, tf) for tf in sorted(set(ENTRY_TFS + SETUP_TFS + HTFS + [30, 60]))}
    ct = {tf: B[tf]['t'] + tf * MIN for tf in B}
    tr = {tf: trend(B[tf]) for tf in (30, 60)}
    htf = {}
    for tf in HTFS:
        c = B[tf]['c']; e50, e200 = ema(c, 50), ema(c, 200)
        htf[tf] = np.where((c > e200) & (e50 > e200), 1, np.where((c < e200) & (e50 < e200), -1, 0))
    setup = {}
    for tf in SETUP_TFS:
        b = B[tf]; ce = chandelier(b['h'], b['l'], b['c'], 22, 3.0); zl = zlsma(b['c'])
        setup[tf] = np.where((ce == 1) & (b['c'] > zl), 1, np.where((ce == -1) & (b['c'] < zl), -1, 0))
    E = {}
    nmin = len(m['t'])
    for etf in ENTRY_TFS:
        b = B[etf]; c, h, l, v = b['c'], b['h'], b['l'], b['v']
        when = ct[etf]
        side = np.sign(c - b['o']).astype(np.int64)
        vr = b['v'] / np.r_[np.nan, sma(v, 20)[:-1]]
        a = atr(h, l, c, 14); ar = a / sma(np.nan_to_num(a), 20)
        hh = np.r_[np.nan, highest(h, 20)[:-1]]; ll = np.r_[np.nan, lowest(l, 20)[:-1]]
        line = ema(c, 12) - ema(c, 26); sig = ema(line, 9); r = rsi(c, 14)
        mom = np.where((line > sig) & (r > 50), 1, np.where((line < sig) & (r < 50), -1, 0))
        tbr = np.where(v > 0, b['tb'] / np.where(v > 0, v, 1), 0.5)
        ref_lo = np.r_[np.full(5, np.nan), ll[:-5]]; ref_hi = np.r_[np.full(5, np.nan), hh[:-5]]   # 20-candle range before the last 5
        lo5 = np.r_[np.full(4, np.nan), lowest(l, 5)[4:]]; hi5 = np.r_[np.full(4, np.nan), highest(h, 5)[4:]]
        sweep_l = (lo5 < ref_lo) & (c > ref_lo); sweep_s = (hi5 > ref_hi) & (c < ref_hi)
        t30 = at_close(ct[30], tr[30], when); t60 = at_close(ct[60], tr[60], when)
        base = np.zeros(len(c))
        L = side == 1
        base += W['t30'] * np.where(L, t30 == 1, t30 == -1)
        base += W['t60'] * np.where(L, t60 == 1, t60 == -1)
        base += W['brk'] * np.where(L, c > hh, c < ll)
        base += W['taker'] * np.where(L, tbr >= 0.55, tbr <= 0.45)
        base += W['mom'] * np.where(L, mom == 1, mom == -1)
        base += W['sweep'] * np.where(L, sweep_l, sweep_s)
        G = np.zeros((len(c), 5), dtype=np.int64)  # setup 15/30/60, HTF 1H/4H, as +1/-1/0
        for j, tf in enumerate(SETUP_TFS):
            G[:, j] = at_close(ct[tf], setup[tf], when)
        for j, tf in enumerate(HTFS):
            G[:, 3 + j] = at_close(ct[tf], htf[tf], when)
        best = (base + W['vol'] * (vr >= min(VOLS)) + W['atr'] * (ar >= min(ATRX))) / W_SUM * 100
        gs = G * side[:, None]
        keep = ((when >= T_START) & (side != 0) & (best >= min(SCORES) - 1e-9)
                & (gs[:, :3].max(1) == 1) & (gs[:, 3:].max(1) == 1))
        i = np.nonzero(keep)[0]
        zl = zlsma(c)
        zdn = np.zeros(nmin, dtype=np.int8); zup = np.zeros(nmin, dtype=np.int8)
        zdn[b['last'][c < zl]] = 1; zup[b['last'][c > zl]] = 1
        E[etf] = dict(t=when[i], side=side[i], base=base[i], vr=np.nan_to_num(vr[i]), ar=np.nan_to_num(ar[i]), G=G[i],
                      m0=b['last'][i] + 1, px=c[i], zdn=zdn, zup=zup)
    return E


@njit(cache=True)
def outcome(o, h, l, c, zdn, zup, m0, side, close, sl, t1, t2, t3, zmode):
    """-> net P&L (fraction of entry), exit 1m index, targets hit (0-3), exit reason (index into REASONS)."""
    e = close * (1 + side * SLIP)
    pnl = -TAKER
    stop = e * (1 - side * sl)
    tg = (e * (1 + side * t1), e * (1 + side * t2), e * (1 + side * t3))
    left = 1.0; k = 0; n = len(c)
    for m in range(m0, n):
        if (l[m] <= stop) if side == 1 else (h[m] >= stop):
            px = stop
            if side == 1 and o[m] < stop: px = o[m]
            if side == -1 and o[m] > stop: px = o[m]
            px *= (1 - side * SLIP)
            pnl += left * (side * (px - e) / e - TAKER)
            return pnl, m, k, 0 if k == 0 else 1
        while k < 3 and ((h[m] >= tg[k]) if side == 1 else (l[m] <= tg[k])):
            pnl += SPLIT[k] * (side * (tg[k] - e) / e - MAKER)
            left -= SPLIT[k]
            if k == 0: stop = e * (1 + side * BE_BUFFER)
            elif k == 1: stop = tg[0]
            k += 1
        if k == 3: return pnl, m, 3, 3
        if (zmode == 0 or (zmode == 1 and k >= 1)) and ((zdn[m] == 1) if side == 1 else (zup[m] == 1)):
            px = c[m] * (1 - side * SLIP)
            pnl += left * (side * (px - e) / e - TAKER)
            return pnl, m, k, 2
    pnl += left * (side * (c[n - 1] - e) / e - TAKER)
    return pnl, n - 1, k, 4


@njit(parallel=True, cache=True)
def outcomes(o, h, l, c, zdn, zup, m0, side, px, exits):
    n = len(m0); ne = exits.shape[0]
    R = np.zeros((n, ne)); X = np.zeros((n, ne), dtype=np.int64); K = np.zeros((n, ne), dtype=np.int8); Q = np.zeros((n, ne), dtype=np.int8)
    for i in prange(n):
        for k in range(ne):
            sl = exits[k, 0] / 100
            p, x, kk, q = outcome(o, h, l, c, zdn, zup, m0[i], side[i], px[i], sl, exits[k, 1] / 100, exits[k, 2] / 100, exits[k, 3] / 100, int(exits[k, 4]))
            R[i, k] = p / sl; X[i, k] = x; K[i, k] = kk; Q[i, k] = q
    return R, X, K, Q


# metric columns per config
COLS = ['trades', 'longs', 'shorts', 'wins', 'long_wins', 'short_wins', 'gross_win', 'gross_loss', 'long_gw', 'long_gl',
        'short_gw', 'short_gl', 'best', 'worst', 'tp1', 'tp2', 'tp3', 'sl', 'be_stop', 'zlsma', 'max_dd',
        'train_trades', 'train_gw', 'train_gl', 'test_trades', 'test_gw', 'test_gl']
NC = len(COLS)


@njit(parallel=True, cache=True)
def grid(t, side, base, vr, ar, G, m0, R, X, K, Q, si, hi, scores, vols, atrx, wv, wa, wsum, split):
    ns = len(scores); nv = len(vols); na = len(atrx); ne = R.shape[1]; n = len(t)
    out = np.zeros((ns, ns, nv, na, ne, NC))
    for q in prange(ns * ns * nv * na):
        a_ = q % na; v_ = (q // na) % nv; s_ = (q // (na * nv)) % ns; l_ = q // (na * nv * ns)
        sel = np.empty(n, dtype=np.int64); cnt = 0
        for i in range(n):
            if G[i, si] != side[i] or G[i, hi] != side[i]: continue
            sc = (base[i] + (wv if vr[i] >= vols[v_] else 0.0) + (wa if ar[i] >= atrx[a_] else 0.0)) / wsum * 100
            if sc < (scores[l_] if side[i] == 1 else scores[s_]) - 1e-9: continue
            sel[cnt] = i; cnt += 1
        for k in range(ne):
            o = out[l_, s_, v_, a_, k]
            o[12] = -1e9; o[13] = 1e9
            free = -1; eq = 0.0; peak = 0.0
            for jj in range(cnt):
                i = sel[jj]
                if m0[i] <= free: continue
                free = X[i, k]
                r = R[i, k]; lg = side[i] == 1
                o[0] += 1
                if lg: o[1] += 1
                else: o[2] += 1
                if r > 0:
                    o[3] += 1; o[6] += r
                    if lg: o[4] += 1; o[8] += r
                    else: o[5] += 1; o[10] += r
                else:
                    o[7] -= r
                    if lg: o[9] -= r
                    else: o[11] -= r
                if r > o[12]: o[12] = r
                if r < o[13]: o[13] = r
                kk = K[i, k]
                if kk >= 1: o[14] += 1
                if kk >= 2: o[15] += 1
                if kk >= 3: o[16] += 1
                qq = Q[i, k]
                if qq == 0: o[17] += 1
                elif qq == 1: o[18] += 1
                elif qq == 2: o[19] += 1
                eq += r
                if eq > peak: peak = eq
                if peak - eq > o[20]: o[20] = peak - eq
                if t[i] < split:
                    o[21] += 1
                    if r > 0: o[22] += r
                    else: o[23] -= r
                else:
                    o[24] += 1
                    if r > 0: o[25] += r
                    else: o[26] -= r
            if o[0] == 0: o[12] = 0; o[13] = 0
    return out


def trade_list(E, combo, ls, ss, vm, ae, k):
    e, s, h = combo
    d = E[e]
    si = SETUP_TFS.index(s); hi = 3 + HTFS.index(h)
    sc = (d['base'] + W['vol'] * (d['vr'] >= vm) + W['atr'] * (d['ar'] >= ae)) / W_SUM * 100
    ok = (d['G'][:, si] == d['side']) & (d['G'][:, hi] == d['side']) & (sc >= np.where(d['side'] == 1, ls, ss) - 1e-9)
    out = []; free = -1
    for i in np.nonzero(ok)[0]:
        if d['m0'][i] <= free: continue
        free = d['X'][i, k]
        out.append(dict(i=int(i), t=int(d['t'][i]), side=int(d['side'][i]), score=round(float(sc[i])), px=float(d['px'][i]),
                        x=int(free), R=float(d['R'][i, k]), tp=int(d['K'][i, k]), why=int(d['Q'][i, k])))
    return out


def pf(w, l):
    return w / l if l > 0 else float('inf')


def main():
    t0 = time.time()
    raw = np.load(DATA)
    m = {k: raw[k] for k in ('t', 'o', 'h', 'l', 'c', 'v', 'tb')}
    E = prepare(m)
    ex = np.array(EXITS)
    for etf, d in E.items():
        d['R'], d['X'], d['K'], d['Q'] = outcomes(m['o'], m['h'], m['l'], m['c'], d['zdn'], d['zup'], d['m0'], d['side'], d['px'], ex)
        print(f'{etf}m: {len(d["t"])} candidate candles ({time.time() - t0:.0f}s)', flush=True)
    shape = (len(TF_COMBOS), len(SCORES), len(SCORES), len(VOLS), len(ATRX), len(EXITS))
    M = np.zeros(shape + (NC,))
    for ci, (e, s, h) in enumerate(TF_COMBOS):
        d = E[e]
        M[ci] = grid(d['t'], d['side'], d['base'], d['vr'], d['ar'], d['G'], d['m0'], d['R'], d['X'], d['K'], d['Q'],
                     SETUP_TFS.index(s), 3 + HTFS.index(h), np.array(SCORES, dtype=np.float64), np.array(VOLS),
                     np.array(ATRX), float(W['vol']), float(W['atr']), float(W_SUM), T_SPLIT)
    print(f'grid done ({time.time() - t0:.0f}s)', flush=True)
    report(E, M, m)
    print(f'all done ({time.time() - t0:.0f}s)')


def cfg(ix):
    ci, l, s, v, a, k = ix
    e, st, h = TF_COMBOS[ci]
    sl, t1, t2, t3, z = EXITS[k]
    return dict(entry=f'{e}m', setup=f'{st}m' if st < 60 else '1H', htf=f'{h // 60}H', score_mode='same' if l == s else 'separate',
                long_score=SCORES[l], short_score=SCORES[s], vol=VOLS[v], atr=ATRX[a], sl_pct=sl, tp1_pct=t1, tp2_pct=t2, tp3_pct=t3, zlsma_exit=ZMODES[z])


def metrics(row):
    r = dict(zip(COLS, row))
    n = r['trades']
    return dict(trades=int(n), longs=int(r['longs']), shorts=int(r['shorts']),
                win_rate=round(r['wins'] / n * 100, 1) if n else 0.0,
                pf=round(pf(r['gross_win'], r['gross_loss']), 3), net_R=round(r['gross_win'] - r['gross_loss'], 2),
                max_dd_R=round(r['max_dd'], 2), avg_R=round((r['gross_win'] - r['gross_loss']) / n, 4) if n else 0.0,
                best_R=round(r['best'], 3), worst_R=round(r['worst'], 3),
                tp1_hits=int(r['tp1']), tp2_hits=int(r['tp2']), tp3_hits=int(r['tp3']), sl_hits=int(r['sl']), be_stop=int(r['be_stop']), zlsma=int(r['zlsma']),
                long_win_rate=round(r['long_wins'] / r['longs'] * 100, 1) if r['longs'] else 0.0,
                long_pf=round(pf(r['long_gw'], r['long_gl']), 3), long_net_R=round(r['long_gw'] - r['long_gl'], 2),
                short_win_rate=round(r['short_wins'] / r['shorts'] * 100, 1) if r['shorts'] else 0.0,
                short_pf=round(pf(r['short_gw'], r['short_gl']), 3), short_net_R=round(r['short_gw'] - r['short_gl'], 2),
                train_trades=int(r['train_trades']), train_pf=round(pf(r['train_gw'], r['train_gl']), 3),
                train_net_R=round(r['train_gw'] - r['train_gl'], 2),
                test_trades=int(r['test_trades']), test_pf=round(pf(r['test_gw'], r['test_gl']), 3),
                test_net_R=round(r['test_gw'] - r['test_gl'], 2))


def report(E, M, m):
    shape = M.shape[:-1]
    flat = M.reshape(-1, NC)
    total = len(flat)
    # ---- all configs to CSV ----
    with gzip.open(OUT_CSV, 'wt', newline='') as fh:
        wr = None
        for f in range(total):
            ix = np.unravel_index(f, shape)
            row = {**cfg(ix), **metrics(flat[f])}
            if wr is None:
                wr = csv.DictWriter(fh, fieldnames=list(row)); wr.writeheader()
            wr.writerow(row)
    C = {c: flat[:, j] for j, c in enumerate(COLS)}
    with np.errstate(divide='ignore', invalid='ignore'):
        pf_all = np.where(C['gross_loss'] > 0, C['gross_win'] / C['gross_loss'], np.nan)
        pf_tr = np.where(C['train_gl'] > 0, C['train_gw'] / C['train_gl'], np.nan)
        pf_te = np.where(C['test_gl'] > 0, C['test_gw'] / C['test_gl'], np.nan)
    net_te = C['test_gw'] - C['test_gl']
    MIN_TR = 40   # train trades: at least ~20 a year
    ok = C['train_trades'] >= MIN_TR
    idx = np.indices(shape).reshape(len(shape), -1)

    L = []; w = L.append
    w('# BTCUSDT.P LONG / SHORT score scalper — full grid')
    w('')
    w('Generated by `scripts/scalp_btc.py`. Binance USDⓈ-M BTCUSDT 1m candles, **train Jan 2023 – Dec 2024, test Jan 2025 – Sep 2026**.')
    w(f'Every combination of the parameter table: **{total:,} configurations**. Every configuration\'s metrics are in')
    w('[`scalp_btc_all.csv.gz`](scalp_btc_all.csv.gz); equity curves and complete trade lists for the top configurations are in')
    w('[`scalp_btc.html`](scalp_btc.html).')
    w('')
    w('## Rules')
    w('')
    w('| Step | LONG (SHORT mirrored) |')
    w('|---|---|')
    w('| HTF filter (1H / 4H) | last closed candle: close > EMA 200 and EMA 50 > EMA 200 |')
    w('| Setup (15m / 30m / 1H) | last closed candle: Chandelier Exit (22, 3) long and close > ZLSMA 32 |')
    w('| Entry (3m / 5m / 15m) | closed green candle with LONG score ≥ LONG min score; market order at its close |')
    w('| SL | SL % from entry |')
    w('| TP1 → TP2 → TP3 | close 40 / 35 / 25 % of the original size; each only out of what is still open |')
    w('| Break-even | after TP1 the stop moves to entry + 0.2 % (covers fees); after TP2 to TP1 |')
    w('| ZLSMA exit | an entry-TF candle closes below ZLSMA 32: the rest of the position closes at market. Tested three ways: always, only after TP1, off (×3 configurations) |')
    w('')
    w('**LONG score** (0–100; the SHORT score uses the mirrored checks):')
    w('')
    w('| Part | Check on the entry candle | Weight |')
    w('|---|---|---|')
    w('| 30m trend bullish | last closed 30m candle: close > EMA 50 and EMA 20 > EMA 50 | 10 |')
    w('| 1H trend bullish | same on 1H | 10 |')
    w('| Resistance breakout | close > the highest high of the 20 candles before | 15 |')
    w('| Volume expansion | volume ≥ multiplier × the average of the 20 candles before | 10 |')
    w('| ATR expansion | ATR 14 ≥ expansion × its 20-candle average | 10 |')
    w('| Order-book imbalance | **no history exists — left out** | (10) |')
    w('| Aggressive market buys | taker-buy volume ≥ 55 % of the candle\'s volume (≤ 45 % for SHORT) | 10 |')
    w('| Momentum bullish | MACD 12/26/9 line > signal and RSI 14 > 50 | 15 |')
    w('| Liquidity sweep / reclaim | a low in the last 5 candles broke the 20-candle low before them; close back above it | 10 |')
    w('')
    w(f'Score = points / {W_SUM} × 100. Costs: 0.055 % taker + 0.02 % slippage on entries, stops and ZLSMA exits (stops fill at the')
    w('open when price gaps through); 0.02 % maker on targets, filled on a touch. Replayed on 1m candles: stop first, then targets,')
    w('then the ZLSMA check at the candle close. One position at a time. **1R = the SL distance**: at 1 % risk per trade, +1R = +1 %')
    w('of the account (no compounding).')
    w('')
    w('## Overview')
    w('')
    w(f'- With at least {MIN_TR} train trades: **{ok.sum():,}** of {total:,} configurations.')
    w(f'- Profitable after costs on train: **{(ok & (pf_tr > 1)).sum():,}**; on test: **{(ok & (pf_te > 1)).sum():,}**; on both: '
      f'**{(ok & (pf_tr > 1) & (pf_te > 1)).sum():,}**.')
    w(f'- Median profit factor: train **{np.nanmedian(pf_tr[ok]):.2f}**, test **{np.nanmedian(pf_te[ok]):.2f}**, whole period '
      f'**{np.nanmedian(pf_all[ok]):.2f}**.')
    w('')
    w('## What each parameter does')
    w('')
    w(f'Each row: all configurations with that value and ≥ {MIN_TR} train trades. Medians over the rest of the grid.')
    w('')
    axes = [('Entry / setup / HTF', 0, lambda i: '{} / {} / {}'.format(*[cfg((i, 0, 0, 0, 0, 0))[x] for x in ('entry', 'setup', 'htf')]), len(TF_COMBOS)),
            ('LONG min score', 1, lambda i: str(SCORES[i]), len(SCORES)), ('SHORT min score', 2, lambda i: str(SCORES[i]), len(SCORES)),
            ('Volume multiplier', 3, lambda i: f'{VOLS[i]}x', len(VOLS)), ('ATR expansion', 4, lambda i: f'{ATRX[i]:.2f}x', len(ATRX))]
    hdr = '| {} | configs | trades / year | train PF | test PF | test PF > 1 | test R / year |'
    def row(name, msk):
        w(f'| {name} | {msk.sum():,} | {np.median(C["trades"][msk]) / (TRAIN_Y + TEST_Y):.0f} | {np.nanmedian(pf_tr[msk]):.2f} | '
          f'{np.nanmedian(pf_te[msk]):.2f} | {(pf_te[msk] > 1).mean() * 100:.0f} % | {np.median(net_te[msk]) / TEST_Y:+.1f} |')
    for name, ax, fmt, cnt in axes:
        w(hdr.format(name)); w('|---|---|---|---|---|---|---|')
        for v in range(cnt):
            msk = ok & (idx[ax] == v)
            if msk.any(): row(fmt(v), msk)
        w('')
    w(hdr.format('Score thresholds')); w('|---|---|---|---|---|---|---|')
    row('same for LONG and SHORT', ok & (idx[1] == idx[2])); row('separate', ok & (idx[1] != idx[2]))
    w('')
    w(hdr.format('ZLSMA exit')); w('|---|---|---|---|---|---|---|')
    for z, zn in enumerate(ZMODES):
        row(zn, ok & (np.array([e[4] for e in EXITS])[idx[5]] == z))
    w('')
    w(hdr.format('SL / TP1 / TP2 / TP3')); w('|---|---|---|---|---|---|---|')
    for k, (sl, t1, t2, t3, z) in enumerate(EXITS):
        if z: continue
        ks = [j for j, e in enumerate(EXITS) if e[:4] == (sl, t1, t2, t3)]
        msk = ok & np.isin(idx[5], ks)
        if msk.any(): row(f'{sl:g} / {t1:g} / {t2:g} / {t3:g} %', msk)
    w('')

    def table(order, title, n):
        w(title); w('')
        w('| # | Entry / setup / HTF | Score L / S | Vol | ATR | SL | TP1 / 2 / 3 | ZLSMA exit | Trades | Win % | Train PF | Test PF | Test R | Net R | Max DD R |')
        w('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for r, f in enumerate(order[:n]):
            c = cfg(np.unravel_index(f, shape)); mt = metrics(flat[f])
            w(f'| {r + 1} | {c["entry"]} / {c["setup"]} / {c["htf"]} | {c["long_score"]} / {c["short_score"]} | {c["vol"]}x | {c["atr"]:.2f}x | '
              f'{c["sl_pct"]:g} % | {c["tp1_pct"]:g} / {c["tp2_pct"]:g} / {c["tp3_pct"]:g} % | {c["zlsma_exit"]} | {mt["trades"]} | {mt["win_rate"]:.0f} | {mt["train_pf"]:.2f} | '
              f'{mt["test_pf"]:.2f} | {mt["test_net_R"]:+.1f} | {mt["net_R"]:+.1f} | {mt["max_dd_R"]:.1f} |')
        w('')
    fok = np.nonzero(ok)[0]
    order = fok[np.argsort(-np.nan_to_num(pf_tr[fok], nan=-1), kind='stable')]
    table(order, f'## Top 25 by train profit factor (≥ {MIN_TR} train trades), checked on test', 25)
    ok2 = C['train_trades'] >= 200
    f2 = np.nonzero(ok2)[0]
    order2 = f2[np.argsort(-np.nan_to_num(pf_tr[f2], nan=-1), kind='stable')]
    table(order2, '## Top 15 by train profit factor with ≥ 100 trades a year (≥ 200 train trades)', 15)
    order_te = fok[np.argsort(-np.nan_to_num(pf_te[fok], nan=-1), kind='stable')]
    table(order_te, '## For reference: top 10 by TEST profit factor (picked on the test window itself: not a fair estimate)', 10)

    # detail of the train pick
    f = order[0]
    c = cfg(np.unravel_index(f, shape)); mt = metrics(flat[f])
    w('## The train pick (#1) in full')
    w('')
    w(f'{c["entry"]} entry / {c["setup"]} setup / {c["htf"]} HTF · score LONG ≥ {c["long_score"]}, SHORT ≥ {c["short_score"]} · volume '
      f'{c["vol"]}x · ATR {c["atr"]:.2f}x · SL {c["sl_pct"]:g} % · TP {c["tp1_pct"]:g} / {c["tp2_pct"]:g} / {c["tp3_pct"]:g} % · ZLSMA exit {c["zlsma_exit"]}')
    w('')
    w('| Metric | Value |'); w('|---|---|')
    for k_, lab in (('trades', 'Total trades'), ('longs', 'LONG trades'), ('shorts', 'SHORT trades'), ('win_rate', 'Win rate %'),
                    ('pf', 'Profit factor'), ('net_R', 'Net P&L (R = % at 1 % risk)'), ('max_dd_R', 'Max drawdown (R)'),
                    ('avg_R', 'Average trade (R)'), ('best_R', 'Best trade (R)'), ('worst_R', 'Worst trade (R)'),
                    ('tp1_hits', 'TP1 hits'), ('tp2_hits', 'TP2 hits'), ('tp3_hits', 'TP3 hits'), ('sl_hits', 'SL hits (before TP1)'),
                    ('be_stop', 'Break-even / lock stops'), ('zlsma', 'ZLSMA exits'),
                    ('long_win_rate', 'LONG win rate %'), ('long_pf', 'LONG PF'), ('long_net_R', 'LONG net R'),
                    ('short_win_rate', 'SHORT win rate %'), ('short_pf', 'SHORT PF'), ('short_net_R', 'SHORT net R'),
                    ('train_pf', 'Train PF'), ('test_pf', 'Test PF')):
        w(f'| {lab} | {mt[k_]} |')
    w('')
    with open(OUT_MD, 'w') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote', OUT_MD, OUT_CSV)

    # ---- HTML explorer: top configs with equity curve + trade list ----
    groups = {}
    for name, lst in (('train', order[:60]), ('busy', order2[:30]), ('test', order_te[:15])):
        for f in lst: groups.setdefault(int(f), []).append(name)
    cfgs = []
    for f, g in groups.items():
        ix = np.unravel_index(f, shape)
        c = cfg(ix); mt = metrics(flat[f])
        ci, l, s, v, a, k = ix
        tl = trade_list(E, TF_COMBOS[ci], SCORES[l], SCORES[s], VOLS[v], ATRX[a], k)
        trades = [[x['t'], x['side'], x['score'], round(x['px'], 1), int(m['t'][x['x']] + MIN), round(x['R'], 3), x['tp'], x['why']] for x in tl]
        cfgs.append(dict(cfg=c, m=mt, g=g, trades=trades))
    tpl = open(os.path.join(os.path.dirname(__file__), 'scalp_btc_template.html')).read()
    with open(OUT_HTML, 'w') as fh:
        fh.write(tpl.replace('/*DATA*/null', json.dumps(dict(configs=cfgs, reasons=REASONS, split=T_SPLIT, total=total), separators=(',', ':'))))
    print('wrote', OUT_HTML, f'({len(cfgs)} configs)')


if __name__ == '__main__':
    main()
