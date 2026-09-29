"""Backtest of strategies/ema_ribbon_flip.pine in plain Python (no dependencies).

Candles come from OKX's BTC-USDT-SWAP (Bybit's API blocks many regions; the two perps track within a
few dollars). Signals on the bar close, fills at the next bar's open, 0.075% cost per side
(Bybit taker 0.055% + slippage), 1x, full equity per trade.

    python3 scripts/ribbon_backtest.py 4H           # defaults = Pine defaults
    python3 scripts/ribbon_backtest.py 1H --no-squeeze --sl 0
"""
import argparse, json, time, urllib.request

LENS = (20, 25, 30, 35, 40, 45, 50, 55)
FEE = 0.00075


def fetch(bar, n):
    out, after = [], ''
    while len(out) < n:
        url = (f'https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar={bar}&limit=100'
               + (f'&after={after}' if after else ''))
        req = urllib.request.Request(url, headers={'User-Agent': 'curl/8.0'})
        for i in range(4):
            try:
                d = json.load(urllib.request.urlopen(req, timeout=20))['data']
                break
            except OSError:
                time.sleep(2 ** i)
        else:
            raise SystemExit('OKX not reachable')
        if not d:
            break
        out += [[int(r[0]), *map(float, r[1:5])] for r in d if r[8] == '1']
        after = d[-1][0]
        time.sleep(0.12)
    return sorted(out)


def ema(src, n):
    a, out, v = 2 / (n + 1), [], None
    for x in src:
        v = x if v is None else v + a * (x - v)
        out.append(v)
    return out


def atr(h, l, c, n):
    out, v = [], None
    for i in range(len(c)):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        v = tr if v is None else v + (tr - v) / n
        out.append(v)
    return out


def backtest(candles, squeeze=0.6, lookback=10, sl_atr=2.5, atr_len=14):
    t, o, h, l, c = zip(*candles)
    E = [ema(c, n) for n in LENS]
    A = atr(h, l, c, atr_len)
    stack = lambda i, s: all(s * (E[k][i] - E[k + 1][i]) > 0 for k in range(len(LENS) - 1))
    width = [(max(e[i] for e in E) - min(e[i] for e in E)) / c[i] * 100 for i in range(len(c))]
    pos = entry = stop = 0
    eq = peak = 1.0
    mdd, trades = 0.0, []

    def close_at(px):
        nonlocal eq, pos
        r = pos * (px / entry - 1) - 2 * FEE
        eq *= 1 + r
        trades.append(r)
        pos = 0

    for i in range(200, len(c) - 1):
        if pos and sl_atr and (l[i] <= stop if pos == 1 else h[i] >= stop):
            close_at(stop)
        ok = squeeze is None or min(width[i - lookback + 1:i + 1]) <= squeeze
        want = 0
        if ok and stack(i, 1) and not stack(i - 1, 1):
            want = 1
        elif ok and stack(i, -1) and not stack(i - 1, -1):
            want = -1
        if want and want != pos:
            if pos:
                close_at(o[i + 1])
            pos, entry = want, o[i + 1]
            stop = entry - pos * sl_atr * A[i]
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    if pos:
        close_at(c[-1])
    wins = [r for r in trades if r > 0]
    loss = -sum(r for r in trades if r <= 0)
    return {
        'from': time.strftime('%Y-%m-%d', time.gmtime(t[200] / 1000)),
        'return_%': round((eq - 1) * 100, 1),
        'buy_hold_%': round((c[-1] / c[200] - 1) * 100, 1),
        'trades': len(trades),
        'win_rate_%': round(100 * len(wins) / max(1, len(trades)), 1),
        'profit_factor': round(sum(wins) / loss, 2) if loss else None,
        'max_drawdown_%': round(mdd * 100, 1),
    }


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('bar', nargs='?', default='4H', help='OKX bar: 15m, 1H, 4H, 1D ...')
    p.add_argument('--candles', type=int, default=9000)
    p.add_argument('--squeeze', type=float, default=0.6)
    p.add_argument('--no-squeeze', action='store_true')
    p.add_argument('--lookback', type=int, default=10)
    p.add_argument('--sl', type=float, default=2.5, help='ATR stop multiple, 0 = off')
    a = p.parse_args()
    print(json.dumps(backtest(fetch(a.bar, a.candles), None if a.no_squeeze else a.squeeze, a.lookback, a.sl), indent=2))
