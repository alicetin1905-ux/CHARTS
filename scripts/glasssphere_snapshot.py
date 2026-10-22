"""Fetch the data behind index.html into glasssphere-data.json (BTC) or glasssphere-<asset>.json (eth, sol, ...).

index.html fetches the same data live in the browser where the exchanges allow it. On GitHub Pages it first reads
that file next to it, written every hour by .github/workflows/pages.yml, for the exchanges a browser
cannot reach and the book history of past hours.

Data shape:
  ts, price
  asset     BTC, ETH, SOL, BNB, XRP, DOGE, SUI, ENA, GALA, PEPE, WLD or NEAR
  bin, histBin  bin sizes of books and bookHist (about 0.012% and 0.12% of the price; BTC $10 / $100)
  books     {exchange: {b: [[price, usd], ...], a: [...]}} in bins of `bin`, within ±15% of the price
  candles   [[open_ms, open, high, low, close, volume_usd], ...] OKX <coin>-USDT-SWAP 1H (400), oldest first
  candles15, candles4h   the same for 15m and 4H (300 each), for display only
  oi        [[ms, usd], ...] Gate <coin>_USDT open interest, 1H
  taker     [[ms, buy share 0..1], ...] Gate taker buy / (buy + sell), 1H
  liqReal   [[ms, long_usd, short_usd], ...] Gate real liquidations, 1H
  market    {exchange: {oi, vol, fund}} open interest (USD), 24h volume (USD), funding per 8h
  bookHist  [{t, b: [[price, bid_usd, ask_usd], ...]}] hourly combined books in bins of `histBin` within ±6%,
            last 48 hours

    python3 scripts/glasssphere_snapshot.py DIR [--asset btc|eth|...|wld] [--prev URL ...]
        writes the asset's file into DIR, carrying the book history of the first --prev URL that answers forward
"""
import json
import math
import os
import sys
import time
import urllib.request

