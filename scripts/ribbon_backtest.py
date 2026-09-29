"""Multi-timeframe backtest of an EMA Ribbon [Krypt] flip strategy on BTCUSDT perpetual. No dependencies.

Ribbon: EMAs 20/25/30/35/40/45/50/55 (Krypt's defaults).
Long when all 8 EMAs flip into bullish order (EMA 20 on top ... EMA 55 at the bottom), short on the
bearish flip. The opposite flip closes and reverses the position. Signals on the bar close, fills at
the next bar's open, 0.075% cost per side (Bybit taker 0.055% + slippage), 1x, full equity per trade.

Variants:
  plain    flip only
  stop     flip + 2.5 x ATR(14) stop-loss
  squeeze  flip + stop, but only if within the last 10 bars the ribbon was squeezed to one line:
           (highest EMA - lowest EMA) <= 0.5 x ATR(14), which scales the same on every timeframe
  cross    EMA 20 x EMA 55 cross + stop (instead of the full 8-EMA flip)

Candles come from OKX's BTC-USDT-SWAP (Bybit's API blocks many regions; the two perps trade within a
few dollars of each other). 5m history builds 5m/15m/30m; 1H history builds 1H/2H/4H/6H/12H/1D.

    python3 scripts/ribbon_backtest.py                 # downloads into ./data (a few minutes)
    python3 scripts/ribbon_backtest.py --data /path    # reuse downloaded btc_5m.json / btc_1H.json
"""
import argparse, json, os, time, urllib.request

LENS = (20, 25, 30, 35, 40, 45, 50, 55)
COST = 0.00075
DAY = 86_400_000
TFS = {'5m': ('5m', 5), '15m': ('5m', 15), '30m': ('5m', 30), '1H': ('1H', 60), '2H': ('1H', 120),
       '4H': ('1H', 240), '6H': ('1H', 360), '12H': ('1H', 720), '1D': ('1H', 1440)}
VARIANTS = {
    'plain':   dict(trigger='flip', sl=None, squeeze=None),
    'stop':    dict(trigger='flip', sl=2.5, squeeze=None),
    'squeeze': dict(trigger='flip', sl=2.5, squeeze=0.5),
    'cross':   dict(trigger='cross', sl=2.5, squeeze=None),
}


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


def load(data_dir, bar, n):
    path = os.path.join(data_dir, f'btc_{bar}.json')
    if not os.path.exists(path):
        os.makedirs(data_dir, exist_ok=True)
        json.dump(fetch(bar, n), open(path, 'w'))
    return [r[:5] for r in json.load(open(path))]


def resample(candles, base_min, minutes):
    if minutes == base_min:
        return candles
    ms, need, out, cur = minutes * 60_000, minutes // base_min, [], None
    for t, o, h, l, c in candles:
        k = t - t % ms
        if cur and cur[0] == k:
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], h), min(cur[3], l), c, cur[5] + 1
        else:
            if cur and cur[5] == need:
                out.append(cur[:5])
            cur = [k, o, h, l, c, 1]
    if cur and cur[5] == need:
        out.append(cur[:5])
    return out


def ema(src, n):
    a, out, v = 2 / (n + 1), [], None
    for x in src:
        v = x if v is None else v + a * (x - v)
        out.append(v)
    return out


def atr(h, l, c, n=14):
    out, v = [], None
    for i in range(len(c)):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        v = tr if v is None else v + (tr - v) / n
        out.append(v)
    return out


