"""BTCUSDT.P compression -> expansion scalper: one symmetric rule set, separate LONG and SHORT thresholds.

    compression  on the setup TF (15m / 30m / 1H): Bollinger Band width (20, 2) is in the lowest p % of its last 100
                 candles, now or within the 2 candles before (p = 10 / 20 / 30)
    box          the high / low of the last N closed setup candles (N = 10 / 20): the range the market was coiled in
    expansion    a closed entry-TF candle (3m / 5m / 15m) is the FIRST to close above the box high (LONG) / below the box
                 low (SHORT), closes in that direction, and
                   volume     >= v x the average of the 20 candles before (v = 1.5 / 2 / 3)
                   range      true range >= k x ATR(14) (k = 1.0 / 1.5 / 2.0)
                   flow       taker buys >= 55 % of the volume (SHORT: taker sells >= 55 %), or off
    HTF filter   off, or the 1H / 4H trend agrees (close > EMA200 and EMA50 > EMA200; mirrored for SHORT)
    exits        SL 0.5 / 0.7 / 1 %; TP1 / TP2 / TP3 close 40 / 35 / 25 % (each only out of what is left; 16 ladders);
                 after TP1 stop to entry + 0.2 %, after TP2 to TP1; ZLSMA exit after TP1 or off.
                 Same fills and costs as scripts/scalp_btc.py (outcome()).

LONG and SHORT are optimised separately (each threshold has its own LONG and SHORT value): every rule set is first
backtested LONG-only and SHORT-only (2 x 248,832 configs), then the 15 best LONG and 15 best SHORT rule sets on the
training years (2023-24) are combined pairwise (one position at a time) and checked on Jan 2025 - Sep 2026.

    python3 scripts/scalp_squeeze.py  -> backtests/scalp_squeeze.md, backtests/scalp_squeeze_{long,short}.csv.gz,
                                         backtests/scalp_squeeze.html
"""
import csv, gzip, json, os, sys, time

import numpy as np
from numba import njit, prange

sys.path.insert(0, os.path.dirname(__file__))
from atlas_score import ema, atr, sma, highest, lowest  # noqa: E402
from scalp_btc import bars, zlsma, outcomes, at_close, pf, REASONS, DATA, MIN, T_START, T_SPLIT, TRAIN_Y, TEST_Y  # noqa: E402

OUT = os.path.join('backtests', 'scalp_squeeze')
ENTRY_TFS = [3, 5, 15]
SETUP_TFS = [15, 30, 60]
TF_COMBOS = [(e, s) for e in ENTRY_TFS for s in SETUP_TFS if e < s]
PCTS = [10, 20, 30]
BOXES = [10, 20]
VOLS = [1.5, 2.0, 3.0]
RANGES = [1.0, 1.5, 2.0]
FLOWS = ['off', '55%']
HTFS = ['off', '1H', '4H']
ZMODES = ['always', 'after TP1', 'off']
EXITS = [(sl, t1, t2, t3, z) for z in (1, 2) for sl in (0.5, 0.7, 1.0) for t1 in (1.0, 1.5) for t2 in (2.0, 2.5, 3.0)
         for t3 in (3.0, 4.0, 5.0) if t1 < t2 < t3]
NSIG = len(PCTS) * len(BOXES) * len(VOLS) * len(RANGES) * len(FLOWS) * len(HTFS)
COLS = ['trades', 'wins', 'gross_win', 'gross_loss', 'best', 'worst', 'tp1', 'tp2', 'tp3', 'sl', 'be_stop', 'zlsma', 'max_dd',
        'train_trades', 'train_gw', 'train_gl', 'test_trades', 'test_gw', 'test_gl']
NC = len(COLS)
MIN_TR = 30  # train trades per side


@njit(cache=True)
def pct_rank(x, n):
    """Share (%) of the last n values (incl. this one) that are <= this one."""
    out = np.full(len(x), 100.0)
    for i in range(n - 1, len(x)):
        if np.isnan(x[i]): continue
        c = 0
        for k in range(i - n + 1, i + 1):
            if x[k] <= x[i]: c += 1
        out[i] = 100.0 * c / n
    return out