# Per asset: book bins (fixed, so the hourly history stays comparable; about 0.012% and 0.12% of the price),
# symbols and contract sizes on each exchange. None = the exchange is left out for that asset.
ASSETS = {
    'btc': dict(bin=10, hist_bin=100, name='BTC', file='glasssphere-data.json', okx='BTC-USDT-SWAP', okx_ct=0.01, usdt='BTCUSDT',
                gate='BTC_USDT', gate_mul=1e-4, gate_iv='10', htx='BTC-USDT', htx_ct=0.001, mexc='BTC_USDT', mexc_ct=1e-4,
                deribit='BTC-PERPETUAL', deribit_usd=True, bitmex='XBTUSD', kraken='PF_XBTUSD', cdc='BTCUSD-PERP',
                hl='BTC', hl_sig=3, dydx='BTC-USD', bfx='tBTCF0:USTF0'),
    'eth': dict(bin=0.25, hist_bin=2.5, name='ETH', file='glasssphere-eth.json', okx='ETH-USDT-SWAP', okx_ct=0.1, usdt='ETHUSDT',
                gate='ETH_USDT', gate_mul=0.01, gate_iv='1', htx='ETH-USDT', htx_ct=0.01, mexc='ETH_USDT', mexc_ct=0.01,
                deribit='ETH-PERPETUAL', deribit_usd=True, bitmex=None, kraken='PF_ETHUSD', cdc='ETHUSD-PERP',
                hl='ETH', hl_sig=4, dydx='ETH-USD', bfx='tETHF0:USTF0'),
    'sol': dict(bin=0.01, hist_bin=0.1, name='SOL', file='glasssphere-sol.json', okx='SOL-USDT-SWAP', okx_ct=1, usdt='SOLUSDT',
                gate='SOL_USDT', gate_mul=1, gate_iv='0.1', htx='SOL-USDT', htx_ct=1, mexc='SOL_USDT', mexc_ct=0.1,
                deribit='SOL_USDC-PERPETUAL', deribit_usd=False, bitmex=None, kraken='PF_SOLUSD', cdc='SOLUSD-PERP',
                hl='SOL', hl_sig=4, dydx='SOL-USD', bfx='tSOLF0:USTF0'),

    'bnb': dict(bin=0.1, hist_bin=1, name='BNB', file='glasssphere-bnb.json', okx='BNB-USDT-SWAP', okx_ct=0.01, usdt='BNBUSDT',
                gate='BNB_USDT', gate_mul=0.001, gate_iv='1', htx='BNB-USDT', htx_ct=0.1, mexc='BNB_USDT', mexc_ct=0.01,
                deribit='BNB_USDC-PERPETUAL', deribit_usd=False, bitmex=None, kraken='PF_BNBUSD', cryptocom=None,
                hl='BNB', hl_sig=3, dydx='BNB-USD', bfx='tBNBF0:USTF0'),
    'xrp': dict(bin=0.0001, hist_bin=0.002, name='XRP', file='glasssphere-xrp.json', okx='XRP-USDT-SWAP', okx_ct=100,
                usdt='XRPUSDT', gate='XRP_USDT', gate_mul=10, gate_iv='0.001', htx='XRP-USDT', htx_ct=10, mexc='XRP_USDT',
                mexc_ct=1, deribit='XRP_USDC-PERPETUAL', deribit_usd=False, bitmex=None, kraken='PF_XRPUSD', cdc='XRPUSD-PERP',
                hl='XRP', hl_sig=4, dydx='XRP-USD', bfx='tXRPF0:USTF0', bitget_sc='scale1'),
    'doge': dict(bin=0.00001, hist_bin=0.0001, name='DOGE', file='glasssphere-doge.json', okx='DOGE-USDT-SWAP', okx_ct=1000,
                 usdt='DOGEUSDT', gate='DOGE_USDT', gate_mul=10, gate_iv='0.0001', htx='DOGE-USDT', htx_ct=100, mexc='DOGE_USDT',
                 mexc_ct=100, deribit='DOGE_USDC-PERPETUAL', deribit_usd=False, bitmex=None, kraken='PF_DOGEUSD',
                 cdc='DOGEUSD-PERP', hl='DOGE', hl_sig=3, dydx='DOGE-USD', bfx='tDOGEF0:USTF0', bitget_sc='scale1'),
    'sui': dict(bin=0.0001, hist_bin=0.001, name='SUI', file='glasssphere-sui.json', okx='SUI-USDT-SWAP', okx_ct=1,
                usdt='SUIUSDT', gate='SUI_USDT', gate_mul=1, gate_iv='0.001', htx='SUI-USDT', htx_ct=1, mexc='SUI_USDT',
                mexc_ct=1, deribit='SUI_USDC-PERPETUAL', deribit_usd=False, bitmex=None, kraken='PF_SUIUSD', cdc='SUIUSD-PERP',
                hl='SUI', hl_sig=3, dydx='SUI-USD', bfx='tSUIF0:USTF0', bitget_sc='scale1'),
    'ena': dict(bin=0.00001, hist_bin=0.0002, name='ENA', file='glasssphere-ena.json', okx='ENA-USDT-SWAP', okx_ct=10,
                usdt='ENAUSDT', gate='ENA_USDT', gate_mul=10, gate_iv='0.0001', htx='ENA-USDT', htx_ct=1, mexc='ENA_USDT',
                mexc_ct=10, deribit='ENA_USDC-PERPETUAL', deribit_usd=False, bitmex=None, kraken='PF_ENAUSD', cdc='ENAUSD-PERP',
                hl='ENA', hl_sig=4, dydx='ENA-USD', bfx='tENAF0:USTF0', bitget_sc='scale1'),
    'gala': dict(bin=0.000001, hist_bin=0.000002, name='GALA', file='glasssphere-gala.json', okx='GALA-USDT-SWAP', okx_ct=10,
                 usdt='GALAUSDT', gate='GALA_USDT', gate_mul=10, gate_iv='0.000001', htx=None, mexc='GALA_USDT', mexc_ct=10,
                 deribit=None, bitmex=None, kraken='PF_GALAUSD', cdc='GALAUSD-PERP', hl='GALA', hl_sig=4, dydx=None,
                 bfx='tGALAF0:USTF0', bitget_sc='scale0'),
    # Binance, Bybit, Hyperliquid (kPEPE) and Deribit quote PEPE per 1000: their prices are divided by 1000 (div)
    'pepe': dict(bin=1e-9, hist_bin=5e-9, name='PEPE', file='glasssphere-pepe.json', okx='PEPE-USDT-SWAP', okx_ct=1e7,
                 usdt='1000PEPEUSDT', bitget_sym='PEPEUSDT', gate='PEPE_USDT', gate_mul=1e7, gate_iv='0.000000001',
                 htx='PEPE-USDT', htx_ct=1e6, mexc='PEPE_USDT', mexc_ct=1e7, deribit='1000PEPE_USDC-PERPETUAL',
                 deribit_usd=False, bitmex=None, kraken='PF_PEPEUSD', cdc='PEPEUSD-PERP', hl='kPEPE', hl_sig=3,
                 dydx='PEPE-USD', bfx='tPEPEF0:USTF0', bitget_sc='scale1',
                 div={'binance': 1000, 'bybit': 1000, 'hyperliquid': 1000, 'deribit': 1000}),
    'wld': dict(bin=0.0001, hist_bin=0.0005, name='WLD', file='glasssphere-wld.json', okx='WLD-USDT-SWAP', okx_ct=1,
                usdt='WLDUSDT', gate='WLD_USDT', gate_mul=1, gate_iv='0.0001', htx='WLD-USDT', htx_ct=1, mexc='WLD_USDT',
                mexc_ct=1, deribit='WLD_USDC-PERPETUAL', deribit_usd=False, bitmex=None, kraken='PF_WLDUSD', cdc='WLDUSD-PERP',
                hl='WLD', hl_sig=3, dydx='WLD-USD', bitfinex=None, bitget_sc='scale0'),
    'near': dict(bin=0.001, hist_bin=0.005, name='NEAR', file='glasssphere-near.json', okx='NEAR-USDT-SWAP', okx_ct=10,
                 usdt='NEARUSDT', gate='NEAR_USDT', gate_mul=1, gate_iv='0.001', htx='NEAR-USDT', htx_ct=1, mexc='NEAR_USDT',
                 mexc_ct=1, deribit='NEAR_USDC-PERPETUAL', deribit_usd=False, bitmex=None, kraken='PF_NEARUSD',
                 cdc='NEARUSD-PERP', hl='NEAR', hl_sig=3, dydx='NEAR-USD', bfx='tNEARF0:USTF0', bitget_sc='scale1'),}


