"""Which chance of the 3-day reading comes true most: the page's reading (3 days, targets at least 0.3 x the expected
range away) on the Binance archive, 2022 (or the listing) - Sep 2026, every 6 hours, grouped by the chance shown.

For each group: how often the more likely target was reached first (of the times one was reached within 3 days), how
often neither was, and the result of a trade toward it with the stop at the other target (closed after 3 days if
neither; 0.1% round-trip cost). Also from a minimum chance up (55%, 60%, ... 80%), as a buy / sell signal would use it.
Other stops (alt): take profit at the more likely target with the stop at 0.5, 0.75, 1 or 1.5 x the target's distance
instead of at the other target (closed after 3 days if neither; a candle touching both counts as the stop).

    python3 scripts/glasssphere_threshold_test.py [cache_dir] --symbol BTCUSDT [--days 1|3|7] [--json OUT]
Uses the archive cache of glasssphere_backtest_long.py; prints a table and, with --json, writes the numbers.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glasssphere_backtest_long as L  # noqa: E402  (reads --symbol and the cache dir from the command line)
import glasssphere_model as M  # noqa: E402

DAYS = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 3     # the reading's horizon
LO, STEP, FEE = 0.3, 6, L.FEE
STOPS = [0.5, 0.75, 1.0, 1.5]                      # stop distance as a multiple of the target distance
# your own take profit and stop, both as a multiple of the distance to the more likely target
GRID = [(tp, sl) for tp in (0.5, 0.75, 1.0) for sl in (0.5, 1.0, 1.5, 2.0)]
# levels as take profit and stop: the take profit just before the target cluster (tp x its distance), the stop 0.2% beyond
# the nearest strong cluster on the other side (pool weight at least s, at least 0.15 x the expected range away)
LEVELS = [(tp, sw) for tp in (0.9, 1.0) for sw in (0.3, 0.6, 1.0)]
BUCKETS = [(50, 55), (55, 60), (60, 65), (65, 70), (70, 75), (75, 80), (80, 101)]
MINS = [55, 60, 65, 70, 75, 80]


def points(candles, stats):
    book = L.M.LiqBook('taker')
    out, n = [], len(candles)
    for k, c in enumerate(candles):
        t = c[0] // 1000
        a, b = stats.get(str(t)), stats.get(str(t + 3600))
        book.step(k, c, a and a['oi'], b and b['oi'], a['taker'] / (1 + a['taker']) if a else None)
        if k < M.WINDOW or k % STEP or k + 24 * DAYS >= n:
            continue
        win = candles[k - M.WINDOW + 1:k + 1]
        P = c[4]
        pl = M.pools(M.estimate_stops(win, P), [(q[0], q[1], q[2]) for q in book.pos], P, rng=0.15)
        rh = M.r24_of(win) * DAYS ** 0.5
        ups = [q for q in pl if P + LO * rh < q[0] <= P + rh]
        dns = [q for q in pl if P - rh <= q[0] < P - LO * rh]
        if not ups or not dns:
            continue
        tu, td = max(ups, key=lambda q: q[1])[0], max(dns, key=lambda q: q[1])[0]
        p_up = (P - td) / (tu - td)
        go_up = p_up >= 0.5
        first = None
        for x in candles[k + 1:k + 1 + 24 * DAYS]:
            if x[2] >= tu or x[3] <= td:
                first = 'both' if x[2] >= tu and x[3] <= td else 'up' if x[2] >= tu else 'down'
                break
        tgt, stp = (tu / P - 1, 1 - td / P) if go_up else (1 - td / P, tu / P - 1)
        if first in ('up', 'down'):
            pnl = tgt if (first == 'up') == go_up else -stp
        elif first == 'both':
            pnl = -stp                                    # same candle: assume the stop came first
        else:
            pnl = (candles[k + 24 * DAYS][4] / P - 1) * (1 if go_up else -1)
        path, end = candles[k + 1:k + 1 + 24 * DAYS], candles[k + 24 * DAYS][4] / P - 1
        alt = {}
        for m in STOPS:                                   # take profit at the target, stop at m x its distance
            r = None
            for x in path:
                fav, adv = (x[2] / P - 1, 1 - x[3] / P) if go_up else (1 - x[3] / P, x[2] / P - 1)
                if adv >= m * tgt:
                    r = -m * tgt; break
                if fav >= tgt:
                    r = tgt; break
            alt[str(m)] = (r if r is not None else end * (1 if go_up else -1)) - FEE
        grid = {}
        for tp, sl in GRID:                               # [result, 'tp' / 'sl' / 'time']
            r = None
            for x in path:
                fav, adv = (x[2] / P - 1, 1 - x[3] / P) if go_up else (1 - x[3] / P, x[2] / P - 1)
                if adv >= sl * tgt:
                    r = [-sl * tgt, 'sl']; break
                if fav >= tp * tgt:
                    r = [tp * tgt, 'tp']; break
            r = r or [end * (1 if go_up else -1), 'time']
            grid[f'{tp}/{sl}'] = [r[0] - FEE, r[1]]
        lv = {}
        for tpf, sw in LEVELS:
            opp = [q for q in pl if q[1] >= sw and ((q[0] < P - 0.15 * rh) if go_up else (q[0] > P + 0.15 * rh))]
            sd = (abs(P - min(opp, key=lambda q: abs(q[0] - P))[0]) / P + 0.002) if opp else stp
            r = None
            for x in path:
                fav, adv = (x[2] / P - 1, 1 - x[3] / P) if go_up else (1 - x[3] / P, x[2] / P - 1)
                if adv >= sd:
                    r = [-sd, 'sl']; break
                if fav >= tpf * tgt:
                    r = [tpf * tgt, 'tp']; break
            r = r or [end * (1 if go_up else -1), 'time']
            lv[f'{tpf}/{sw}'] = [r[0] - FEE, r[1], sd]
        how = 'tp' if first in ('up', 'down') and (first == 'up') == go_up else 'sl' if first else 'time'
        out.append({'pct': round(max(p_up, 1 - p_up) * 100), 'first': first, 'right': first in ('up', 'down') and (first == 'up') == go_up,
                    'tgt': tgt, 'stp': stp, 'pnl': pnl - FEE, 'alt': alt, 'grid': grid, 'lv': lv, 'how': how})
    return out


def stats_of(rs):
    if not rs:
        return None
    hit = [r for r in rs if r['first']]
    m = sum(r['pnl'] for r in rs) / len(rs)
    sd = (sum((r['pnl'] - m) ** 2 for r in rs) / len(rs)) ** 0.5
    neff = max(len(rs) * STEP / (24 * DAYS), 2)          # overlapping 3-day windows: about one independent per 3 days
    med = lambda xs: sorted(xs)[len(xs) // 2]
    alt = {}
    for k in rs[0]['alt']:
        xs = [r['alt'][k] for r in rs]; am = sum(xs) / len(xs); asd = (sum((x - am) ** 2 for x in xs) / len(xs)) ** 0.5
        alt[k] = {'pnl': am, 'win': sum(x > 0 for x in xs) / len(xs), 't': am / asd * neff ** 0.5 if asd else 0}
    def parts(get):                                       # share and mean result of take profit, stop and time-out
        out = {}
        for h in ('tp', 'sl', 'time'):
            xs = [v for v, w in (get(r) for r in rs) if w == h]
            out[h] = [len(xs) / len(rs), sum(xs) / len(xs) if xs else 0]
        return out
    grid = {k: {'pnl': sum(r['grid'][k][0] for r in rs) / len(rs), 'parts': parts(lambda r, k=k: r['grid'][k])} for k in rs[0]['grid']}
    lvl = {k: {'pnl': sum(r['lv'][k][0] for r in rs) / len(rs), 'parts': parts(lambda r, k=k: r['lv'][k][:2]),
               'sl': sorted(r['lv'][k][2] for r in rs)[len(rs) // 2]} for k in rs[0]['lv']}
    return {'lvl': lvl, 'parts': parts(lambda r: (r['pnl'], r['how'])), 'grid': grid, 'alt': alt, 'n': len(rs), 'pred': sum(r['pct'] for r in rs) / len(rs), 'hit': sum(r['right'] for r in hit) / max(len(hit), 1),
            'neither': 1 - len(hit) / len(rs), 'tgt': med([r['tgt'] for r in rs]), 'stp': med([r['stp'] for r in rs]),
            'pnl': m, 't': m / sd * neff ** 0.5 if sd else 0}


def main():
    candles, st, _ = L.load()
    pts = points(candles, st)
    res = {'symbol': L.SYM, 'from': candles[0][0], 'to': candles[-1][0], 'n': len(pts),
           'buckets': {f'{a}-{b - 1 if b < 101 else 100}': stats_of([r for r in pts if a <= r['pct'] < b]) for a, b in BUCKETS},
           'mins': {str(m): stats_of([r for r in pts if r['pct'] >= m]) for m in MINS}}
    print(f'{L.SYM}: {len(pts)} points')
    for name, part in (('kans', res['buckets']), ('vanaf', res['mins'])):
        for k, s in part.items():
            if s:
                print(f"  {name} {k:>7}  n {s['n']:5d}  getoond {s['pred']:.0f}%  uitgekomen {s['hit']:.0%}  geen {s['neither']:.0%}  "
                      f"doel {s['tgt']:.1%} / stop {s['stp']:.1%}  trade {s['pnl']:+.2%}  t {s['t']:+.1f}  | " +
                      '  '.join(f"stop {k}x {v['pnl']:+.2%}" for k, v in s['alt'].items()))
    if '--json' in sys.argv:
        json.dump(res, open(sys.argv[sys.argv.index('--json') + 1], 'w'))


if __name__ == '__main__':
    main()
