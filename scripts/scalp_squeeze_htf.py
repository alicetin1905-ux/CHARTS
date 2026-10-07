"""BTCUSDT.P compression -> expansion on larger timeframes, stop on the other side of the box.

Same rules as scripts/scalp_squeeze.py (one symmetric rule set, separate LONG / SHORT thresholds), with:
    entry TF   15m / 30m / 1H        setup TF  1H / 2H / 4H (larger than entry)       HTF filter  off / 4H / 1D
    stop       the far side of the box (LONG: box low) or the box midpoint, 0.05 % beyond it;
               trades whose stop is closer than 0.3 % are skipped (fees would be a large share of 1R)
    targets    in R (multiples of that stop distance): TP1 1 / 1.5R, TP2 2 / 2.5 / 3R, TP3 3 / 4 / 5R (TP1 < TP2 < TP3),
               closing 40 / 35 / 25 % (each only out of what is left); after TP1 stop to entry + 0.2 %, after TP2 to TP1
    ZLSMA      exit on an entry-TF close beyond ZLSMA 32 after TP1, or off
Costs and 1m replay as scripts/scalp_btc.py. 1R = the stop distance (1 % of the account at 1 % risk).

    python3 scripts/scalp_squeeze_htf.py  -> backtests/scalp_squeeze_htf.md, _long/_short.csv.gz, .html
"""
import csv, gzip, json, os, sys, time

import numpy as np
from numba import njit, prange

sys.path.insert(0, os.path.dirname(__file__))
from atlas_score import ema, atr, sma, highest, lowest  # noqa: E402
from scalp_btc import bars, zlsma, at_close, pf, REASONS, DATA, MIN, T_START, T_SPLIT, TRAIN_Y, TEST_Y  # noqa: E402
from scalp_squeeze import pct_rank, combine, stats, COLS, NC  # noqa: E402
from scalp_btc import TAKER, MAKER, SLIP, BE_BUFFER, SPLIT  # noqa: E402

OUT = os.path.join('backtests', 'scalp_squeeze_htf')
ENTRY_TFS = [15, 30, 60]
SETUP_TFS = [60, 120, 240]
TF_COMBOS = [(e, s) for e in ENTRY_TFS for s in SETUP_TFS if e < s]
PCTS = [10, 20, 30]
BOXES = [10, 20]
VOLS = [1.5, 2.0, 3.0]
RANGES = [1.0, 1.5, 2.0]
FLOWS = ['off', '55%']
HTFS = ['off', '4H', '1D']
STOPS = ['box far side', 'box midpoint']
ZMODES = ['always', 'after TP1', 'off']
EXITS = [(st, t1, t2, t3, z) for z in (1, 2) for st in (0, 1) for t1 in (1.0, 1.5) for t2 in (2.0, 2.5, 3.0) for t3 in (3.0, 4.0, 5.0)
         if t1 < t2 < t3]
NSIG = len(PCTS) * len(BOXES) * len(VOLS) * len(RANGES) * len(FLOWS) * len(HTFS)
STOP_BUF, MIN_STOP = 0.0005, 0.003
MIN_TR = 20  # train trades per side


def tfname(tf):
    return f'{tf}m' if tf < 60 else f'{tf // 60}H'


@njit(cache=True)
def outcome_r(o, h, l, c, zdn, zup, m0, side, close, sl, r1, r2, r3, zmode):
    """Like scalp_btc.outcome, with the stop sl (fraction) given per trade and targets at r1/r2/r3 x sl."""
    e = close * (1 + side * SLIP)
    pnl = -TAKER
    stop = e * (1 - side * sl)
    tg = (e * (1 + side * r1 * sl), e * (1 + side * r2 * sl), e * (1 + side * r3 * sl))
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
def outcomes_r(o, h, l, c, zdn, zup, m0, side, px, stops, exits):
    """stops[i, b, s]: stop distance (fraction) of candidate i for box b and stop mode s (NaN = no trade)."""
    n = len(m0); nb = stops.shape[1]; ne = exits.shape[0]
    R = np.full((n, nb, ne), np.nan); X = np.zeros((n, nb, ne), dtype=np.int64)
    K = np.zeros((n, nb, ne), dtype=np.int8); Q = np.zeros((n, nb, ne), dtype=np.int8)
    for i in prange(n):
        for b in range(nb):
            for k in range(ne):
                sl = stops[i, b, int(exits[k, 0])]
                if np.isnan(sl): continue
                p, x, kk, q = outcome_r(o, h, l, c, zdn, zup, m0[i], side[i], px[i], sl, exits[k, 1], exits[k, 2], exits[k, 3], int(exits[k, 4]))
                R[i, b, k] = p / sl; X[i, b, k] = x; K[i, b, k] = kk; Q[i, b, k] = q
    return R, X, K, Q