def get(url, body=None):
    headers = {'User-Agent': 'curl/8.0'}
    if body is not None:
        headers['content-type'] = 'application/json'
        body = json.dumps(body).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def digits(step):
    """Decimals that keep multiples of step exact after rounding (PEPE bins are 1e-9)."""
    return max(0, -math.floor(math.log10(step))) + 3


def bin_levels(rows, side, step):
    m = {}
    for p, usd in rows:
        if usd <= 0:
            continue
        k = (math.floor(p / step + 1e-9) if side == 'b' else math.ceil(p / step - 1e-9)) * step
        k = round(k, digits(step))
        m[k] = m.get(k, 0) + usd
    return [[k, round(v)] for k, v in sorted(m.items())]


# ---------- order books: (bids, asks) as [(price, usd)] ----------

def book_okx(A):
    b = get(f'https://www.okx.com/api/v5/market/books-full?instId={A["okx"]}&sz=5000')['data'][0]
    f = lambda r: (float(r[0]), float(r[1]) * A['okx_ct'] * float(r[0]))
    return [f(r) for r in b['bids']], [f(r) for r in b['asks']]


def book_binance(A):
    j = get(f'https://fapi.binance.com/fapi/v1/depth?symbol={A["usdt"]}&limit=1000')
    f = lambda r: (float(r[0]), float(r[1]) * float(r[0]))
    return [f(r) for r in j['bids']], [f(r) for r in j['asks']]


