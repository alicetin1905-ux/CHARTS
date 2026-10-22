"""Fetch the data behind liquidity-map.html and embed it in the page, or write it for the page's database.

The page fetches the same data live in the browser where the exchanges allow it. On GitHub Pages it also reads
liquidity-data.json next to it (written every hour by .github/workflows/pages.yml with --site), for the exchanges a
browser cannot reach and the book history of past hours. The hosted claude.ai page cannot reach the exchanges: it
reads the newest snapshot from its database (written every hour by a scheduled task with --db). Both fall back to
the snapshot embedded between /*SNAPSHOT*/ and /*END SNAPSHOT*/.

Snapshot shape:
  ts, price
  books     {exchange: {b: [[price, usd], ...], a: [...]}} in $10 bins, within ±15% of the price
  candles   [[open_ms, open, high, low, close, volume_usd], ...] OKX BTC-USDT-SWAP 1H (400), oldest first
  candles15, candles4h   the same for 15m and 4H (300 each), for display only
  oi        [[ms, usd], ...] Gate BTC_USDT open interest, 1H
  taker     [[ms, buy share 0..1], ...] Gate taker buy / (buy + sell), 1H
  liqReal   [[ms, long_usd, short_usd], ...] Gate real liquidations, 1H
  market    {exchange: {oi, vol, fund}} open interest (USD), 24h volume (USD), funding per 8h
  bookHist  [{t, b: [[price, bid_usd, ask_usd], ...]}] hourly combined books, $100 bins within ±6%

    python3 scripts/liquidity_snapshot.py                 embed into liquidity-map.html
    python3 scripts/liquidity_snapshot.py --db DIR        write DIR/snap.json and DIR/bookhist.json (no embed)
    python3 scripts/liquidity_snapshot.py --site DIR [--prev URL]
                                                          write DIR/liquidity-data.json (no embed), carrying the
                                                          book history of the file at URL forward
"""
import json
import math
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, 'liquidity-map.html')
BIN = 10
HIST_BIN = 100


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


# ---------- order books: (bids, asks) as [(price, usd)] ----------

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


BOOKS = [('binance', book_binance), ('okx', book_okx), ('bybit', book_bybit), ('bitget', book_bitget),
         ('gate', book_gate), ('htx', book_htx), ('mexc', book_mexc), ('deribit', book_deribit),
         ('bitmex', book_bitmex), ('kraken', book_kraken), ('cryptocom', book_cryptocom),
         ('hyperliquid', book_hyperliquid), ('dydx', book_dydx), ('bitfinex', book_bitfinex)]


# ---------- open interest (USD), 24h volume (USD), funding per 8h ----------

def mk_okx():
    oi = get('https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP')['data'][0]
    t = get('https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP')['data'][0]
    f = get('https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP')['data'][0]
    return {'oi': float(oi['oiUsd']), 'vol': float(t['volCcy24h']) * float(t['last']), 'fund': float(f['fundingRate'])}


def mk_gate():
    t = get('https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=BTC_USDT')[0]
    return {'oi': float(t['total_size']) * 1e-4 * float(t['mark_price']), 'vol': float(t['volume_24h_quote']),
            'fund': float(t['funding_rate'])}


def mk_bitget():
    t = get('https://api.bitget.com/api/v2/mix/market/ticker?symbol=BTCUSDT&productType=usdt-futures')['data'][0]
    return {'oi': float(t['holdingAmount']) * float(t['lastPr']), 'vol': float(t['usdtVolume']), 'fund': float(t['fundingRate'])}


def mk_htx():
    oi = get('https://api.hbdm.com/linear-swap-api/v1/swap_his_open_interest'
             '?contract_code=BTC-USDT&period=60min&amount_type=1&size=1')['data']['tick'][0]['value']
    t = get('https://api.hbdm.com/linear-swap-ex/market/detail/merged?contract_code=BTC-USDT')['tick']
    return {'oi': float(oi), 'vol': float(t['trade_turnover'])}


def mk_mexc():
    t = get('https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT')['data']
    return {'oi': t['holdVol'] * 1e-4 * t['lastPrice'], 'vol': t['amount24'], 'fund': t['fundingRate']}


def mk_deribit():
    r = get('https://www.deribit.com/api/v2/public/get_book_summary_by_instrument?instrument_name=BTC-PERPETUAL')['result'][0]
    return {'oi': r['open_interest'], 'vol': r['volume_usd'], 'fund': r['funding_8h']}


