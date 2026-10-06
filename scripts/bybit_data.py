"""Bybit BTCUSDT perpetual 15m candles (UTC) from public.bybit.com. Needs pandas.

Bybit's REST API blocks many regions, but its public file server does not:
  - kline_for_metatrader4/BTCUSDT/<year>/BTCUSDT_15_*.csv.gz : 15m candles, Apr 2020 - Nov 2024, times in UTC+3
  - trading/BTCUSDT/BTCUSDT<date>.csv.gz                    : every trade, used from Dec 2024 on

Writes data/bybit_15m.json as [[open time ms UTC, open, high, low, close, volume BTC], ...].

    python3 scripts/bybit_data.py
"""
import calendar, gzip, io, json, os, re, sys, time, urllib.request
from multiprocessing import Pool

import pandas as pd

BASE = 'https://public.bybit.com'
MT4_OFFSET_MS = 3 * 3_600_000
TRADES_FROM = '2024-12-01'
CACHE = os.path.join(sys.argv[1] if len(sys.argv) > 1 else 'data', 'bybit_days')


def get(url, tries=5):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'curl/8.0'}),
                                          timeout=120).read()
        except Exception:  # dropped connections surface as IncompleteRead, not OSError
            time.sleep(2 ** i)
    raise SystemExit(f'cannot download {url}')


def links(path):
    return re.findall(r'href="([^"/]+/?)"', get(f'{BASE}/{path}').decode())


def mt4_month(path):
    rows = []
    for ln in gzip.decompress(get(f'{BASE}/kline_for_metatrader4/BTCUSDT/{path}')).decode().splitlines():
        if not ln.strip():
            continue
        ts, o, h, l, c, v = ln.split(',')
        t = calendar.timegm(time.strptime(ts, '%Y.%m.%d %H:%M')) * 1000 - MT4_OFFSET_MS
        rows.append([t, float(o), float(h), float(l), float(c), float(v)])
    return rows


def trade_day(name):
    cache = os.path.join(CACHE, name.replace('.csv.gz', '.json'))
    if os.path.exists(cache):
        return json.load(open(cache))
    df = pd.read_csv(io.BytesIO(gzip.decompress(get(f'{BASE}/trading/BTCUSDT/{name}'))),
                     usecols=['timestamp', 'price', 'size'])
    df = df.sort_values('timestamp', kind='stable')
    k = (df['timestamp'] * 1000).astype('int64') // 900_000 * 900_000
    g = df.groupby(k, sort=True)
    out = pd.DataFrame({'o': g['price'].first(), 'h': g['price'].max(), 'l': g['price'].min(),
                        'c': g['price'].last(), 'v': g['size'].sum()})
    rows = [[int(t), *map(float, r)] for t, r in zip(out.index, out.values)]
    json.dump(rows, open(cache, 'w'))
    return rows


if __name__ == '__main__':
    data_dir = sys.argv[1] if len(sys.argv) > 1 else 'data'
    os.makedirs(CACHE, exist_ok=True)
    months = [f'{y}{f}' for y in links('kline_for_metatrader4/BTCUSDT/') if y.endswith('/')
              for f in links(f'kline_for_metatrader4/BTCUSDT/{y}') if f.startswith('BTCUSDT_15_')]
    days = [f for f in links('trading/BTCUSDT/') if f.endswith('.csv.gz') and f[7:17] >= TRADES_FROM]
    print(f'{len(months)} MT4 months, {len(days)} trade days', file=sys.stderr, flush=True)
    with Pool(4) as pool:
        mt4 = [r for m in pool.map(mt4_month, months) for r in m]
        trades = []
        for i, d in enumerate(pool.imap(trade_day, days)):
            trades += d
            if i % 50 == 0:
                print(f'  trades {days[i][7:17]}', file=sys.stderr, flush=True)
    cut = calendar.timegm(time.strptime(TRADES_FROM, '%Y-%m-%d')) * 1000
    merged = {r[0]: r for r in mt4 if r[0] < cut}
    merged.update({r[0]: r for r in trades if r[0] >= cut})
    out = [merged[t] for t in sorted(merged)]
    json.dump(out, open(os.path.join(data_dir, 'bybit_15m.json'), 'w'))
    gaps = sum(1 for a, b in zip(out, out[1:]) if b[0] - a[0] != 900_000)
    print(f'{len(out)} candles {time.strftime("%Y-%m-%d", time.gmtime(out[0][0] / 1000))} -> '
          f'{time.strftime("%Y-%m-%d %H:%M", time.gmtime(out[-1][0] / 1000))}, {gaps} gaps', file=sys.stderr)
