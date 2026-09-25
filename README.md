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