def book_deribit(A):
    j = get(f'https://www.deribit.com/api/v2/public/get_order_book?instrument_name={A["deribit"]}&depth=10000')['result']
    f = (lambda r: (r[0], r[1])) if A['deribit_usd'] else (lambda r: (r[0], r[1] * r[0]))  # inverse: USD, linear: coin
    return [f(r) for r in j['bids']], [f(r) for r in j['asks']]


def book_gate(A):
    j = get(f'https://api.gateio.ws/api/v4/futures/usdt/order_book?contract={A["gate"]}&limit=300&interval={A["gate_iv"]}')
    f = lambda r: (float(r['p']), r['s'] * A['gate_mul'] * float(r['p']))
    return [f(r) for r in j['bids']], [f(r) for r in j['asks']]


def book_hyperliquid(A):
    j = get('https://api.hyperliquid.xyz/info', {'type': 'l2Book', 'coin': A['hl'], 'nSigFigs': A['hl_sig']})
    f = lambda r: (float(r['px']), float(r['sz']) * float(r['px']))
    return [f(r) for r in j['levels'][0]], [f(r) for r in j['levels'][1]]


def book_bybit(A):
    r = get(f'https://api.bybit.com/v5/market/orderbook?category=linear&symbol={A["usdt"]}&limit=500')['result']
    f = lambda x: (float(x[0]), float(x[1]) * float(x[0]))
    return [f(x) for x in r['b']], [f(x) for x in r['a']]


def book_bitget(A):
    d = get('https://api.bitget.com/api/v2/mix/market/merge-depth'
            f'?productType=usdt-futures&symbol={A.get("bitget_sym", A["usdt"])}&limit=max&precision={A.get("bitget_sc", "scale2")}')['data']
    f = lambda x: (float(x[0]), float(x[1]) * float(x[0]))
    return [f(x) for x in d['bids']], [f(x) for x in d['asks']]


def book_htx(A):
    t = get(f'https://api.hbdm.com/linear-swap-ex/market/depth?contract_code={A["htx"]}&type=step0')['tick']
    f = lambda x: (x[0], x[1] * A['htx_ct'] * x[0])
    return [f(x) for x in t['bids']], [f(x) for x in t['asks']]


def book_mexc(A):
    d = get(f'https://contract.mexc.com/api/v1/contract/depth/{A["mexc"]}?limit=2000')['data']
    f = lambda x: (x[0], x[1] * A['mexc_ct'] * x[0])
    return [f(x) for x in d['bids']], [f(x) for x in d['asks']]


def book_bitmex(A):
    j = get(f'https://www.bitmex.com/api/v1/orderBook/L2?symbol={A["bitmex"]}&depth=0')
    f = lambda x: (x['price'], x['size'])  # XBTUSD contracts are $1 each
    return [f(x) for x in j if x['side'] == 'Buy'], [f(x) for x in j if x['side'] == 'Sell']


def book_kraken(A):
    b = get(f'https://futures.kraken.com/derivatives/api/v3/orderbook?symbol={A["kraken"]}')['orderBook']
    f = lambda x: (x[0], x[1] * x[0])
    return [f(x) for x in b['bids']], [f(x) for x in b['asks']]


def book_cryptocom(A):
    d = get(f'https://api.crypto.com/exchange/v1/public/get-book?instrument_name={A["cdc"]}&depth=150')['result']['data'][0]
    f = lambda x: (float(x[0]), float(x[1]) * float(x[0]))
    return [f(x) for x in d['bids']], [f(x) for x in d['asks']]


def book_dydx(A):
    j = get(f'https://indexer.dydx.trade/v4/orderbooks/perpetualMarket/{A["dydx"]}')
    f = lambda x: (float(x['price']), float(x['size']) * float(x['price']))
    return [f(x) for x in j['bids']], [f(x) for x in j['asks']]


