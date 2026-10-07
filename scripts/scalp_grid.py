"""Multi-timeframe ATLAS-score scalping grid: every combination of

    entry TF 3m/5m/15m · setup TF 15m/30m/1H · HTF filter 1H/4H · min score 60-85 (same or separate long/short)
    volume multiplier 1.1/1.25/1.5/2 · ATR expansion 1.02/1.05/1.10 · SL 0.5/0.7/1% · TP1 1/1.5% · TP2 2/2.5/3% · TP3 3/4/5%

on Binance USD-M 1m candles (scripts/binance_1m.py), BTC ETH SOL XRP DOGE SUI, Jan 2023 - Sep 2026.

Rules (one position per coin at a time):
  setup    the ATLAS score (TradeBot src/atlasScore.js, classic, price/volume inputs; scripts/atlas_score.py) of the
           last closed setup-TF candle is >= +min score (long) or <= -min score (short)
  HTF      the ATLAS score of the last closed HTF candle leans the same way: >= +25 / <= -25 (ATLAS's bias threshold)
  entry    on a closed entry-TF candle that closes in the trade's direction, with
             volume >= multiplier x the average of the 20 candles before it, and
             ATR(14) >= expansion x its own 20-candle average
           market order at that close: 0.055% taker + 0.02% slippage
  exits    stop SL% from entry; T1 / T2 / T3 close 40 / 35 / 25% (TradeBot's split) with 0.02% maker fees;
           after T1 the stop moves to entry + 0.2%, after T2 to T1 (TradeBot's live rules);
           stops pay taker + slippage and fill at the open if price gaps through. Replayed on 1m candles, stop
           first when a minute touches both. No time limit.

Results are in R (1R = the SL distance; at 1% risk per trade, 1R = 1% of the account). Train = 2023-2024,
test = Jan 2025 - Sep 2026.

    python3 scripts/scalp_grid.py            writes backtests/scalp_grid.md (~15 min, cached in data/scalp_cache)
"""
import os, sys, time
from itertools import product

import numpy as np
from numba import njit, prange

sys.path.insert(0, os.path.dirname(__file__))
from atlas_score import score, atr, sma  # noqa: E402

DATA = os.path.join('data', 'binance_1m')
CACHE = os.path.join('data', 'scalp_cache')
OUT = os.path.join('backtests', 'scalp_grid.md')
COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'SUIUSDT']
MIN = 60000
DAY = 86400000
T_START = 1672531200000          # 2023-01-01
T_SPLIT = 1735689600000          # 2025-01-01
YEARS = [2023, 2024, 2025, 2026]
Y_START = [1672531200000, 1704067200000, 1735689600000, 1767225600000]

ENTRY_TFS = [3, 5, 15]
SETUP_TFS = [15, 30, 60]
HTFS = [60, 240]
SCORES = [60, 65, 70, 75, 80, 85]
VOLS = [1.1, 1.25, 1.5, 2.0]
ATRX = [1.02, 1.05, 1.10]
SLS = [0.5, 0.7, 1.0]
EXITS = [(sl, t1, t2, t3) for sl in SLS for t1 in (1.0, 1.5) for t2 in (2.0, 2.5, 3.0) for t3 in (3.0, 4.0, 5.0) if t1 < t2 < t3]
# entry / setup / HTF combos where each is a larger candle than the one before
TF_COMBOS = [(e, s, h) for e in ENTRY_TFS for s in SETUP_TFS for h in HTFS if e < s < h]
HTF_BIAS = 25
TAKER, MAKER, SLIP = 0.00055, 0.0002, 0.0002
SPLIT = (0.40, 0.35, 0.25)
BE_BUFFER = 0.002


