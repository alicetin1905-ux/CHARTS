"""Write a data snapshot into liquidity-map.html.

The page fetches order books, 1H candles and open interest live in the browser. The hosted
claude.ai page cannot reach the exchanges, so it falls back to the snapshot this script embeds
between the /*SNAPSHOT*/ and /*END SNAPSHOT*/ markers. Same sources and units as the page:

  books   {exchange: {b: [[price, usd], ...], a: [...]}} in $10 bins
  candles [[open_ms, open, high, low, close, volume_usd], ...] OKX BTC-USDT-SWAP 1H, oldest first
  oi      [[ms, open_interest_usd], ...] OKX BTC-USDT-SWAP 1H, oldest first

    python3 scripts/liquidity_snapshot.py
"""
import json
import math
import os
import re
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, 'liquidity-map.html')
BIN = 10


def get(url, body=None):
    headers = {'User-Agent': 'curl/8.0'}
    if body is not None:
        headers['content-type'] = 'application/json'
        body = json.dumps(body).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def bin_levels(rows, side):
    m = {}
    for p, usd in rows:
        if usd <= 0:
            continue
        k = math.floor(p / BIN) * BIN if side == 'b' else math.ceil(p / BIN) * BIN
        m[k] = m.get(k, 0) + usd
    return [[k, round(v)] for k, v in sorted(m.items())]


def book_okx():
    b = get('https://www.okx.com/api/v5/market/books-full?instId=BTC-USDT-SWAP&sz=5000')['data'][0]
    f = lambda r: (float(r[0]), float(r[1]) * 0.01 * float(r[0]))
    return [f(r) for r in b['bids']], [f(r) for r in b['asks']]


def book_binance():
    j = get('https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000')
    f = lambda r: (float(r[0]), float(r[1]) * float(r[0]))
    return [f(r) for r in j['bids']], [f(r) for r in j['asks']]


def book_deribit():
    j = get('https://www.deribit.com/api/v2/public/get_order_book?instrument_name=BTC-PERPETUAL&depth=10000')['result']
    return [tuple(r) for r in j['bids']], [tuple(r) for r in j['asks']]  # amounts are USD


def book_gate():
    j = get('https://api.gateio.ws/api/v4/futures/usdt/order_book?contract=BTC_USDT&limit=300&interval=10')
    f = lambda r: (float(r['p']), r['s'] * 1e-4 * float(r['p']))
    return [f(r) for r in j['bids']], [f(r) for r in j['asks']]


def book_hyperliquid():
    j = get('https://api.hyperliquid.xyz/info', {'type': 'l2Book', 'coin': 'BTC', 'nSigFigs': 3})
    f = lambda r: (float(r['px']), float(r['sz']) * float(r['px']))
    return [f(r) for r in j['levels'][0]], [f(r) for r in j['levels'][1]]


def book_bybit():
    r = get('https://api.bybit.com/v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=500')['result']
    f = lambda x: (float(x[0]), float(x[1]) * float(x[0]))
    return [f(x) for x in r['b']], [f(x) for x in r['a']]


def book_bitget():
    d = get('https://api.bitget.com/api/v2/mix/market/merge-depth'
            '?productType=usdt-futures&symbol=BTCUSDT&limit=max&precision=scale2')['data']
    f = lambda x: (float(x[0]), float(x[1]) * float(x[0]))
    return [f(x) for x in d['bids']], [f(x) for x in d['asks']]


def book_htx():
    t = get('https://api.hbdm.com/linear-swap-ex/market/depth?contract_code=BTC-USDT&type=step0')['tick']
    f = lambda x: (x[0], x[1] * 0.001 * x[0])
    return [f(x) for x in t['bids']], [f(x) for x in t['asks']]


def book_mexc():
    d = get('https://contract.mexc.com/api/v1/contract/depth/BTC_USDT?limit=2000')['data']
    f = lambda x: (x[0], x[1] * 0.0001 * x[0])
    return [f(x) for x in d['bids']], [f(x) for x in d['asks']]


