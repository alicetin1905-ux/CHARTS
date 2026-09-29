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

## EMA Ribbon Flip strategy (`strategies/ema_ribbon_flip.pine`)

A TradingView strategy for BYBIT:BTCUSDT.P built on the 8 EMAs of **EMA Ribbon [Krypt]** (20/25/30/35/40/45/50/55).

- **Long**: the ribbon was squeezed into (almost) one line within the last 10 bars (width ≤ 0.6% of price),
  then all 8 EMAs line up bullish: EMA 20 on top, EMA 55 at the bottom.
- **Short**: the same, with all 8 EMAs lined up bearish.
- **Exit**: the opposite flip closes the trade and opens the other side. A 2.5 × ATR(14) stop cuts losing trades.
- Signals on the candle close, filled at the next open. Bybit taker fee 0.055% is included.
- Settings can switch the trigger to a simple EMA 20 × EMA 55 cross, turn off the squeeze filter, trade one
  direction only, add a take-profit or use up to 10× leverage. Alerts fire on each flip.

To use it: TradingView → Pine Editor → paste the file → *Add to chart* on BYBIT:BTCUSDT.P, **4H**.

### Backtest (`scripts/ribbon_backtest.py`, 1×, 0.075% cost per side)

| Chart | Period | Strategy | Buy & hold | Trades | Win rate | Profit factor | Max drawdown |
|---|---|---|---|---|---|---|---|
| 4H | Sep 2022 – Sep 2026 | **+141.7%** | +340% | 93 | 28% | 1.65 | 28.7% |
| 4H, May 2025 – Sep 2026 only | | +34.1% | −21% | 32 | 38% | 1.62 | 16.9% |
| 1H | Oct 2023 – Sep 2026 | −24.8% | +196% | 336 | 22% | 1.00 | 67.5% |
| 15m | Oct 2025 – Sep 2026 | −26.3% | −27% | 432 | 23% | 0.92 | 35.9% |

Use it on 4H. On 1H and 15m the ribbon flips too often and fees eat the edge. It was profitable on 4H in each
third of the test period, but it earns less than holding in a strong bull market. Most trades lose small and a
few trends pay for them, so expect long losing streaks. Past results do not predict future ones.