def book_bitfinex(A):
    rows = get(f'https://api-pub.bitfinex.com/v2/book/{A["bfx"]}/P0?len=250')
    return ([(r[0], r[2] * r[0]) for r in rows if r[2] > 0],
            [(r[0], -r[2] * r[0]) for r in rows if r[2] < 0])


BOOKS = [('binance', book_binance), ('okx', book_okx), ('bybit', book_bybit), ('bitget', book_bitget),
         ('gate', book_gate), ('htx', book_htx), ('mexc', book_mexc), ('deribit', book_deribit),
         ('bitmex', book_bitmex), ('kraken', book_kraken), ('cryptocom', book_cryptocom),
         ('hyperliquid', book_hyperliquid), ('dydx', book_dydx), ('bitfinex', book_bitfinex)]


# ---------- open interest (USD), 24h volume (USD), funding per 8h ----------

def mk_okx(A):
    oi = get(f'https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={A["okx"]}')['data'][0]
    t = get(f'https://www.okx.com/api/v5/market/ticker?instId={A["okx"]}')['data'][0]
    f = get(f'https://www.okx.com/api/v5/public/funding-rate?instId={A["okx"]}')['data'][0]
    return {'oi': float(oi['oiUsd']), 'vol': float(t['volCcy24h']) * float(t['last']), 'fund': float(f['fundingRate'])}


def mk_gate(A):
    t = get(f'https://api.gateio.ws/api/v4/futures/usdt/tickers?contract={A["gate"]}')[0]
    return {'oi': float(t['total_size']) * A['gate_mul'] * float(t['mark_price']), 'vol': float(t['volume_24h_quote']),
            'fund': float(t['funding_rate'])}


def mk_bitget(A):
    t = get(f'https://api.bitget.com/api/v2/mix/market/ticker?symbol={A.get("bitget_sym", A["usdt"])}&productType=usdt-futures')['data'][0]
    return {'oi': float(t['holdingAmount']) * float(t['lastPr']), 'vol': float(t['usdtVolume']), 'fund': float(t['fundingRate'])}


def mk_htx(A):
    oi = get('https://api.hbdm.com/linear-swap-api/v1/swap_his_open_interest'
             f'?contract_code={A["htx"]}&period=60min&amount_type=1&size=1')['data']['tick'][0]['value']
    t = get(f'https://api.hbdm.com/linear-swap-ex/market/detail/merged?contract_code={A["htx"]}')['tick']
    return {'oi': float(oi), 'vol': float(t['trade_turnover'])}


def mk_mexc(A):
    t = get(f'https://contract.mexc.com/api/v1/contract/ticker?symbol={A["mexc"]}')['data']
    return {'oi': t['holdVol'] * A['mexc_ct'] * t['lastPrice'], 'vol': t['amount24'], 'fund': t['fundingRate']}


def mk_deribit(A):
    r = get(f'https://www.deribit.com/api/v2/public/get_book_summary_by_instrument?instrument_name={A["deribit"]}')['result'][0]
    oi = r['open_interest'] if A['deribit_usd'] else r['open_interest'] * r['mark_price']
    return {'oi': oi, 'vol': r['volume_usd'], 'fund': r.get('funding_8h') or 0}


def mk_kraken(A):
    t = get(f'https://futures.kraken.com/derivatives/api/v3/tickers/{A["kraken"]}')['ticker']
    return {'oi': t['openInterest'] * t['last'], 'vol': t['volumeQuote']}


def mk_cryptocom(A):
    t = get(f'https://api.crypto.com/exchange/v1/public/get-tickers?instrument_name={A["cdc"]}')['result']['data'][0]
    return {'oi': float(t['oi']) * float(t['a']), 'vol': float(t['vv'])}


