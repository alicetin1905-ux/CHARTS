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

Under the chart, with a BTC / ETH switch: daily net flows for the US spot Bitcoin or Ether ETFs (all funds combined), cumulative net inflow,
5- and 20-day totals, net assets, coins held, and a per-fund table (IBIT, FBTC, ... or ETHA, FETH, ...). Data comes from
SoSoValue's public API.

- Opened as a file, the page reads SoSoValue directly and checks for new data every 30 minutes.
- On the hosted claude.ai page, which cannot reach SoSoValue, a daily scheduled job runs
  `scripts/etf_flows.py btc` and `scripts/etf_flows.py eth` and saves the results to the page's `etf/btc` and
  `etf/eth` database documents.

## EMA Ribbon flip backtest (`scripts/ribbon_backtest.py`)

Backtest of trading BTCUSDT.P on the flips of the **EMA Ribbon [Krypt]** (EMAs 20–55): long when all 8 EMAs
turn bullish in order, short when they turn bearish, reversed on the opposite flip. Tested on 5m, 15m, 30m, 1H,
2H, 4H, 6H, 12H and 1D, in four variants. Full tables: [`backtests/ema_ribbon_flip.md`](backtests/ema_ribbon_flip.md).

Squeeze variant (flip after the EMAs were on one line, 2.5 × ATR stop), 1×, after fees:

| Chart | Last 2 years | Aug 2020 – Sep 2026 | Trades / year | Worst year | Max drawdown (2020–26) |
|---|---|---|---|---|---|
| 5m – 2H | −42% to −97% | — | 55 – 1,300 | — | — |
| 4H | +102% | +371% | ~24 | −8% | 45% |
| 6H | +57% | +1,168% | ~16 | −34% (2022) | 35% |
| **12H** | **+97%** | **+912%** | **~9** | **−9% (2022)** | **40%** |
| 1D | +15% | +384% | ~4 | −18% (2024) | 49% |
| Buy & hold | +32% | +607% | | −64% (2022) | |

- Below 4H the strategy loses money. Fees eat the edge (5m: +63% before costs, −97% after).
- 12H is the most consistent: highest profit per unit of loss (profit factor 3.25) and small losing years.
- Daily flips too late on 20–55-day EMAs and gives only ~4 trades a year.
- Much of the 2020–26 gain came from the late-2020 rally; from 2021 on, 12H made about +220% vs +191% for holding.
- The variant was picked after seeing the results, so expect worse live. Past results do not predict future ones.

Run it: `python3 scripts/ribbon_backtest.py` (downloads candles into `data/`, ~10 minutes the first time).