def prepare(m, side):
    """Candidate entry candles for one side, with their features and outcomes for every exit."""
    B = {tf: bars(m, tf) for tf in sorted(set(ENTRY_TFS + SETUP_TFS + [60, 240]))}
    ct = {tf: B[tf]['t'] + tf * MIN for tf in B}
    trend = {}
    for tf in (60, 240):
        c = B[tf]['c']; e50, e200 = ema(c, 50), ema(c, 200)
        trend[tf] = np.where((c > e200) & (e50 > e200), 1, np.where((c < e200) & (e50 < e200), -1, 0))
    S = {}
    for tf in SETUP_TFS:
        b = B[tf]; c = b['c']
        mid = sma(c, 20)
        sd = np.sqrt(np.maximum(sma(c * c, 20) - mid * mid, 0))
        bbw = 4 * sd / mid
        pr = pct_rank(bbw, 100)
        pr3 = np.minimum(pr, np.minimum(np.r_[100, pr[:-1]], np.r_[100, 100, pr[:-2]]))
        S[tf] = dict(pr3=pr3, hi={n: highest(b['h'], n) for n in BOXES}, lo={n: lowest(b['l'], n) for n in BOXES})
    E = {}
    for etf in ENTRY_TFS:
        b = B[etf]; c, o, h, l, v = b['c'], b['o'], b['h'], b['l'], b['v']
        when = ct[etf]
        vr = v / np.r_[np.nan, sma(v, 20)[:-1]]
        a = atr(h, l, c, 14)
        tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
        kr = tr / np.r_[np.nan, a[:-1]]          # this candle's true range vs the ATR before it
        tbr = np.where(v > 0, b['tb'] / np.where(v > 0, v, 1), 0.5)
        flow = tbr if side == 1 else 1 - tbr
        dirn = np.sign(c - o) == side
        prev_c = np.r_[np.nan, c[:-1]]
        htf = np.stack([np.ones(len(c), dtype=np.int64), at_close(ct[60], trend[60], when) == side,
                        at_close(ct[240], trend[240], when) == side], 1).astype(np.int64)
        per = {}
        for stf in SETUP_TFS:
            if stf <= etf: continue
            j = np.searchsorted(ct[stf], when, side='right') - 1
            jj = np.maximum(j, 0)
            pr3 = np.where(j >= 0, S[stf]['pr3'][jj], 100.0)
            brk = np.zeros((len(c), len(BOXES)), dtype=np.int64)
            for bi, n in enumerate(BOXES):
                lvl = S[stf]['hi'][n][jj] if side == 1 else S[stf]['lo'][n][jj]
                if side == 1: brk[:, bi] = (c > lvl) & (prev_c <= lvl)
                else: brk[:, bi] = (c < lvl) & (prev_c >= lvl)
            keep = ((when >= T_START) & dirn & (pr3 <= max(PCTS)) & (brk.max(1) == 1) & (vr >= min(VOLS)) & (kr >= min(RANGES)))
            per[stf] = dict(i=np.nonzero(keep)[0], pr3=pr3, brk=brk)
        idx = np.unique(np.concatenate([p['i'] for p in per.values()]))
        zl = zlsma(c)
        zdn = np.zeros(len(m['t']), dtype=np.int8); zup = np.zeros(len(m['t']), dtype=np.int8)
        zdn[b['last'][c < zl]] = 1; zup[b['last'][c > zl]] = 1
        d = dict(t=when[idx], m0=b['last'][idx] + 1, px=c[idx], vr=np.nan_to_num(vr[idx]), kr=np.nan_to_num(kr[idx]),
                 flow=flow[idx], htf=htf[idx], side=np.full(len(idx), side, dtype=np.int64))
        for stf, p in per.items():
            d[f'pr3_{stf}'] = p['pr3'][idx]; d[f'brk_{stf}'] = p['brk'][idx]
        d['R'], d['X'], d['K'], d['Q'] = outcomes(m['o'], m['h'], m['l'], m['c'], zdn, zup, d['m0'], d['side'], d['px'], np.array(EXITS))
        E[etf] = d
    return E