def mk_hyperliquid(A):
    meta, ctx = get('https://api.hyperliquid.xyz/info', {'type': 'metaAndAssetCtxs'})
    x = ctx[[u['name'] for u in meta['universe']].index(A['hl'])]
    return {'oi': float(x['openInterest']) * float(x['markPx']), 'vol': float(x['dayNtlVlm']), 'fund': float(x['funding']) * 8}


def mk_dydx(A):
    x = get(f'https://indexer.dydx.trade/v4/perpetualMarkets?ticker={A["dydx"]}')['markets'][A['dydx']]
    return {'oi': float(x['openInterest']) * float(x['oraclePrice']), 'vol': float(x['volume24H']),
            'fund': float(x['nextFundingRate']) * 8}


def mk_bitfinex(A):
    s = get(f'https://api-pub.bitfinex.com/v2/status/deriv?keys={A["bfx"]}')[0]
    t = get(f'https://api-pub.bitfinex.com/v2/ticker/{A["bfx"]}')
    return {'oi': s[18] * s[15], 'vol': t[7] * t[6], 'fund': s[12]}


def mk_binance(A):
    oi = get(f'https://fapi.binance.com/fapi/v1/openInterest?symbol={A["usdt"]}')
    t = get(f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={A["usdt"]}')
    f = get(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={A["usdt"]}')
    return {'oi': float(oi['openInterest']) * float(f['markPrice']), 'vol': float(t['quoteVolume']),
            'fund': float(f['lastFundingRate'])}


def mk_bybit(A):
    t = get(f'https://api.bybit.com/v5/market/tickers?category=linear&symbol={A["usdt"]}')['result']['list'][0]
    return {'oi': float(t['openInterestValue']), 'vol': float(t['turnover24h']), 'fund': float(t['fundingRate'])}


def mk_bitmex(A):
    x = get(f'https://www.bitmex.com/api/v1/instrument?symbol={A["bitmex"]}')[0]
    return {'oi': x['openInterest'], 'vol': x.get('foreignNotional24h') or 0, 'fund': x['fundingRate']}


MARKET = [('binance', mk_binance), ('okx', mk_okx), ('bybit', mk_bybit), ('bitget', mk_bitget), ('gate', mk_gate),
          ('htx', mk_htx), ('mexc', mk_mexc), ('deribit', mk_deribit), ('bitmex', mk_bitmex), ('kraken', mk_kraken),
          ('cryptocom', mk_cryptocom), ('hyperliquid', mk_hyperliquid), ('dydx', mk_dydx), ('bitfinex', mk_bitfinex)]


def okx_candles(A, bar, pages):
    base = 'https://www.okx.com/api/v5/market/'
    rows = get(base + f'candles?instId={A["okx"]}&bar={bar}&limit=300')['data']
    for _ in range(pages - 1):
        rows += get(base + f'history-candles?instId={A["okx"]}&bar={bar}&limit=100&after={rows[-1][0]}')['data']
    return sorted([int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), round(float(r[7]))] for r in rows)


def volume_weights(books, market, price):
    """Exchanges whose share of the depth within ±1% exceeds their share of 24h volume are scaled down to it."""
    depth = {k: sum(u for p, u in v['b'] if p >= price * 0.99) + sum(u for p, u in v['a'] if p <= price * 1.01)
             for k, v in books.items()}
    has = [k for k in depth if market.get(k, {}).get('vol')]
    dt, vt = sum(depth[k] for k in has), sum(market[k]['vol'] for k in has)
    return {k: min(1, (market[k]['vol'] / vt) / (depth[k] / dt)) if k in has and dt and vt and depth[k] else 1
            for k in depth}


def book_hist_entry(books, weights, price, ts, step):
    lo, hi = price * 0.94, price * 1.06
    m = {}
    for ex, v in books.items():
        w = weights.get(ex, 1)
        for side, i in (('b', 0), ('a', 1)):
            for p, u in v[side]:
                if lo <= p <= hi:
                    k = round(math.floor(p / step + 1e-9) * step, digits(step))
                    m.setdefault(k, [0, 0])[i] += u * w
    return {'t': ts // 3600000 * 3600000, 'b': [[k, round(a), round(b)] for k, (a, b) in sorted(m.items()) if a + b > 0]}


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith('--'):
        raise SystemExit('usage: glasssphere_snapshot.py DIR [--asset btc|eth|...|wld] [--prev URL ...]')
    out_dir = sys.argv[1]
    key = sys.argv[sys.argv.index('--asset') + 1] if '--asset' in sys.argv else 'btc'
    A = ASSETS[key]
    prev_urls = [sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == '--prev']

    candles = okx_candles(A, '1H', 2)[-400:]
    candles15 = okx_candles(A, '15m', 1)
    candles4h = okx_candles(A, '4H', 1)
    price = candles[-1][4]
    step, hist_step = A['bin'], A['hist_bin']

    books = {}
    for name, fn in BOOKS:
        if A.get(name, True) is None:
            continue
        try:
            bids, asks = fn(A)
            d = A.get('div', {}).get(name, 1)   # quoted per 1000 coins: back to the price of one
            if d != 1:
                bids, asks = [(p / d, u) for p, u in bids], [(p / d, u) for p, u in asks]
            books[name] = {'b': bin_levels(bids, 'b', step), 'a': bin_levels(asks, 'a', step)}
            print(f'book   {name:12s} {len(bids):5d} bids {len(asks):5d} asks')
        except Exception as e:
            print(f'book   {name:12s} skipped: {e}')
    market = {}
    for name, fn in MARKET:
        if A.get(name, True) is None:
            continue
        try:
            market[name] = {k: round(v, 8 if k == 'fund' else 0) for k, v in fn(A).items()}
        except Exception as e:
            print(f'market {name:12s} skipped: {e}')
    print('market', ', '.join(f'{k} OI ${v.get("oi", 0) / 1e9:.2f}B' for k, v in market.items()))

    st = get('https://api.gateio.ws/api/v4/futures/usdt/contract_stats'
             f'?contract={A["gate"]}&interval=1h&from={candles[0][0] // 1000}&limit=1000')
    oi = [[r['time'] * 1000, round(r['open_interest_usd'])] for r in st]
    taker = [[r['time'] * 1000, round(r['lsr_taker'] / (1 + r['lsr_taker']), 4)] for r in st]
    liq_real = [[r['time'] * 1000, round(r['long_liq_usd']), round(r['short_liq_usd'])] for r in st]

    for v in books.values():  # keep the file small: ±15% is more than the page ever shows
        v['b'] = [r for r in v['b'] if r[0] >= price * 0.85]
        v['a'] = [r for r in v['a'] if r[0] <= price * 1.15]
    ts = int(time.time() * 1000)
    entry = book_hist_entry(books, volume_weights(books, market, price), price, ts, hist_step)
    snap = {'ts': ts, 'asset': A['name'], 'bin': step, 'histBin': hist_step, 'price': price, 'books': books,
            'candles': candles, 'candles15': candles15, 'candles4h': candles4h, 'oi': oi, 'taker': taker,
            'liqReal': liq_real, 'market': market}
    print(f'{A["name"]}: {len(candles)} 1H candles, {len(oi)} Gate stat hours, price {price}, bins {step} / {hist_step}')

    prev = []
    for url in prev_urls:  # carry the book history of the live site forward, one entry per hour, last 48
        try:
            prev = get(url).get('bookHist', [])
            break
        except Exception as e:
            print(f'no previous data at {url}: {e}')
    hist = {e['t']: e for e in prev}
    hist[entry['t']] = entry
    snap['bookHist'] = [hist[k] for k in sorted(hist)][-48:]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, A['file'])
    json.dump(snap, open(path, 'w'), separators=(',', ':'))
    print(f'wrote {path} ({os.path.getsize(path) // 1024} KB, {len(snap["bookHist"])} hours of book history)')


if __name__ == '__main__':
    main()