def prepare(m, side, B, ct, trend):
    S = {}
    for tf in SETUP_TFS:
        b = B[tf]; c = b['c']
        mid = sma(c, 20)
        sd = np.sqrt(np.maximum(sma(c * c, 20) - mid * mid, 0))
        pr = pct_rank(4 * sd / mid, 100)
        pr3 = np.minimum(pr, np.minimum(np.r_[100, pr[:-1]], np.r_[100, 100, pr[:-2]]))
        S[tf] = dict(pr3=pr3, hi={n: highest(b['h'], n) for n in BOXES}, lo={n: lowest(b['l'], n) for n in BOXES})
    E = {}
    for etf, stf in TF_COMBOS:
        b = B[etf]; c, o, h, l, v = b['c'], b['o'], b['h'], b['l'], b['v']
        when = ct[etf]
        vr = v / np.r_[np.nan, sma(v, 20)[:-1]]
        a = atr(h, l, c, 14)
        pc = np.r_[c[0], c[:-1]]
        kr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))) / np.r_[np.nan, a[:-1]]
        tbr = np.where(v > 0, b['tb'] / np.where(v > 0, v, 1), 0.5)
        flow = tbr if side == 1 else 1 - tbr
        htf = np.stack([np.ones(len(c), dtype=np.int64), at_close(ct[240], trend[240], when) == side,
                        at_close(ct[1440], trend[1440], when) == side], 1).astype(np.int64)
        j = np.searchsorted(ct[stf], when, side='right') - 1
        jj = np.maximum(j, 0)
        pr3 = np.where(j >= 0, S[stf]['pr3'][jj], 100.0)
        brk = np.zeros((len(c), len(BOXES)), dtype=np.int64)
        stops = np.full((len(c), len(BOXES), len(STOPS)), np.nan)
        prev_c = np.r_[np.nan, c[:-1]]
        for bi, n in enumerate(BOXES):
            hi, lo = S[stf]['hi'][n][jj], S[stf]['lo'][n][jj]
            if side == 1:
                brk[:, bi] = (c > hi) & (prev_c <= hi)
                far, midp = lo, (hi + lo) / 2
            else:
                brk[:, bi] = (c < lo) & (prev_c >= lo)
                far, midp = hi, (hi + lo) / 2
            e = c * (1 + side * SLIP)
            for si, lvl in enumerate((far, midp)):
                d = side * (e - lvl) / e + STOP_BUF
                stops[:, bi, si] = np.where(d >= MIN_STOP, d, np.nan)
        keep = ((when >= T_START) & (np.sign(c - o) == side) & (pr3 <= max(PCTS)) & (brk.max(1) == 1) & (vr >= min(VOLS))
                & (kr >= min(RANGES)))
        i = np.nonzero(keep)[0]
        zl = zlsma(c)
        zdn = np.zeros(len(m['t']), dtype=np.int8); zup = np.zeros(len(m['t']), dtype=np.int8)
        zdn[b['last'][c < zl]] = 1; zup[b['last'][c > zl]] = 1
        d = dict(t=when[i], m0=b['last'][i] + 1, px=c[i], vr=np.nan_to_num(vr[i]), kr=np.nan_to_num(kr[i]), flow=flow[i],
                 htf=htf[i], pr3=pr3[i], brk=brk[i], stops=stops[i], side=np.full(len(i), side, dtype=np.int64))
        d['R'], d['X'], d['K'], d['Q'] = outcomes_r(m['o'], m['h'], m['l'], m['c'], zdn, zup, d['m0'], d['side'], d['px'], d['stops'],
                                                    np.array(EXITS, dtype=np.float64))
        E[(etf, stf)] = d
    return E