def mk_kraken():
    t = get('https://futures.kraken.com/derivatives/api/v3/tickers/PF_XBTUSD')['ticker']
    return {'oi': t['openInterest'] * t['last'], 'vol': t['volumeQuote']}


def mk_cryptocom():
    t = get('https://api.crypto.com/exchange/v1/public/get-tickers?instrument_name=BTCUSD-PERP')['result']['data'][0]
    return {'oi': float(t['oi']) * float(t['a']), 'vol': float(t['vv'])}


def mk_hyperliquid():
    meta, ctx = get('https://api.hyperliquid.xyz/info', {'type': 'metaAndAssetCtxs'})
    x = ctx[[u['name'] for u in meta['universe']].index('BTC')]
    return {'oi': float(x['openInterest']) * float(x['markPx']), 'vol': float(x['dayNtlVlm']), 'fund': float(x['funding']) * 8}


def mk_dydx():
    x = get('https://indexer.dydx.trade/v4/perpetualMarkets?ticker=BTC-USD')['markets']['BTC-USD']
    return {'oi': float(x['openInterest']) * float(x['oraclePrice']), 'vol': float(x['volume24H']),
            'fund': float(x['nextFundingRate']) * 8}


def mk_bitfinex():
    s = get('https://api-pub.bitfinex.com/v2/status/deriv?keys=tBTCF0:USTF0')[0]
    t = get('https://api-pub.bitfinex.com/v2/ticker/tBTCF0:USTF0')
    return {'oi': s[18] * s[15], 'vol': t[7] * t[6], 'fund': s[12]}


def mk_binance():
    oi = get('https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT')
    t = get('https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT')
    f = get('https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT')
    return {'oi': float(oi['openInterest']) * float(f['markPrice']), 'vol': float(t['quoteVolume']),
            'fund': float(f['lastFundingRate'])}


def mk_bybit():
    t = get('https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT')['result']['list'][0]
    return {'oi': float(t['openInterestValue']), 'vol': float(t['turnover24h']), 'fund': float(t['fundingRate'])}


def mk_bitmex():
    x = get('https://www.bitmex.com/api/v1/instrument?symbol=XBTUSD')[0]
    return {'oi': x['openInterest'], 'vol': x.get('foreignNotional24h') or 0, 'fund': x['fundingRate']}


MARKET = [('binance', mk_binance), ('okx', mk_okx), ('bybit', mk_bybit), ('bitget', mk_bitget), ('gate', mk_gate),
          ('htx', mk_htx), ('mexc', mk_mexc), ('deribit', mk_deribit), ('bitmex', mk_bitmex), ('kraken', mk_kraken),
          ('cryptocom', mk_cryptocom), ('hyperliquid', mk_hyperliquid), ('dydx', mk_dydx), ('bitfinex', mk_bitfinex)]


def okx_candles(bar, pages):
    base = 'https://www.okx.com/api/v5/market/'
    rows = get(base + f'candles?instId=BTC-USDT-SWAP&bar={bar}&limit=300')['data']
    for _ in range(pages - 1):
        rows += get(base + f'history-candles?instId=BTC-USDT-SWAP&bar={bar}&limit=100&after={rows[-1][0]}')['data']
    return sorted([int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), round(float(r[7]))] for r in rows)


def volume_weights(books, market, price):
    """Exchanges whose share of the depth within ±1% exceeds their share of 24h volume are scaled down to it."""
    depth = {k: sum(u for p, u in v['b'] if p >= price * 0.99) + sum(u for p, u in v['a'] if p <= price * 1.01)
             for k, v in books.items()}
    has = [k for k in depth if market.get(k, {}).get('vol')]
    dt, vt = sum(depth[k] for k in has), sum(market[k]['vol'] for k in has)
    return {k: min(1, (market[k]['vol'] / vt) / (depth[k] / dt)) if k in has and dt and vt and depth[k] else 1
            for k in depth}