def book_bitmex():
    j = get('https://www.bitmex.com/api/v1/orderBook/L2?symbol=XBTUSD&depth=0')
    f = lambda x: (x['price'], x['size'])  # XBTUSD contracts are $1 each
    return [f(x) for x in j if x['side'] == 'Buy'], [f(x) for x in j if x['side'] == 'Sell']


def book_kraken():
    b = get('https://futures.kraken.com/derivatives/api/v3/orderbook?symbol=PF_XBTUSD')['orderBook']
    f = lambda x: (x[0], x[1] * x[0])
    return [f(x) for x in b['bids']], [f(x) for x in b['asks']]


def book_cryptocom():
    d = get('https://api.crypto.com/exchange/v1/public/get-book?instrument_name=BTCUSD-PERP&depth=150')['result']['data'][0]
    f = lambda x: (float(x[0]), float(x[1]) * float(x[0]))
    return [f(x) for x in d['bids']], [f(x) for x in d['asks']]


def book_dydx():
    j = get('https://indexer.dydx.trade/v4/orderbooks/perpetualMarket/BTC-USD')
    f = lambda x: (float(x['price']), float(x['size']) * float(x['price']))
    return [f(x) for x in j['bids']], [f(x) for x in j['asks']]


def book_bitfinex():
    rows = get('https://api-pub.bitfinex.com/v2/book/tBTCF0:USTF0/P0?len=250')
    return ([(r[0], r[2] * r[0]) for r in rows if r[2] > 0],
            [(r[0], -r[2] * r[0]) for r in rows if r[2] < 0])


def main():
    books = {}
    for name, fn in [('binance', book_binance), ('okx', book_okx), ('bybit', book_bybit), ('bitget', book_bitget),
                     ('gate', book_gate), ('htx', book_htx), ('mexc', book_mexc), ('deribit', book_deribit),
                     ('bitmex', book_bitmex), ('kraken', book_kraken), ('cryptocom', book_cryptocom),
                     ('hyperliquid', book_hyperliquid), ('dydx', book_dydx), ('bitfinex', book_bitfinex)]:
        try:
            bids, asks = fn()
            books[name] = {'b': bin_levels(bids, 'b'), 'a': bin_levels(asks, 'a')}
            print(f'{name:12s} {len(bids):5d} bids {len(asks):5d} asks')
        except Exception as e:
            print(f'{name:12s} skipped: {e}')

    base = 'https://www.okx.com/api/v5/market/'
    c1 = get(base + 'candles?instId=BTC-USDT-SWAP&bar=1H&limit=300')['data']
    c2 = get(base + f'history-candles?instId=BTC-USDT-SWAP&bar=1H&limit=100&after={c1[-1][0]}')['data']
    candles = sorted([[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), round(float(r[7]))]
                      for r in c1 + c2])

    oi, end = {}, ''
    for _ in range(4):
        d = get('https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history'
                f'?instId=BTC-USDT-SWAP&period=1H&limit=100{"&end=" + end if end else ""}')['data']
        if not d:
            break
        for r in d:
            oi[int(r[0])] = round(float(r[3]))
        end = d[-1][0]

    snap = {'ts': int(time.time() * 1000), 'price': candles[-1][4], 'books': books,
            'candles': candles, 'oi': sorted([k, v] for k, v in oi.items())}
    print(f'{len(candles)} candles, {len(oi)} OI points, price {snap["price"]}')

    html = open(PAGE, encoding='utf-8').read()
    html, n = re.subn(r'/\*SNAPSHOT\*/.*?/\*END SNAPSHOT\*/',
                      lambda _: '/*SNAPSHOT*/' + json.dumps(snap, separators=(',', ':')) + '/*END SNAPSHOT*/',
                      html, count=1, flags=re.S)
    if n != 1:
        raise SystemExit('snapshot markers not found in liquidity-map.html')
    open(PAGE, 'w', encoding='utf-8').write(html)
    print(f'wrote {PAGE} ({len(html) // 1024} KB)')


if __name__ == '__main__':
    main()