@njit(parallel=True, cache=True)
def grid(t, pr3, brk, vr, kr, flow, htf, m0, R, X, K, Q, pcts, vols, ranges, split):
    nb = brk.shape[1]; nh = htf.shape[1]; ne = R.shape[2]; n = len(t)
    np_, nv, nr = len(pcts), len(vols), len(ranges)
    nsig = np_ * nb * nv * nr * 2 * nh
    out = np.zeros((nsig, ne, NC))
    for q in prange(nsig):
        hh = q % nh
        fl = (q // nh) % 2
        rr = (q // (nh * 2)) % nr
        vv = (q // (nh * 2 * nr)) % nv
        bb = (q // (nh * 2 * nr * nv)) % nb
        pp = q // (nh * 2 * nr * nv * nb)
        sel = np.empty(n, dtype=np.int64); cnt = 0
        for i in range(n):
            if pr3[i] > pcts[pp] or brk[i, bb] == 0 or vr[i] < vols[vv] or kr[i] < ranges[rr]: continue
            if fl == 1 and flow[i] < 0.55: continue
            if htf[i, hh] == 0: continue
            sel[cnt] = i; cnt += 1
        for k in range(ne):
            o = out[q, k]
            o[4] = -1e9; o[5] = 1e9
            free = -1; eq = 0.0; peak = 0.0
            for jj in range(cnt):
                i = sel[jj]
                r = R[i, bb, k]
                if np.isnan(r) or m0[i] <= free: continue
                free = X[i, bb, k]
                o[0] += 1
                if r > 0: o[1] += 1; o[2] += r
                else: o[3] -= r
                if r > o[4]: o[4] = r
                if r < o[5]: o[5] = r
                kk = K[i, bb, k]
                if kk >= 1: o[6] += 1
                if kk >= 2: o[7] += 1
                if kk >= 3: o[8] += 1
                qq = Q[i, bb, k]
                if qq == 0: o[9] += 1
                elif qq == 1: o[10] += 1
                elif qq == 2: o[11] += 1
                eq += r
                if eq > peak: peak = eq
                if peak - eq > o[12]: o[12] = peak - eq
                if t[i] < split:
                    o[13] += 1
                    if r > 0: o[14] += r
                    else: o[15] -= r
                else:
                    o[16] += 1
                    if r > 0: o[17] += r
                    else: o[18] -= r
            if o[0] == 0: o[4] = 0; o[5] = 0
    return out


def sig_params(q):
    nh, nr, nv, nb = len(HTFS), len(RANGES), len(VOLS), len(BOXES)
    return dict(pct=PCTS[q // (nh * 2 * nr * nv * nb)], box=BOXES[(q // (nh * 2 * nr * nv)) % nb], vol=VOLS[(q // (nh * 2 * nr)) % nv],
                range=RANGES[(q // (nh * 2)) % nr], flow=FLOWS[(q // nh) % 2], htf=HTFS[q % nh])


def unflat(f):
    ci, rest = divmod(f, NSIG * len(EXITS))
    q, k = divmod(rest, len(EXITS))
    return ci, q, k


def cfg_of(f):
    ci, q, k = unflat(f)
    e, s = TF_COMBOS[ci]
    st, r1, r2, r3, z = EXITS[k]
    return dict(entry=tfname(e), setup=tfname(s), **sig_params(q), stop=STOPS[st], tp1_R=r1, tp2_R=r2, tp3_R=r3, zlsma_exit=ZMODES[z])


def short_desc(c):
    return (f'{c["entry"]}/{c["setup"]} p{c["pct"]} box{c["box"]} v{c["vol"]} k{c["range"]} flow {c["flow"]} HTF {c["htf"]} · '
            f'stop {c["stop"]} · TP {c["tp1_R"]:g}/{c["tp2_R"]:g}/{c["tp3_R"]:g}R · ZL {c["zlsma_exit"]}')


def side_trades(E, f):
    ci, q, k = unflat(f)
    d = E[TF_COMBOS[ci]]; p = sig_params(q)
    bi = BOXES.index(p['box']); hi = HTFS.index(p['htf'])
    ok = ((d['pr3'] <= p['pct']) & (d['brk'][:, bi] == 1) & (d['vr'] >= p['vol']) & (d['kr'] >= p['range'])
          & ((d['flow'] >= 0.55) if p['flow'] != 'off' else True) & (d['htf'][:, hi] == 1) & ~np.isnan(d['R'][:, bi, k]))
    return [(int(d['m0'][j]), int(d['X'][j, bi, k]), int(d['t'][j]), int(d['side'][j]), float(d['px'][j]), float(d['R'][j, bi, k]),
             int(d['K'][j, bi, k]), int(d['Q'][j, bi, k]), round(float(d['stops'][j, bi, EXITS[k][0]]) * 100, 2)) for j in np.nonzero(ok)[0]]


def main():
    t0 = time.time()
    raw = np.load(DATA)
    m = {k: raw[k] for k in ('t', 'o', 'h', 'l', 'c', 'v', 'tb')}
    B = {tf: bars(m, tf) for tf in sorted(set(ENTRY_TFS + SETUP_TFS + [240, 1440]))}
    ct = {tf: B[tf]['t'] + tf * MIN for tf in B}
    trend = {}
    for tf in (240, 1440):
        c = B[tf]['c']; e50, e200 = ema(c, 50), ema(c, 200)
        trend[tf] = np.where((c > e200) & (e50 > e200), 1, np.where((c < e200) & (e50 < e200), -1, 0))
    res = {}
    for side, name in ((1, 'long'), (-1, 'short')):
        E = prepare(m, side, B, ct, trend)
        M = np.zeros((len(TF_COMBOS), NSIG, len(EXITS), NC))
        for ci, combo in enumerate(TF_COMBOS):
            d = E[combo]
            M[ci] = grid(d['t'], d['pr3'], d['brk'], d['vr'], d['kr'], d['flow'], d['htf'], d['m0'], d['R'], d['X'], d['K'], d['Q'],
                         np.array(PCTS, dtype=np.float64), np.array(VOLS), np.array(RANGES), T_SPLIT)
        res[name] = (E, M)
        print(f'{name}: {sum(len(d["t"]) for d in E.values())} candidates, grid done ({time.time() - t0:.0f}s)', flush=True)
    report(res, m)
    print(f'all done ({time.time() - t0:.0f}s)')


def report(res, m):
    L = []; w = L.append
    sides = {}
    for name, (E, M) in res.items():
        flat = M.reshape(-1, NC)
        C = {c: flat[:, j] for j, c in enumerate(COLS)}
        with np.errstate(divide='ignore', invalid='ignore'):
            C['pf_tr'] = np.where(C['train_gl'] > 0, C['train_gw'] / C['train_gl'], np.where(C['train_gw'] > 0, np.inf, np.nan))
            C['pf_te'] = np.where(C['test_gl'] > 0, C['test_gw'] / C['test_gl'], np.where(C['test_gw'] > 0, np.inf, np.nan))
        sides[name] = (E, flat, C)
        with gzip.open(f'{OUT}_{name}.csv.gz', 'wt', newline='') as fh:
            wr = None
            for f in range(len(flat)):
                r = {**cfg_of(f), **{c: round(float(flat[f, j]), 3) for j, c in enumerate(COLS)},
                     'train_pf': round(float(C['pf_tr'][f]), 3), 'test_pf': round(float(C['pf_te'][f]), 3)}
                if wr is None: wr = csv.DictWriter(fh, fieldnames=list(r)); wr.writeheader()
                wr.writerow(r)
    n_side = len(sides['long'][1])
    w('# BTCUSDT.P compression → expansion on larger timeframes, stop on the other side of the box')
    w('')
    w('Generated by `scripts/scalp_squeeze_htf.py`. Binance USDⓈ-M BTCUSDT 1m candles, **train Jan 2023 – Dec 2024, test Jan 2025 – Sep 2026**.')
    w('Same rules as [`scalp_squeeze.md`](scalp_squeeze.md), changed:')
    w('')
    w('| | Tested values |')
    w('|---|---|')
    w('| Entry TF / setup TF | 15m / 30m / 1H · 1H / 2H / 4H (setup larger than entry: 8 combos) |')
    w('| HTF filter | off / 4H / 1D (close > EMA 200 and EMA 50 > EMA 200; mirrored for SHORT) |')
    w('| Stop | the far side of the box (LONG: box low) or the box midpoint, 0.05 % beyond it. Trades whose stop is closer than 0.3 % are skipped |')
    w('| Targets | in R (multiples of the stop distance): TP1 1 / 1.5R, TP2 2 / 2.5 / 3R, TP3 3 / 4 / 5R; close 40 / 35 / 25 %, each only out of what is still open; after TP1 the stop goes to entry + 0.2 %, after TP2 to TP1 |')
    w('| ZLSMA exit | after TP1, or off |')
    w('| Compression / box / volume / range / flow | as before: BBW ≤ p10 / 20 / 30 · box 10 / 20 · volume 1.5 / 2 / 3× · range 1 / 1.5 / 2× ATR · flow off / 55 % |')
    w('')
    w(f'**{n_side:,} rule sets per side.** 1R = the stop distance: at 1 % risk per trade, 1R = 1 % of the account, whatever the stop width.')
    w('')
    w('## Each side on its own')
    w('')
    for name in ('long', 'short'):
        E, flat, C = sides[name]
        ok = C['train_trades'] >= MIN_TR
        w(f'**{name.upper()}** — {ok.sum():,} rule sets with ≥ {MIN_TR} train trades; profitable on train {(ok & (C["pf_tr"] > 1)).sum():,}, '
          f'on test {(ok & (C["pf_te"] > 1)).sum():,}, on both {(ok & (C["pf_tr"] > 1) & (C["pf_te"] > 1)).sum():,}. Median PF train '
          f'{np.nanmedian(C["pf_tr"][ok]):.2f}, test {np.nanmedian(C["pf_te"][ok]):.2f}.')
        w('')
    w('### What each parameter does')
    w('')
    w(f'Median test PF (share with test PF > 1) over all rule sets with that value and ≥ {MIN_TR} train trades.')
    w('')
    allf = np.arange(n_side)
    ci_, rest = np.divmod(allf, NSIG * len(EXITS)); q_, k_ = np.divmod(rest, len(EXITS))
    P = [sig_params(q) for q in range(NSIG)]
    pick = lambda key, vals: np.array([vals.index(P[q][key]) for q in range(NSIG)])[q_]
    dims = [('Entry / setup', ci_, [f'{tfname(e)} / {tfname(s)}' for e, s in TF_COMBOS]),
            ('BBW percentile ≤', pick('pct', PCTS), [f'{p} %' for p in PCTS]), ('Box', pick('box', BOXES), [str(b) for b in BOXES]),
            ('Volume ≥', pick('vol', VOLS), [f'{v}×' for v in VOLS]), ('Range ≥', pick('range', RANGES), [f'{r}× ATR' for r in RANGES]),
            ('Flow', pick('flow', FLOWS), FLOWS), ('HTF filter', pick('htf', HTFS), HTFS),
            ('Stop', np.array([x[0] for x in EXITS])[k_], STOPS), ('TP1', np.array([[1.0, 1.5].index(x[1]) for x in EXITS])[k_], ['1R', '1.5R']),
            ('TP3', np.array([[3.0, 4.0, 5.0].index(x[3]) for x in EXITS])[k_], ['3R', '4R', '5R']),
            ('ZLSMA exit', np.array([x[4] - 1 for x in EXITS])[k_], ['after TP1', 'off'])]
    for title, v, labels in dims:
        w(f'| {title} | LONG trades / yr | LONG test PF | SHORT trades / yr | SHORT test PF |')
        w('|---|---|---|---|---|')
        for j, lab in enumerate(labels):
            cells = []
            for name in ('long', 'short'):
                C = sides[name][2]; msk = (C['train_trades'] >= MIN_TR) & (v == j)
                cells.append(f'{np.median(C["trades"][msk]) / (TRAIN_Y + TEST_Y):.0f} | {np.nanmedian(C["pf_te"][msk]):.2f} '
                             f'({(C["pf_te"][msk] > 1).mean() * 100:.0f} %)' if msk.any() else '— | —')
            w(f'| {lab} | ' + ' | '.join(cells) + ' |')
        w('')
    tops = {}
    for name in ('long', 'short'):
        E, flat, C = sides[name]
        fok = np.nonzero(C['train_trades'] >= MIN_TR)[0]
        order = fok[np.argsort(-np.nan_to_num(C['pf_tr'][fok], nan=-1, posinf=1e9), kind='stable')]
        tops[name] = order
        w(f'### Top 15 {name.upper()} rule sets by train PF (≥ {MIN_TR} train trades)')
        w('')
        w('| # | Rule set | Train trades | Train PF | Test trades | Test PF | Test R | Max DD R |')
        w('|---|---|---|---|---|---|---|---|')
        for r, f in enumerate(order[:15]):
            w(f'| {r + 1} | {short_desc(cfg_of(f))} | {C["train_trades"][f]:.0f} | {C["pf_tr"][f]:.2f} | {C["test_trades"][f]:.0f} | '
              f'{C["pf_te"][f]:.2f} | {C["test_gw"][f] - C["test_gl"][f]:+.1f} | {C["max_dd"][f]:.1f} |')
        w('')
    TOPN = 15
    LT = {f: side_trades(sides['long'][0], f) for f in tops['long'][:TOPN]}
    ST = {f: side_trades(sides['short'][0], f) for f in tops['short'][:TOPN]}
    pairs = []
    for fl, a in LT.items():
        for fs, b in ST.items():
            s = stats(combine(a, b))
            if s: pairs.append((fl, fs, s))
    pairs.sort(key=lambda x: -x[2]['train_pf'])
    w(f'## LONG + SHORT combined ({TOPN} × {TOPN} best per side, one position at a time), ranked by train PF')
    w('')
    w('| # | LONG rule set | SHORT rule set | Trades | Win % | Train PF | Test PF | Test R | Net R | Max DD R |')
    w('|---|---|---|---|---|---|---|---|---|---|')
    for r, (fl, fs, s) in enumerate(pairs[:20]):
        w(f'| {r + 1} | {short_desc(cfg_of(fl))} | {short_desc(cfg_of(fs))} | {s["trades"]} | {s["win_rate"]:.0f} | {s["train_pf"]:.2f} | '
          f'{s["test_pf"]:.2f} | {s["test_net_R"]:+.1f} | {s["net_R"]:+.1f} | {s["max_dd_R"]:.1f} |')
    w('')
    fl, fs, s = pairs[0]
    tr = combine(LT[fl], ST[fs])
    w('### The train pick (#1) in full')
    w('')
    w(f'- LONG: {short_desc(cfg_of(fl))}')
    w(f'- SHORT: {short_desc(cfg_of(fs))}')
    w(f'- Stop distance: median {np.median([x[8] for x in tr]):.2f} %, range {min(x[8] for x in tr):.2f} – {max(x[8] for x in tr):.2f} %')
    w('')
    w('| Metric | Value |'); w('|---|---|')
    for k, v in s.items():
        w(f'| {k} | {v} |')
    w('')
    with open(f'{OUT}.md', 'w') as fh:
        fh.write('\n'.join(L) + '\n')

    cfgs = []
    for fl, fs, s in pairs[:60]:
        tr = combine(LT[fl], ST[fs])
        cfgs.append(dict(cfg=dict(long=short_desc(cfg_of(fl)), short=short_desc(cfg_of(fs))), m=s, g=['train'],
                         trades=[[x[2], x[3], x[8], round(x[4], 1), int(m['t'][x[1]] + MIN), round(x[5], 3), x[6], x[7]] for x in tr]))
    tpl = open(os.path.join(os.path.dirname(__file__), 'scalp_btc_template.html')).read()
    rep = [
        ("<title>BTC Scalper Grid</title>", "<title>BTC Squeeze HTF</title>"),
        ("<h1>BTCUSDT.P LONG / SHORT score scalper</h1>", "<h1>BTCUSDT.P compression → expansion · larger timeframes · stop beyond the box</h1>"),
        ("<th>Score</th>", "<th>Stop %</th>"),
        ("let sortK = 11,", "let sortK = 6,"),
        ('<option value="train">best by train PF</option>', ''),
        ('<option value="busy">best with ≥ 100 trades / year</option>\n        <option value="test">best by test PF (hindsight)</option>\n', ''),
    ]
    for a, b in rep:
        assert a in tpl, a[:50]; tpl = tpl.replace(a, b)
    a0 = tpl.index("$('#lede').textContent ="); a1 = tpl.index("\n\nconst COLS")
    tpl = tpl[:a0] + ("$('#lede').textContent = `${D.total.toLocaleString('en')} rule sets per side backtested LONG-only and SHORT-only on Binance BTCUSDT 1m candles, Jan 2023 – Sep 2026. ` +\n"
                      "  `Listed: the ${C.length} best pairs of a LONG and a SHORT rule set (from the 15 best of each side on 2023 – 2024), trading together one position at a time. ${nProf} of them made money in both periods.`;") + tpl[a1:]
    a0 = tpl.index("['Entry / setup / HTF'"); a1 = tpl.index("  ['Trades', c => c.m.trades]")
    tpl = tpl[:a0] + "['LONG rule set', c => c.cfg.long, 'l'], ['SHORT rule set', c => c.cfg.short, 'l'],\n" + tpl[a1:]
    a0 = tpl.index("  $('#dtitle').innerHTML ="); a1 = tpl.index("\n", a0)
    tpl = tpl[:a0] + "  $('#dtitle').innerHTML = `${g.long}<br>${g.short}`;" + tpl[a1:]
    with open(f'{OUT}.html', 'w') as fh:
        fh.write(tpl.replace('/*DATA*/null', json.dumps(dict(configs=cfgs, reasons=REASONS, split=T_SPLIT, total=n_side), separators=(',', ':'))))
    print('wrote', f'{OUT}.md', f'{OUT}.html')


if __name__ == '__main__':
    main()