@njit(parallel=True, cache=True)
def grid(t, pr3, brk, vr, kr, flow, htf, m0, R, X, K, Q, pcts, vols, ranges, split):
    nb = brk.shape[1]; nh = htf.shape[1]; ne = R.shape[1]; n = len(t)
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
                if m0[i] <= free: continue
                free = X[i, k]
                r = R[i, k]
                o[0] += 1
                if r > 0: o[1] += 1; o[2] += r
                else: o[3] -= r
                if r > o[4]: o[4] = r
                if r < o[5]: o[5] = r
                kk = K[i, k]
                if kk >= 1: o[6] += 1
                if kk >= 2: o[7] += 1
                if kk >= 3: o[8] += 1
                qq = Q[i, k]
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
    hh = q % nh; q //= nh
    fl = q % 2; q //= 2
    rr = q % nr; q //= nr
    vv = q % nv; q //= nv
    bb = q % nb; q //= nb
    return dict(pct=PCTS[q], box=BOXES[bb], vol=VOLS[vv], range=RANGES[rr], flow=FLOWS[fl], htf=HTFS[hh])


def describe(p, side):
    s = 'LONG' if side == 1 else 'SHORT'
    return (f'{s}: BBW ≤ p{p["pct"]} · box {p["box"]} · vol ≥ {p["vol"]}× · range ≥ {p["range"]}× ATR · flow {p["flow"]} · HTF {p["htf"]}')


def side_trades(E, combo, p, k):
    e, s = combo
    d = E[e]
    bi = BOXES.index(p['box']); hi = HTFS.index(p['htf'])
    ok = ((d[f'pr3_{s}'] <= p['pct']) & (d[f'brk_{s}'][:, bi] == 1) & (d['vr'] >= p['vol']) & (d['kr'] >= p['range'])
          & ((d['flow'] >= 0.55) if p['flow'] != 'off' else True) & (d['htf'][:, hi] == 1))
    i = np.nonzero(ok)[0]
    return [(int(d['m0'][j]), int(d['X'][j, k]), int(d['t'][j]), int(d['side'][j]), float(d['px'][j]), float(d['R'][j, k]),
             int(d['K'][j, k]), int(d['Q'][j, k])) for j in i]


def combine(a, b):
    """Merge two candidate lists (LONG + SHORT) with one position at a time."""
    out = []; free = -1
    for x in sorted(a + b):
        if x[0] <= free: continue
        free = x[1]; out.append(x)
    return out


