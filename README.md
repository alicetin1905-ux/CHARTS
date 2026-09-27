# BTC/USDT.P Chart Dashboard

A single-file dashboard (`index.html`) that shows the BTCUSDT perpetual in nine chart styles at once:
candlestick + volume, hollow candles, Heikin Ashi, OHLC bars, line with EMA 20/50, area, baseline,
step line and Renko.

- Live data from Binance USDⓈ-M Futures, falling back to OKX (BTC-USDT-SWAP) if Binance is blocked,
  and to demo data if neither is reachable. Refreshes every 3 seconds.
- Intervals: 1m, 5m, 15m, 1h, 4h, 1D. Light / dark / auto theme.
- Pan or zoom one chart and the others follow.

Open `index.html` in a browser — no build step. Charts use TradingView Lightweight Charts from a CDN.

## Liquidation Radar (`liquidations.html`)

A dark, neon-style BTC perpetual chart with an estimated liquidation map on the right. The map is
drawn on the chart's own price scale, so every bar lines up with the price next to it.

- A VPVR (volume profile of the visible range) sits inside the chart: blue = volume from candles that
  closed up, yellow = closed down, brighter rows = 70% value area, red line = point of control (POC).
  Toggle it with the VPVR button.
- Magenta bars below the price are long liquidations; cyan bars above are short liquidations.
- The two biggest clusters on each side are also marked on the chart as dotted lines.
- The boxes at the top show how much is estimated to be liquidated within 2% of the price, the largest
  cluster on each side, the long/short balance and open interest.
- Buttons switch the chart timeframe and which leverage levels (10×/25×/50×/100×) count.

The levels are estimates, not exchange data. Each 1H/4H candle's volume is treated as new positions at
that candle's average price, split across 10/25/50/100× leverage with 0.5% maintenance margin. Levels
that price has already crossed are removed.

### ETF flows panel

Under the chart: daily net flows for the US spot Bitcoin ETFs (all funds combined), cumulative net inflow,
5- and 20-day totals, net assets, BTC held, and a per-fund table (IBIT, FBTC, GBTC, ...). Data comes from
SoSoValue's public API.

- Opened as a file, the page reads SoSoValue directly and checks for new data every 30 minutes.
- On the hosted claude.ai page, which cannot reach SoSoValue, a daily scheduled job runs
  `scripts/etf_flows.py`'s logic and saves the result to the page's `etf/btc` database document.

## Crypto ETF Tracker (`etfs.html`)

One page for every US spot crypto ETF category: Bitcoin, Ethereum and Solana today, with XRP, Litecoin,
Hedera, Dogecoin and Chainlink checked on every load. They show up on their own once SoSoValue starts
reporting them. Until then, they're listed at the bottom as having no data yet.

- Top row: total ETF net assets across all coins, the latest day's combined net flow (split by coin),
  5- and 20-day flows, cumulative net inflow and value traded.
- A bar that shows each coin's share of total ETF net assets.
- A card per coin with net assets, the latest day's flow, 5-day, 20-day and cumulative flows, and coins held.
  Click a card to select that coin.
- For the selected coin: daily net flow bars and a cumulative net inflow chart that pan together, with
  1M/3M/6M/1Y/All ranges. Under them, a fund table (flow, net assets, share, cumulative inflow,
  premium/discount, fee, value traded).
- A table of the last 15 trading days of net flows, with a column per coin and a total.

Data comes from SoSoValue's public API and refreshes every 30 minutes. The page uses the same fallback as the
Liquidation Radar: where SoSoValue can't be reached, it reads the `etf/<coin>` database documents
(`etf/btc`, `etf/eth`, `etf/sol`, ...). You can build those documents with `scripts/etf_flows.py us-<coin>-spot`.