def backtest(candles, start_ms=0, trigger='flip', sl=2.5, squeeze=0.5, lookback=10, cost=COST):
    t, o, h, l, c = zip(*candles)
    N = len(c)
    E = [ema(c, n) for n in LENS]
    A = atr(h, l, c)
    bull = [all(E[k][i] > E[k + 1][i] for k in range(7)) for i in range(N)]
    bear = [all(E[k][i] < E[k + 1][i] for k in range(7)) for i in range(N)]
    tight = [max(e[i] for e in E) - min(e[i] for e in E) <= (squeeze or 0) * A[i] for i in range(N)]
    first = max(200, next((i for i in range(N) if t[i] >= start_ms), N))

    pos = entry = stop = 0
    eq = peak = 1.0
    mdd, trades, last_sq, bars_in = 0.0, [], -10 ** 9, 0

    def close_at(px):
        nonlocal eq, pos
        r = pos * (px / entry - 1) - 2 * cost
        eq *= 1 + r
        trades.append(r)
        pos = 0

    for i in range(first, N - 1):
        if tight[i]:
            last_sq = i
        if pos:
            bars_in += 1
            if sl and (l[i] <= stop if pos == 1 else h[i] >= stop):
                close_at(stop)
        if trigger == 'flip':
            want = 1 if bull[i] and not bull[i - 1] else -1 if bear[i] and not bear[i - 1] else 0
        else:
            want = (1 if E[0][i] > E[7][i] and E[0][i - 1] <= E[7][i - 1]
                    else -1 if E[0][i] < E[7][i] and E[0][i - 1] >= E[7][i - 1] else 0)
        if squeeze is not None and i - last_sq >= lookback:
            want = 0
        if want and want != pos:
            if pos:
                close_at(o[i + 1])
            pos, entry = want, o[i + 1]
            stop = entry - pos * (sl or 0) * A[i]
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    if pos:
        close_at(c[-1])

    wins = [r for r in trades if r > 0]
    loss = -sum(r for r in trades if r <= 0)
    years = (t[-1] - t[first]) / DAY / 365
    return {
        'from': time.strftime('%Y-%m-%d', time.gmtime(t[first] / 1000)),
        'return': (eq - 1) * 100,
        'buy_hold': (c[-1] / o[first + 1] - 1) * 100,
        'trades': len(trades),
        'per_month': len(trades) / (years * 12) if years else 0,
        'win_rate': 100 * len(wins) / max(1, len(trades)),
        'profit_factor': sum(wins) / loss if loss else float('inf'),
        'max_dd': mdd * 100,
        'time_in_market': 100 * bars_in / max(1, N - 1 - first),
    }


def row(tf, name, r):
    return (f"| {tf} | {name} | {r['return']:+.1f}% | {r['buy_hold']:+.1f}% | {r['trades']} "
            f"| {r['per_month']:.1f} | {r['win_rate']:.0f}% | {r['profit_factor']:.2f} | {r['max_dd']:.1f}% |")


HEAD = ('| TF | Variant | Return | Buy & hold | Trades | Trades/mo | Win rate | Profit factor | Max DD |\n'
        '|---|---|---|---|---|---|---|---|---|')

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data', default='data')
    p.add_argument('--years', type=float, default=2, help='common test window for every timeframe')
    a = p.parse_args()
    base = {'5m': load(a.data, '5m', 211_000), '1H': load(a.data, '1H', 60_000)}
    end = min(base['5m'][-1][0], base['1H'][-1][0])
    start = end - int(a.years * 365 * DAY)
    series = {tf: resample(base[b], 5 if b == '5m' else 60, m) for tf, (b, m) in TFS.items()}

    print(f'## Same window for every timeframe (last {a.years:g} years)\n\n' + HEAD)
    for tf, s in series.items():
        for name, v in VARIANTS.items():
            print(row(tf, name, backtest(s, start, **v)))
    print('\n## Gross vs net (squeeze variant, same window): what fees cost\n')
    print('| TF | Before costs | After costs |\n|---|---|---|')
    for tf, s in series.items():
        g, n = (backtest(s, start, cost=c, **VARIANTS['squeeze']) for c in (0, COST))
        print(f"| {tf} | {g['return']:+.1f}% | {n['return']:+.1f}% |")
    print(f"\n## Full history, 1H and up (from {time.strftime('%Y-%m', time.gmtime(base['1H'][0][0] / 1000))})\n\n" + HEAD)
    for tf in ('1H', '2H', '4H', '6H', '12H', '1D'):
        for name, v in VARIANTS.items():
            print(row(tf, name, backtest(series[tf], 0, **v)))
