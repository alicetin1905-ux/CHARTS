"""Full backtest of one community strategy from community_backtest.py on Bybit BTCUSDT perpetual.

    python3 scripts/community_detail.py 4H bb_tv n=30 k=2.5 filt=200 sl_atr=5.0 side=both

Writes a markdown report to stdout and an equity chart (log scale, strategy vs buy & hold) to
backtests/<tf>_<strategy>_equity.svg.
"""
import os, sys, time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import community_backtest as C  # noqa: E402

P, DAY = C.P, C.DAY


def parse(v):
    if v in ('True', 'False'):
        return v == 'True'
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    return v


def equity(t, trades, cost, start):
    """Mark-to-market equity per bar is overkill here: equity steps at each trade exit."""
    eq, curve = 1.0, []
    for j, k, d, r in trades:
        eq *= 1 + r - 2 * cost
        curve.append((t[min(k, len(t) - 1)], eq))
    return curve


def svg(path, t, c, curve, start, title):
    W, H, L, R, T, B = 900, 360, 60, 20, 30, 40
    t0, t1 = t[start], t[-1]
    bh = [(t[i], c[i] / c[start]) for i in range(start, len(t), max(1, (len(t) - start) // 1500))]
    st = [(t0, 1.0)] + curve
    lo = min(min(v for _, v in bh), min(v for _, v in st))
    hi = max(max(v for _, v in bh), max(v for _, v in st))
    ly0, ly1 = np.log(lo * 0.9), np.log(hi * 1.1)
    X = lambda x: L + (x - t0) / (t1 - t0) * (W - L - R)
    Y = lambda y: T + (ly1 - np.log(y)) / (ly1 - ly0) * (H - T - B)
    def line(pts, color, step):
        d, prev = [], None
        for x, y in pts:
            if step and prev is not None:
                d.append(f'L{X(x):.1f},{Y(prev):.1f}')
            d.append(f'{"M" if not d else "L"}{X(x):.1f},{Y(y):.1f}')
            prev = y
        return f'<path d="{" ".join(d)}" fill="none" stroke="{color}" stroke-width="1.6"/>'
    grid = []
    for m in (0.5, 1, 2, 3, 5, 10, 20, 30, 50, 100):
        if lo * 0.9 <= m <= hi * 1.1:
            grid.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(m):.1f}" y2="{Y(m):.1f}" stroke="#8884" />'
                        f'<text x="{L - 6}" y="{Y(m) + 4:.1f}" text-anchor="end" font-size="11" fill="#888">{m:g}x</text>')
    for y in range(time.gmtime(t0 / 1000).tm_year + 1, time.gmtime(t1 / 1000).tm_year + 1):
        x = X(time.mktime((y, 1, 1, 0, 0, 0, 0, 0, 0)) * 1000 - time.timezone * 1000)
        grid.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{H - B}" stroke="#8882" />'
                    f'<text x="{x:.1f}" y="{H - B + 16}" text-anchor="middle" font-size="11" fill="#888">{y}</text>')
    open(path, 'w').write(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif">'
        f'<rect width="{W}" height="{H}" fill="#fff"/>{"".join(grid)}'
        f'{line(bh, "#f2a900", False)}{line(st, "#1f6feb", True)}'
        f'<text x="{L}" y="18" font-size="13" fill="#333">{title.replace("&", "&amp;")}</text>'
        f'<text x="{W - R}" y="18" font-size="12" text-anchor="end"><tspan fill="#1f6feb">■ strategy</tspan>'
        f'<tspan fill="#f2a900" dx="10">■ buy &amp; hold BTC</tspan></text></svg>')