def stats(tr):
    R = np.array([x[5] for x in tr]); t = np.array([x[2] for x in tr]); side = np.array([x[3] for x in tr])
    K = np.array([x[6] for x in tr]); Q = np.array([x[7] for x in tr])
    if not len(R): return None
    eq = np.cumsum(R); dd = (np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()
    def part(msk):
        r = R[msk]; gw = r[r > 0].sum(); gl = -r[r <= 0].sum()
        return len(r), (r > 0).mean() * 100 if len(r) else 0, pf(gw, gl), gw - gl
    tr_n, _, tr_pf, tr_r = part(t < T_SPLIT); te_n, _, te_pf, te_r = part(t >= T_SPLIT)
    n, wr, pff, net = part(np.ones(len(R), bool)); ln, lwr, lpf, lnet = part(side == 1); sn, swr, spf, snet = part(side == -1)
    r3 = lambda x: round(float(x), 3)
    return dict(trades=int(n), longs=int(ln), shorts=int(sn), win_rate=round(wr, 1), pf=r3(pff), net_R=round(float(net), 2),
                max_dd_R=round(float(dd), 2), avg_R=round(float(net / n), 4), best_R=r3(R.max()), worst_R=r3(R.min()),
                tp1_hits=int((K >= 1).sum()), tp2_hits=int((K >= 2).sum()), tp3_hits=int((K >= 3).sum()),
                sl_hits=int((Q == 0).sum()), be_stop=int((Q == 1).sum()), zlsma=int((Q == 2).sum()),
                long_win_rate=round(lwr, 1), long_pf=r3(lpf), long_net_R=round(float(lnet), 2),
                short_win_rate=round(swr, 1), short_pf=r3(spf), short_net_R=round(float(snet), 2),
                train_trades=int(tr_n), train_pf=r3(tr_pf), train_net_R=round(float(tr_r), 2),
                test_trades=int(te_n), test_pf=r3(te_pf), test_net_R=round(float(te_r), 2))


def main():
    t0 = time.time()
    raw = np.load(DATA)
    m = {k: raw[k] for k in ('t', 'o', 'h', 'l', 'c', 'v', 'tb')}
    res = {}
    for side, name in ((1, 'long'), (-1, 'short')):
        E = prepare(m, side)
        M = np.zeros((len(TF_COMBOS), NSIG, len(EXITS), NC))
        for ci, (e, s) in enumerate(TF_COMBOS):
            d = E[e]
            M[ci] = grid(d['t'], d[f'pr3_{s}'], d[f'brk_{s}'], d['vr'], d['kr'], d['flow'], d['htf'], d['m0'], d['R'], d['X'],
                         d['K'], d['Q'], np.array(PCTS, dtype=np.float64), np.array(VOLS), np.array(RANGES), T_SPLIT)
        res[name] = (E, M)
        print(f'{name}: {sum(len(d["t"]) for d in E.values())} candidates, grid done ({time.time() - t0:.0f}s)', flush=True)
    report(res, m)
    print(f'all done ({time.time() - t0:.0f}s)')


def side_table(M):
    """Flat per-config arrays and descriptors for one side."""
    flat = M.reshape(-1, NC)
    C = {c: flat[:, j] for j, c in enumerate(COLS)}
    with np.errstate(divide='ignore', invalid='ignore'):
        C['pf_tr'] = np.where(C['train_gl'] > 0, C['train_gw'] / C['train_gl'], np.where(C['train_gw'] > 0, np.inf, np.nan))
        C['pf_te'] = np.where(C['test_gl'] > 0, C['test_gw'] / C['test_gl'], np.where(C['test_gw'] > 0, np.inf, np.nan))
        C['pf_all'] = np.where(C['gross_loss'] > 0, C['gross_win'] / C['gross_loss'], np.nan)
    return flat, C


def unflat(f):
    ci, rest = divmod(f, NSIG * len(EXITS))
    q, k = divmod(rest, len(EXITS))
    return ci, q, k


def cfg_of(f):
    ci, q, k = unflat(f)
    e, s = TF_COMBOS[ci]
    sl, t1, t2, t3, z = EXITS[k]
    return dict(entry=f'{e}m', setup=f'{s}m' if s < 60 else '1H', **sig_params(q), sl_pct=sl, tp1_pct=t1, tp2_pct=t2, tp3_pct=t3,
                zlsma_exit=ZMODES[z])


def report(res, m):
    L = []; w = L.append
    sides = {}
    for name, (E, M) in res.items():
        flat, C = side_table(M)
        sides[name] = (E, flat, C)
        with gzip.open(f'{OUT}_{name}.csv.gz', 'wt', newline='') as fh:
            wr = None
            for f in range(len(flat)):
                r = {**cfg_of(f), **{c: round(float(flat[f, j]), 3) for j, c in enumerate(COLS)},
                     'train_pf': round(float(C['pf_tr'][f]), 3), 'test_pf': round(float(C['pf_te'][f]), 3)}
                if wr is None: wr = csv.DictWriter(fh, fieldnames=list(r)); wr.writeheader()
                wr.writerow(r)
    n_side = len(sides['long'][1])
    w('# BTCUSDT.P compression → expansion scalper')
    w('')
    w('Generated by `scripts/scalp_squeeze.py`. Binance USDⓈ-M BTCUSDT 1m candles, **train Jan 2023 – Dec 2024, test Jan 2025 – Sep 2026**.')
    w('')
    w('## Rules (the same for LONG and SHORT, mirrored; every threshold has its own LONG and SHORT value)')
    w('')
    w('| Step | Rule (LONG; SHORT mirrored) | Tested values |')
    w('|---|---|---|')
    w('| Compression (setup TF) | Bollinger Band width (20, 2) in the lowest p % of its last 100 candles, now or in the 2 candles before | setup 15m / 30m / 1H · p 10 / 20 / 30 % |')
    w('| Box | high / low of the last N closed setup candles | N 10 / 20 |')
    w('| Expansion (entry TF) | the first entry candle to close above the box high, green | entry 3m / 5m / 15m (smaller than setup) |')
    w('| — volume | volume ≥ v × average of the 20 candles before | v 1.5 / 2 / 3 |')
    w('| — range | true range ≥ k × ATR 14 | k 1.0 / 1.5 / 2.0 |')
    w('| — flow | taker buys ≥ 55 % of the candle\'s volume (SHORT: taker sells) | off / on |')
    w('| HTF filter | 1H or 4H: close > EMA 200 and EMA 50 > EMA 200 | off / 1H / 4H |')
    w('| Exits | SL; TP1 / TP2 / TP3 close 40 / 35 / 25 % (each only out of what is still open); after TP1 stop to entry + 0.2 %, after TP2 to TP1; ZLSMA 32 exit on an entry candle close | SL 0.5 / 0.7 / 1 % · 16 TP ladders · ZLSMA after TP1 / off |')
    w('')
    w(f'**{n_side:,} rule sets per side**, each backtested LONG-only and SHORT-only. Market entries at the candle close (0.055 % taker')
    w('+ 0.02 % slippage), stops taker + slippage (at the open when price gaps through), targets 0.02 % maker on a touch; replayed on 1m')
    w('candles, stop first. 1R = the SL distance (= 1 % of the account at 1 % risk). One position at a time.')
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
    # parameter effects per side
    w('### What each parameter does')
    w('')
    w(f'Median test PF (share with test PF > 1) over all rule sets with that value and ≥ {MIN_TR} train trades.')
    w('')
    idx = {}
    allf = np.arange(n_side)
    ci_, rest = np.divmod(allf, NSIG * len(EXITS)); q_, k_ = np.divmod(rest, len(EXITS))
    P = [sig_params(q) for q in range(NSIG)]
    dims = [('Entry / setup', lambda: ci_, [f'{e}m / {s}m' if s < 60 else f'{e}m / 1H' for e, s in TF_COMBOS]),
            ('BBW percentile ≤', lambda: np.array([PCTS.index(P[q]['pct']) for q in range(NSIG)])[q_], [f'{p} %' for p in PCTS]),
            ('Box', lambda: np.array([BOXES.index(P[q]['box']) for q in range(NSIG)])[q_], [str(b) for b in BOXES]),
            ('Volume ≥', lambda: np.array([VOLS.index(P[q]['vol']) for q in range(NSIG)])[q_], [f'{v}×' for v in VOLS]),
            ('Range ≥', lambda: np.array([RANGES.index(P[q]['range']) for q in range(NSIG)])[q_], [f'{r}× ATR' for r in RANGES]),
            ('Flow', lambda: np.array([FLOWS.index(P[q]['flow']) for q in range(NSIG)])[q_], FLOWS),
            ('HTF filter', lambda: np.array([HTFS.index(P[q]['htf']) for q in range(NSIG)])[q_], HTFS),
            ('SL', lambda: np.array([[0.5, 0.7, 1.0].index(x[0]) for x in EXITS])[k_], ['0.5 %', '0.7 %', '1 %']),
            ('TP1', lambda: np.array([[1.0, 1.5].index(x[1]) for x in EXITS])[k_], ['1 %', '1.5 %']),
            ('ZLSMA exit', lambda: np.array([x[4] - 1 for x in EXITS])[k_], ['after TP1', 'off'])]
    for title, f_, labels in dims:
        v = f_()
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
    # top per side
    tops = {}
    for name in ('long', 'short'):
        E, flat, C = sides[name]
        fok = np.nonzero(C['train_trades'] >= MIN_TR)[0]
        order = fok[np.argsort(-np.nan_to_num(C['pf_tr'][fok], nan=-1, posinf=1e9), kind='stable')]
        tops[name] = order
        w(f'### Top 15 {name.upper()} rule sets by train PF (≥ {MIN_TR} train trades)')
        w('')
        w('| # | Entry / setup | BBW ≤ | Box | Vol | Range | Flow | HTF | SL | TP1 / 2 / 3 | ZLSMA | Train trades | Train PF | Test trades | Test PF | Test R |')
        w('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for r, f in enumerate(order[:15]):
            c = cfg_of(f)
            w(f'| {r + 1} | {c["entry"]} / {c["setup"]} | p{c["pct"]} | {c["box"]} | {c["vol"]}× | {c["range"]}× | {c["flow"]} | {c["htf"]} | '
              f'{c["sl_pct"]:g} % | {c["tp1_pct"]:g} / {c["tp2_pct"]:g} / {c["tp3_pct"]:g} % | {c["zlsma_exit"]} | {C["train_trades"][f]:.0f} | '
              f'{C["pf_tr"][f]:.2f} | {C["test_trades"][f]:.0f} | {C["pf_te"][f]:.2f} | {C["test_gw"][f] - C["test_gl"][f]:+.1f} |')
        w('')

    # combine: best LONG x best SHORT on train, one position at a time
    def trades_of(name, f):
        E = sides[name][0]; ci, q, k = unflat(f)
        return side_trades(E, TF_COMBOS[ci], sig_params(q), k)
    TOPN = 15
    LT = {f: trades_of('long', f) for f in tops['long'][:TOPN]}
    ST = {f: trades_of('short', f) for f in tops['short'][:TOPN]}
    pairs = []
    for fl, a in LT.items():
        for fs, b in ST.items():
            s = stats(combine(a, b))
            if s: pairs.append((fl, fs, s))
    pairs.sort(key=lambda x: -x[2]['train_pf'])
    w(f'## LONG + SHORT combined ({TOPN} × {TOPN} best rule sets per side, one position at a time), ranked by train PF')
    w('')
    w('| # | LONG rule set | SHORT rule set | Trades | Win % | Train PF | Test PF | Test R | Net R | Max DD R |')
    w('|---|---|---|---|---|---|---|---|---|---|')
    def short_desc(c):
        return (f'{c["entry"]}/{c["setup"]} p{c["pct"]} box{c["box"]} v{c["vol"]} k{c["range"]} flow {c["flow"]} HTF {c["htf"]} · '
                f'SL {c["sl_pct"]:g} TP {c["tp1_pct"]:g}/{c["tp2_pct"]:g}/{c["tp3_pct"]:g} ZL {c["zlsma_exit"]}')
    for r, (fl, fs, s) in enumerate(pairs[:20]):
        w(f'| {r + 1} | {short_desc(cfg_of(fl))} | {short_desc(cfg_of(fs))} | {s["trades"]} | {s["win_rate"]:.0f} | {s["train_pf"]:.2f} | '
          f'{s["test_pf"]:.2f} | {s["test_net_R"]:+.1f} | {s["net_R"]:+.1f} | {s["max_dd_R"]:.1f} |')
    w('')
    fl, fs, s = pairs[0]
    w('### The train pick (#1) in full')
    w('')
    w(f'- {describe(cfg_of(fl), 1)} · {cfg_of(fl)["entry"]} entry / {cfg_of(fl)["setup"]} setup · SL {cfg_of(fl)["sl_pct"]:g} % · TP '
      f'{cfg_of(fl)["tp1_pct"]:g} / {cfg_of(fl)["tp2_pct"]:g} / {cfg_of(fl)["tp3_pct"]:g} % · ZLSMA {cfg_of(fl)["zlsma_exit"]}')
    w(f'- {describe(cfg_of(fs), -1)} · {cfg_of(fs)["entry"]} entry / {cfg_of(fs)["setup"]} setup · SL {cfg_of(fs)["sl_pct"]:g} % · TP '
      f'{cfg_of(fs)["tp1_pct"]:g} / {cfg_of(fs)["tp2_pct"]:g} / {cfg_of(fs)["tp3_pct"]:g} % · ZLSMA {cfg_of(fs)["zlsma_exit"]}')
    w('')
    w('| Metric | Value |'); w('|---|---|')
    for k, v in s.items():
        w(f'| {k} | {v} |')
    w('')
    with open(f'{OUT}.md', 'w') as fh:
        fh.write('\n'.join(L) + '\n')

    # HTML explorer: the combined pairs
    cfgs = []
    for r, (fl, fs, s) in enumerate(pairs[:60]):
        tr = combine(LT[fl], ST[fs])
        cl, cs = cfg_of(fl), cfg_of(fs)
        cfgs.append(dict(cfg=dict(long=short_desc(cl), short=short_desc(cs)), m=s, g=['train'],
                         trades=[[x[2], x[3], '', round(x[4], 1), int(m['t'][x[1]] + MIN), round(x[5], 3), x[6], x[7]] for x in tr]))
    tpl = open(os.path.join(os.path.dirname(__file__), 'scalp_btc_template.html')).read()
    rep = [
        ("<title>BTC Scalper Grid</title>", "<title>BTC Squeeze Scalper</title>"),
        ("<h1>BTCUSDT.P LONG / SHORT score scalper</h1>", "<h1>BTCUSDT.P compression → expansion scalper</h1>"),
        ("$('#lede').textContent = `${D.total.toLocaleString('en')} configurations backtested on Binance BTCUSDT 1m candles, Jan 2023 – Sep 2026. ` +\n"
         "  `This page lists the ${C.length} best by train profit factor, by train profit factor with at least 100 trades a year, and by test profit factor; ` +\n"
         "  `all ${D.total.toLocaleString('en')} are in scalp_btc_all.csv.gz. ${nProf} of the listed configurations made money in both periods.`;",
         "$('#lede').textContent = `${D.total.toLocaleString('en')} rule sets per side backtested LONG-only and SHORT-only on Binance BTCUSDT 1m candles, Jan 2023 – Sep 2026. ` +\n"
         "  `Listed: the ${C.length} best pairs of a LONG and a SHORT rule set (from the 15 best of each side on 2023 – 2024), trading together one position at a time. ${nProf} of them made money in both periods.`;"),
    ]
    for a, b in rep:
        assert a in tpl, a[:50]; tpl = tpl.replace(a, b)
    a0 = tpl.index("['Entry / setup / HTF'"); a1 = tpl.index("  ['Trades', c => c.m.trades]")
    tpl = tpl[:a0] + "['LONG rule set', c => c.cfg.long, 'l'], ['SHORT rule set', c => c.cfg.short, 'l'],\n" + tpl[a1:]
    a0 = tpl.index("  $('#dtitle').innerHTML ="); a1 = tpl.index("\n", a0)
    tpl = tpl[:a0] + "  $('#dtitle').innerHTML = `${g.long}<br>${g.short}`;" + tpl[a1:]
    tpl = tpl.replace("let sortK = 11,", "let sortK = 6,").replace("<th>Score</th>", "<th></th>")
    tpl = tpl.replace('<option value="busy">best with ≥ 100 trades / year</option>\n        <option value="test">best by test PF (hindsight)</option>\n', '')
    tpl = tpl.replace('<option value="train">best by train PF</option>', '')
    with open(f'{OUT}.html', 'w') as fh:
        fh.write(tpl.replace('/*DATA*/null', json.dumps(dict(configs=cfgs, reasons=REASONS, split=T_SPLIT, total=n_side), separators=(',', ':'))))
    print('wrote', f'{OUT}.md', f'{OUT}.html')


if __name__ == '__main__':
    main()