def bars(m, tf):
    """UTC-aligned tf-minute candles from 1m arrays."""
    t = m['t']
    starts = np.r_[0, np.nonzero(np.diff(t // (tf * MIN)))[0] + 1]
    ends = np.r_[starts[1:] - 1, len(t) - 1]
    return dict(t=(t[starts] // (tf * MIN)) * tf * MIN, o=m['o'][starts], h=np.maximum.reduceat(m['h'], starts),
                l=np.minimum.reduceat(m['l'], starts), c=m['c'][ends], v=np.add.reduceat(m['v'], starts))


def tf_score(m, tf):
    b = bars(m, tf)
    d = bars(m, 1440)
    piv = (d['h'] + d['l'] + d['c']) / 3
    j = np.searchsorted(d['t'] + DAY, b['t'] + tf * MIN, side='right') - 1
    pivot = np.where(j >= 1, piv[np.maximum(j, 0)], np.nan)
    return b['t'] + tf * MIN, score(b['t'], b['o'], b['h'], b['l'], b['c'], b['v'], pivot)


@njit(cache=True)
def outcome(o, h, l, c, m0, side, entry_close, sl, t1, t2, t3):
    """Net P&L (fraction of entry) and the 1m index of the exit, for one trade."""
    e = entry_close * (1 + side * SLIP)
    pnl = -TAKER
    stop = e * (1 - side * sl)
    tg = (e * (1 + side * t1), e * (1 + side * t2), e * (1 + side * t3))
    left = 1.0; k = 0
    n = len(c)
    for m in range(m0, n):
        hit = l[m] <= stop if side == 1 else h[m] >= stop
        if hit:
            px = stop
            if side == 1 and o[m] < stop: px = o[m]
            if side == -1 and o[m] > stop: px = o[m]
            px *= (1 - side * SLIP)
            pnl += left * (side * (px - e) / e - TAKER)
            return pnl, m
        while k < 3 and (h[m] >= tg[k] if side == 1 else l[m] <= tg[k]):
            pnl += SPLIT[k] * (side * (tg[k] - e) / e - MAKER)
            left -= SPLIT[k]
            if k == 0: stop = e * (1 + side * BE_BUFFER)
            elif k == 1: stop = tg[0]
            k += 1
        if k == 3: return pnl, m
    pnl += left * (side * (c[n - 1] - e) / e - TAKER)
    return pnl, n - 1


@njit(parallel=True, cache=True)
def outcomes(o, h, l, c, m0, side, px, exits):
    n = len(m0); ne = exits.shape[0]
    R = np.zeros((n, ne)); X = np.zeros((n, ne), dtype=np.int64)
    for i in prange(n):
        for k in range(ne):
            p, x = outcome(o, h, l, c, m0[i], side[i], px[i], exits[k, 0] / 100, exits[k, 1] / 100, exits[k, 2] / 100, exits[k, 3] / 100)
            R[i, k] = p / (exits[k, 0] / 100); X[i, k] = x
    return R, X


def candidates(sym):
    """Entry-TF candles that pass the loosest filter, with their scores and exit outcomes for all 48 exits."""
    f = os.path.join(CACHE, f'{sym}.npz')
    if os.path.exists(f):
        z = np.load(f)
        return {k: z[k] for k in z.files}
    t0 = time.time()
    raw = np.load(os.path.join(DATA, f'{sym}.npz'))
    m = {k: raw[k] for k in 'tohlcv'}
    sc = {tf: tf_score(m, tf) for tf in sorted(set(SETUP_TFS + HTFS))}
    out = {}
    ex = np.array(EXITS)
    for etf in ENTRY_TFS:
        b = bars(m, etf)
        close_t = b['t'] + etf * MIN
        vavg = np.r_[np.nan, sma(b['v'], 20)[:-1]]
        vr = b['v'] / vavg
        a = atr(b['h'], b['l'], b['c'], 14)
        ar = a / sma(np.nan_to_num(a), 20)
        side = np.sign(b['c'] - b['o']).astype(np.int64)
        S = np.zeros((len(b['t']), 5), dtype=np.int64)  # setup 15/30/60, HTF 60/240
        for j, tf in enumerate(SETUP_TFS + HTFS):
            ct, s = sc[tf]
            idx = np.searchsorted(ct, close_t, side='right') - 1
            S[:, j] = np.where(idx >= 0, s[np.maximum(idx, 0)], 0)
        ss = S * side[:, None]
        keep = ((close_t >= T_START) & (side != 0) & (vr >= min(VOLS)) & (ar >= min(ATRX))
                & (ss[:, :3].max(1) >= min(SCORES)) & (ss[:, 3:].max(1) >= HTF_BIAS))
        i = np.nonzero(keep)[0]
        m0 = ((close_t[i] - m['t'][0]) // MIN).astype(np.int64)  # first 1m candle after the entry candle closed
        R, X = outcomes(m['o'], m['h'], m['l'], m['c'], m0, side[i], b['c'][i], ex)
        out[f'{etf}_t'] = close_t[i]; out[f'{etf}_side'] = side[i]; out[f'{etf}_vr'] = vr[i]; out[f'{etf}_ar'] = ar[i]
        out[f'{etf}_S'] = S[i]; out[f'{etf}_m0'] = m0; out[f'{etf}_R'] = R; out[f'{etf}_X'] = X
        print(f'  {sym} {etf}m: {len(i)} candidate candles', flush=True)
    os.makedirs(CACHE, exist_ok=True)
    np.savez(f, **out)
    print(f'  {sym} done in {time.time() - t0:.0f}s', flush=True)
    return out


@njit(parallel=True, cache=True)
def grid(t, side, vr, ar, S, m0, R, X, si, hi, scores, vols, atrx, ystart, split):
    """Stats per (long score, short score, vol, atr, exit): [period(train/test)][n, wins, gross win, gross loss] and R per year."""
    ns = len(scores); nv = len(vols); na = len(atrx); ne = R.shape[1]; n = len(t)
    stats = np.zeros((ns, ns, nv, na, ne, 2, 4))
    years = np.zeros((ns, ns, nv, na, ne, len(ystart)))
    for q in prange(ns * ns * nv * na):
        a_ = q % na; v_ = (q // na) % nv; s_ = (q // (na * nv)) % ns; l_ = q // (na * nv * ns)
        sel = np.empty(n, dtype=np.int64); cnt = 0
        for i in range(n):
            if vr[i] < vols[v_] or ar[i] < atrx[a_]: continue
            if side[i] == 1:
                if S[i, si] < scores[l_] or S[i, hi] < 25: continue
            else:
                if S[i, si] > -scores[s_] or S[i, hi] > -25: continue
            sel[cnt] = i; cnt += 1
        for k in range(ne):
            free = -1
            for jj in range(cnt):
                i = sel[jj]
                if m0[i] <= free: continue
                free = X[i, k]
                r = R[i, k]
                p = 0 if t[i] < split else 1
                stats[l_, s_, v_, a_, k, p, 0] += 1
                if r > 0:
                    stats[l_, s_, v_, a_, k, p, 1] += 1; stats[l_, s_, v_, a_, k, p, 2] += r
                else:
                    stats[l_, s_, v_, a_, k, p, 3] -= r
                y = 0
                while y + 1 < len(ystart) and t[i] >= ystart[y + 1]: y += 1
                years[l_, s_, v_, a_, k, y] += r
    return stats, years


def trades(c, combo, ls, ss, vm, ae, k):
    """Trade list (close time, R, side) for one config on one coin."""
    e, s, h = combo
    t, side, vr, ar, S, m0, R, X = (c[f'{e}_{x}'] for x in ('t', 'side', 'vr', 'ar', 'S', 'm0', 'R', 'X'))
    si = SETUP_TFS.index(s); hi = 3 + HTFS.index(h)
    ok = (vr >= vm) & (ar >= ae) & np.where(side == 1, (S[:, si] >= ls) & (S[:, hi] >= HTF_BIAS),
                                            (S[:, si] <= -ss) & (S[:, hi] <= -HTF_BIAS))
    out = []; free = -1
    for i in np.nonzero(ok)[0]:
        if m0[i] <= free: continue
        free = X[i, k]
        out.append((X[i, k], t[i], R[i, k], side[i]))
    return out


def pf(w, l):
    return w / l if l > 0 else float('inf')


def main():
    t0 = time.time()
    cands = {}
    for sym in COINS:
        cands[sym] = candidates(sym)
    nc = len(TF_COMBOS)
    shape = (nc, len(SCORES), len(SCORES), len(VOLS), len(ATRX), len(EXITS))
    ST = np.zeros(shape + (2, 4)); YR = np.zeros(shape + (len(YEARS),))
    COIN_ST = {}
    for sym in COINS:
        c = cands[sym]
        cs = np.zeros(shape + (2, 4))
        for ci, (e, s, h) in enumerate(TF_COMBOS):
            st, yr = grid(c[f'{e}_t'], c[f'{e}_side'], c[f'{e}_vr'], c[f'{e}_ar'], c[f'{e}_S'], c[f'{e}_m0'], c[f'{e}_R'],
                          c[f'{e}_X'], SETUP_TFS.index(s), 3 + HTFS.index(h), np.array(SCORES, dtype=np.float64),
                          np.array(VOLS), np.array(ATRX), np.array(Y_START, dtype=np.int64), T_SPLIT)
            cs[ci] = st; YR[ci] += yr
        ST += cs; COIN_ST[sym] = cs
        print(f'grid {sym} done ({time.time() - t0:.0f}s)', flush=True)
    np.savez(os.path.join(CACHE, 'grid.npz'), ST=ST, YR=YR, **{f'coin_{k}': v for k, v in COIN_ST.items()})
    report(cands, ST, YR, COIN_ST)


def label(idx):
    ci, l, s, v, a, k = idx
    e, st, h = TF_COMBOS[ci]
    sl, t1, t2, t3 = EXITS[k]
    sc = f'{SCORES[l]}' if l == s else f'L{SCORES[l]}/S{SCORES[s]}'
    return f'{e}m / {st}m / {h // 60}H', sc, f'{VOLS[v]}x', f'{ATRX[a]:.2f}x', f'{sl:g}%', f'{t1:g} / {t2:g} / {t3:g}%'


TRAIN_Y = 2.0
TEST_Y = 1.75


def report(cands, ST, YR, COIN_ST):
    n_tr, w_tr, gw_tr, gl_tr = (ST[..., 0, j] for j in range(4))
    n_te, w_te, gw_te, gl_te = (ST[..., 1, j] for j in range(4))
    with np.errstate(divide='ignore', invalid='ignore'):
        pf_tr = np.where(gl_tr > 0, gw_tr / gl_tr, np.nan)
        pf_te = np.where(gl_te > 0, gw_te / gl_te, np.nan)
    net_tr = gw_tr - gl_tr; net_te = gw_te - gl_te
    total = ST.size // 8
    sym_mask = np.zeros(ST.shape[:-2], dtype=bool)
    for l in range(len(SCORES)):
        sym_mask[:, l, l] = True
    MIN_TR = 100  # trades in train (6 coins, 2 years): at least ~50 a year
    ok = n_tr >= MIN_TR
    L = []
    w = L.append
    w('# Multi-timeframe ATLAS-score scalping grid')
    w('')
    w('Generated by `scripts/scalp_grid.py`. Binance USDⓈ-M 1m candles, **BTC, ETH, SOL, XRP, DOGE, SUI** (SUI from May 2023), ')
    w('**train Jan 2023 – Dec 2024, test Jan 2025 – Sep 2026**. All combinations of the parameter table:')
    w('')
    w('| Parameter | Values |')
    w('|---|---|')
    w('| Entry TF / setup TF / HTF | 3m, 5m, 15m / 15m, 30m, 1H / 1H, 4H (each larger than the one before: 13 combos) |')
    w('| Min score | 60, 65, 70, 75, 80, 85; the same for longs and shorts, or separate (36 pairs) |')
    w('| Volume multiplier | 1.1, 1.25, 1.5, 2 × |')
    w('| ATR expansion | 1.02, 1.05, 1.10 × |')
    w('| SL | 0.5, 0.7, 1.0 % |')
    w('| TP1 / TP2 / TP3 | 1, 1.5 / 2, 2.5, 3 / 3, 4, 5 % (TP1 < TP2 < TP3: 16 ladders) |')
    w('')
    w(f'= **{total:,} configurations**, each on 6 coins.')
    w('')
    w('## Rules')
    w('')
    w('- **Score** = TradeBot\'s ATLAS score (`src/atlasScore.js`, classic mode) with the inputs a backtest has: price, volume and the')
    w('  daily pivot. The order-flow inputs of the live bot (funding, open interest, long/short ratio, order book, taker tape) cannot be')
    w('  replayed. The Python port (`scripts/atlas_score.py`) gives the same score as the bot\'s own code on 645 of 645 sampled')
    w('  15m / 1H / 4H candles (`scripts/check_score.js`).')
    w('- **Setup**: the last closed setup-TF candle scores ≥ +min score (long) or ≤ −min score (short).')
    w(f'- **HTF filter**: the last closed HTF candle\'s score leans the same way (≥ +{HTF_BIAS} / ≤ −{HTF_BIAS}, ATLAS\'s bias threshold).')
    w('- **Entry**: a closed entry-TF candle in the trade\'s direction (green for longs, red for shorts) with volume ≥ multiplier × the')
    w('  average of the 20 candles before it, and ATR(14) ≥ expansion × its own 20-candle average. Market order at that close.')
    w('- **Exits**: stop at SL %; TP1 / TP2 / TP3 close 40 / 35 / 25 % (TradeBot\'s split). After TP1 the stop goes to entry + 0.2 %,')
    w('  after TP2 to TP1 (TradeBot\'s live rules). Replayed on 1m candles, stop first when a minute touches both; no time limit.')
    w('- **Costs**: 0.055 % taker + 0.02 % slippage on the entry and on stops (filled at the open when price gaps through);')
    w('  0.02 % maker on targets (filled on a touch). One position per coin at a time.')
    w('- **R**: 1R = the SL distance. At 1 % risk per trade, +1R = +1 % of the account (6 coins, fixed risk, no compounding).')
    w('')

    # ---- overview ----
    both = ok & (pf_tr > 1) & (pf_te > 1)
    w('## Overview')
    w('')
    w(f'- Configurations with at least {MIN_TR} train trades: **{ok.sum():,}** of {total:,}.')
    w(f'- Of those, profitable after costs in train: **{(ok & (pf_tr > 1)).sum():,}**; in test: **{(ok & (pf_te > 1)).sum():,}**;'
      f' in both: **{both.sum():,}** ({both.sum() / max(ok.sum(), 1) * 100:.1f} %).')
    w(f'- Median profit factor: train **{np.nanmedian(pf_tr[ok]):.2f}**, test **{np.nanmedian(pf_te[ok]):.2f}**.')
    w('')

    # ---- parameter effects ----
    w('## What each parameter does')
    w('')
    w(f'Each row: all configurations with that value (and ≥ {MIN_TR} train trades). Median PF over the rest of the grid, the share with')
    w('test PF > 1, the median number of trades a year (6 coins together), and the median test result in R a year.')
    w('')
    idx = np.indices(ST.shape[:-2])
    axes = [('Entry / setup / HTF', 0, lambda i: '{}m / {}m / {}H'.format(TF_COMBOS[i][0], TF_COMBOS[i][1], TF_COMBOS[i][2] // 60), len(TF_COMBOS)),
            ('Min score (long)', 1, lambda i: str(SCORES[i]), len(SCORES)),
            ('Min score (short)', 2, lambda i: str(SCORES[i]), len(SCORES)),
            ('Volume multiplier', 3, lambda i: f'{VOLS[i]}x', len(VOLS)),
            ('ATR expansion', 4, lambda i: f'{ATRX[i]:.2f}x', len(ATRX))]
    tr_y = n_tr / TRAIN_Y; te_y = net_te / TEST_Y
    for name, ax, fmt, cnt in axes:
        w(f'| {name} | configs | train PF | test PF | test PF > 1 | trades / year | test R / year |')
        w('|---|---|---|---|---|---|---|')
        for v in range(cnt):
            msk = ok & (idx[ax] == v)
            if not msk.any():
                continue
            w(f'| {fmt(v)} | {msk.sum():,} | {np.nanmedian(pf_tr[msk]):.2f} | {np.nanmedian(pf_te[msk]):.2f} | '
              f'{(pf_te[msk] > 1).mean() * 100:.0f} % | {np.median(tr_y[msk]):.0f} | {np.median(te_y[msk]):+.1f} |')
        w('')
    # symmetric vs asymmetric
    w('| Score thresholds | configs | train PF | test PF | test PF > 1 | trades / year | test R / year |')
    w('|---|---|---|---|---|---|---|')
    for nm, mm in (('same for long and short', sym_mask), ('separate long / short', ~sym_mask)):
        msk = ok & mm
        w(f'| {nm} | {msk.sum():,} | {np.nanmedian(pf_tr[msk]):.2f} | {np.nanmedian(pf_te[msk]):.2f} | '
          f'{(pf_te[msk] > 1).mean() * 100:.0f} % | {np.median(tr_y[msk]):.0f} | {np.median(te_y[msk]):+.1f} |')
    w('')
    # exits
    ex_idx = idx[5]
    w('| SL | TP1 / TP2 / TP3 | configs | train PF | test PF | test PF > 1 | test R / year |')
    w('|---|---|---|---|---|---|---|')
    for k, (sl, t1, t2, t3) in enumerate(EXITS):
        msk = ok & (ex_idx == k)
        if not msk.any():
            continue
        w(f'| {sl:g} % | {t1:g} / {t2:g} / {t3:g} % | {msk.sum():,} | {np.nanmedian(pf_tr[msk]):.2f} | {np.nanmedian(pf_te[msk]):.2f} | '
          f'{(pf_te[msk] > 1).mean() * 100:.0f} % | {np.median(te_y[msk]):+.1f} |')
    w('')

    # ---- best by train, checked on test ----
    def table(order, title, n=25):
        w(title)
        w('')
        w('| # | Entry / setup / HTF | Score | Vol | ATR | SL | TP1 / 2 / 3 | Train trades/yr | Train PF | Train R | Test trades/yr | Test PF | Test R | Test win % |')
        w('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for r, flat in enumerate(order[:n]):
            ix = np.unravel_index(flat, ST.shape[:-2])
            tf, sc, v, a, sl, tp = label(ix)
            w(f'| {r + 1} | {tf} | {sc} | {v} | {a} | {sl} | {tp} | {n_tr[ix] / TRAIN_Y:.0f} | {pf_tr[ix]:.2f} | {net_tr[ix]:+.0f} | '
              f'{n_te[ix] / TEST_Y:.0f} | {pf_te[ix]:.2f} | {net_te[ix]:+.0f} | {w_te[ix] / max(n_te[ix], 1) * 100:.0f} |')
        w('')

    flat_ok = np.nonzero(ok.ravel())[0]
    key = np.nan_to_num(pf_tr.ravel()[flat_ok], nan=-1)
    order = flat_ok[np.argsort(-key, kind='stable')]
    table(order, f'## Top 25 by train profit factor (≥ {MIN_TR} train trades), and how they did on test', 25)
    ok300 = n_tr >= 300
    flat300 = np.nonzero(ok300.ravel())[0]
    order300 = flat300[np.argsort(-np.nan_to_num(pf_tr.ravel()[flat300], nan=-1), kind='stable')]
    table(order300, '## Top 15 by train profit factor with ≥ 150 trades a year (≥ 300 train trades)', 15)
    order_te = flat_ok[np.argsort(-np.nan_to_num(pf_te.ravel()[flat_ok], nan=-1), kind='stable')]
    table(order_te, '## For reference: top 10 by TEST profit factor (picked on the test window itself, so not a fair estimate)', 10)

    # ---- the train pick in detail ----
    pick = np.unravel_index(order[0], ST.shape[:-2])
    w('## The train pick in detail (#1 above)')
    w('')
    tf, sc, v, a, sl, tp = label(pick)
    w(f'{tf} · score {sc} · volume {v} · ATR {a} · SL {sl} · TP {tp}')
    w('')
    w('| Coin | Train trades | Train PF | Train R | Test trades | Test PF | Test R |')
    w('|---|---|---|---|---|---|---|')
    for sym in COINS:
        s = COIN_ST[sym][pick]
        w(f'| {sym[:-4]} | {s[0, 0]:.0f} | {pf(s[0, 2], s[0, 3]):.2f} | {s[0, 2] - s[0, 3]:+.1f} | {s[1, 0]:.0f} | '
          f'{pf(s[1, 2], s[1, 3]):.2f} | {s[1, 2] - s[1, 3]:+.1f} |')
    w('')
    w('| Year | ' + ' | '.join(str(y) for y in YEARS) + ' |')
    w('|---|' + '---|' * len(YEARS))
    w('| R (6 coins) | ' + ' | '.join(f'{x:+.1f}' for x in YR[pick]) + ' |')
    w('')
    ci, l, s_, v_, a_, k = pick
    allt = []
    for sym in COINS:
        allt += trades(cands[sym], TF_COMBOS[ci], SCORES[l], SCORES[s_], VOLS[v_], ATRX[a_], k)
    allt.sort()
    eq = np.cumsum([x[2] for x in allt])
    dd = (np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max() if len(eq) else 0
    longs = [x[2] for x in allt if x[3] == 1]; shorts = [x[2] for x in allt if x[3] == -1]
    lw = sum(x for x in longs if x > 0); ll = -sum(x for x in longs if x <= 0)
    sw = sum(x for x in shorts if x > 0); sl_ = -sum(x for x in shorts if x <= 0)
    w(f'Whole period: {len(allt)} trades, {eq[-1]:+.1f} R, max drawdown {dd:.1f} R (at 1 % risk: {dd:.0f} % of the starting account). '
      f'Longs {len(longs)} (PF {pf(lw, ll):.2f}), shorts {len(shorts)} (PF {pf(sw, sl_):.2f}).')
    w('')
    with open(OUT, 'w') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote', OUT)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--report':
        z = np.load(os.path.join(CACHE, 'grid.npz'))
        report({s: candidates(s) for s in COINS}, z['ST'], z['YR'], {s: z[f'coin_{s}'] for s in COINS})
    else:
        main()