if __name__ == '__main__':
    tf, name = sys.argv[1], sys.argv[2]
    p = {k: parse(v) for k, v in (a.split('=') for a in sys.argv[3:])}
    candles = P.load('data', tf)
    t, c = candles[:, 0], candles[:, 4]
    D = P.prepare(candles)
    W = C.windows(t)
    tr = C.backtest(D, name, p)
    start = 300
    day = lambda ms: time.strftime('%Y-%m-%d', time.gmtime(ms / 1000))
    print(f'# {C.NAMES[name]} on {tf}: full backtest\n')
    print(f'Bybit BTCUSDT perpetual, {day(t[start])} to {day(t[-1])}. Settings: {C.ps(p)}. Signal on the close, fill at the '
          'next open, in the market all the time (reverses on the opposite signal), 1x, full equity per trade.\n')
    print('| Window | Profit factor | Trades / year | Win rate | Return | Max drawdown | Buy & hold |\n|---|---|---|---|---|---|---|')
    for w, (s, e) in [('Train (settings picked here)', W['train']), ('Test (never seen)', W['test']), ('Whole period', (start, len(t)))]:
        r = P.stats(t, tr, s, e)
        print(f"| {w} | {r['pf']:.2f} | {r['per_year']:.0f} | {r['win']:.0f}% | {r['ret']:+.0f}% | {r['dd']:.0f}% | "
              f"{(c[e - 1] / c[s] - 1) * 100:+.0f}% |")
    print('\n## Per calendar year\n\n| Year | PF | Trades | Win rate | Return | Max DD | BTC |\n|---|---|---|---|---|---|---|')
    for y in range(time.gmtime(t[start] / 1000).tm_year, time.gmtime(t[-1] / 1000).tm_year + 1):
        ms = lambda yy: (time.mktime((yy, 1, 1, 0, 0, 0, 0, 0, 0)) - time.timezone) * 1000
        s, e = max(start, int(np.searchsorted(t, ms(y)))), min(len(t), int(np.searchsorted(t, ms(y + 1))))
        r = P.stats(t, tr, s, e)
        print(f"| {y} | {min(r['pf'], 99):.2f} | {r['n']} | {r['win']:.0f}% | {r['ret']:+.0f}% | {r['dd']:.0f}% | {(c[e - 1] / c[s] - 1) * 100:+.0f}% |")
    print('\n## Longs vs shorts (whole period)\n\n| Side | Trades | PF | Win rate | Avg trade | Avg bars held |\n|---|---|---|---|---|---|')
    for d, lab in ((1, 'Long'), (-1, 'Short')):
        sub = [x for x in tr if x[2] == d]
        if not sub:
            print(f'| {lab} | 0 | | | | |')
            continue
        r = np.array([x[3] for x in sub]) - 2 * P.COST
        loss = -r[r <= 0].sum()
        print(f"| {lab} | {len(r)} | {r[r > 0].sum() / loss:.2f} | {100 * (r > 0).mean():.0f}% | {100 * r.mean():+.2f}% | "
              f"{np.mean([x[1] - x[0] for x in sub]):.0f} |")
    print('\n## Fee sensitivity (whole period)\n\n| Cost per side | PF | Return |\n|---|---|---|')
    for cost, lab in ((0.0, '0 (no fees)'), (0.0002, '0.02% (Bybit maker)'), (0.00055, '0.055% (Bybit taker)'),
                      (0.00075, '0.075% (taker + slippage, used above)'), (0.0015, '0.15% (bad fills)')):
        r = P.stats(t, tr, start, len(t), cost)
        print(f"| {lab} | {r['pf']:.2f} | {r['ret']:+.0f}% |")
    g = C.grid(name)
    print('\n## Neighbouring settings (one parameter moved one step), test window\n\n| Changed | Train PF | Test PF | Test return |\n|---|---|---|---|')
    for k, v in p.items():
        i = g[k].index(v)
        for j in (i - 1, i + 1):
            if 0 <= j < len(g[k]):
                q = dict(p, **{k: g[k][j]})
                tq = C.backtest(D, name, q)
                a, b = P.stats(t, tq, *W['train']), P.stats(t, tq, *W['test'])
                print(f"| {k}={g[k][j]} | {a['pf']:.2f} | {b['pf']:.2f} | {b['ret']:+.0f}% |")
    path = f'backtests/{tf}_{name}_equity.svg'
    svg(path, t, c, equity(t, tr, P.COST, start), start, f'{C.NAMES[name]} {tf} ({C.ps(p)}) vs buy & hold, log scale, after fees')
    print(f'\n![equity]({os.path.basename(path)})')