def book_hist_entry(books, weights, price, ts):
    lo, hi = price * 0.94, price * 1.06
    m = {}
    for ex, v in books.items():
        w = weights.get(ex, 1)
        for side, i in (('b', 0), ('a', 1)):
            for p, u in v[side]:
                if lo <= p <= hi:
                    k = math.floor(p / HIST_BIN) * HIST_BIN
                    m.setdefault(k, [0, 0])[i] += u * w
    return {'t': ts // 3600000 * 3600000, 'b': [[k, round(a), round(b)] for k, (a, b) in sorted(m.items()) if a + b > 0]}


def main():
    arg = lambda name: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None
    db_dir, site_dir, prev_url = arg('--db'), arg('--site'), arg('--prev')
    books = {}
    for name, fn in BOOKS:
        try:
            bids, asks = fn()
            books[name] = {'b': bin_levels(bids, 'b'), 'a': bin_levels(asks, 'a')}
            print(f'book   {name:12s} {len(bids):5d} bids {len(asks):5d} asks')
        except Exception as e:
            print(f'book   {name:12s} skipped: {e}')
    market = {}
    for name, fn in MARKET:
        try:
            market[name] = {k: round(v, 8 if k == 'fund' else 0) for k, v in fn().items()}
        except Exception as e:
            print(f'market {name:12s} skipped: {e}')
    print('market', ', '.join(f'{k} OI ${v.get("oi", 0) / 1e9:.2f}B' for k, v in market.items()))

    candles = okx_candles('1H', 2)[-400:]
    candles15 = okx_candles('15m', 1)
    candles4h = okx_candles('4H', 1)
    st = get('https://api.gateio.ws/api/v4/futures/usdt/contract_stats'
             f'?contract=BTC_USDT&interval=1h&from={candles[0][0] // 1000}&limit=1000')
    oi = [[r['time'] * 1000, round(r['open_interest_usd'])] for r in st]
    taker = [[r['time'] * 1000, round(r['lsr_taker'] / (1 + r['lsr_taker']), 4)] for r in st]
    liq_real = [[r['time'] * 1000, round(r['long_liq_usd']), round(r['short_liq_usd'])] for r in st]

    price = candles[-1][4]
    for v in books.values():  # keep the snapshot small: ±15% is more than the page ever shows
        v['b'] = [r for r in v['b'] if r[0] >= price * 0.85]
        v['a'] = [r for r in v['a'] if r[0] <= price * 1.15]
    ts = int(time.time() * 1000)
    entry = book_hist_entry(books, volume_weights(books, market, price), price, ts)
    snap = {'ts': ts, 'price': price, 'books': books, 'candles': candles, 'candles15': candles15,
            'candles4h': candles4h, 'oi': oi, 'taker': taker, 'liqReal': liq_real, 'market': market}
    print(f'{len(candles)} 1H candles, {len(oi)} Gate stat hours, price {price}')

    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
        body = json.dumps(snap, separators=(',', ':'))
        json.dump({'ts': ts, 'json': body}, open(os.path.join(db_dir, 'snap.json'), 'w'), separators=(',', ':'))
        json.dump({'t': entry['t'], 'json': json.dumps(entry, separators=(',', ':'))},
                  open(os.path.join(db_dir, 'bookhist.json'), 'w'), separators=(',', ':'))
        print(f'wrote {db_dir}/snap.json ({len(body) // 1024} KB) and bookhist.json, doc id h{ts // 3600000}')
        if len(body) > 250 * 1024:
            raise SystemExit('snapshot is over the 256 KiB document limit')
        return

    def with_history(prev):  # carry the previous book history forward, one entry per hour, last 48
        hist = {e['t']: e for e in prev}
        hist[entry['t']] = entry
        snap['bookHist'] = [hist[k] for k in sorted(hist)][-48:]

    if site_dir:
        prev = []
        if prev_url:
            try:
                prev = get(prev_url).get('bookHist', [])
            except Exception as e:
                print(f'no previous data at {prev_url}: {e}')
        with_history(prev)
        os.makedirs(site_dir, exist_ok=True)
        path = os.path.join(site_dir, 'liquidity-data.json')
        json.dump(snap, open(path, 'w'), separators=(',', ':'))
        print(f'wrote {path} ({os.path.getsize(path) // 1024} KB, {len(snap["bookHist"])} hours of book history)')
        return

    html = open(PAGE, encoding='utf-8').read()
    old = re.search(r'/\*SNAPSHOT\*/(.*?)/\*END SNAPSHOT\*/', html, re.S)
    if not old:
        raise SystemExit('snapshot markers not found in liquidity-map.html')
    try:
        with_history(json.loads(old.group(1)).get('bookHist', []))
    except (ValueError, AttributeError):
        with_history([])
    body = json.dumps(snap, separators=(',', ':'))
    html = html[:old.start()] + '/*SNAPSHOT*/' + body + '/*END SNAPSHOT*/' + html[old.end():]
    open(PAGE, 'w', encoding='utf-8').write(html)
    print(f'wrote {PAGE} ({len(html) // 1024} KB, {len(snap["bookHist"])} hours of book history)')


if __name__ == '__main__':
    main()
