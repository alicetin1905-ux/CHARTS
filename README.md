# BTC/USDT.P Chart Dashboard

A single-file dashboard (`index.html`) that shows the BTCUSDT perpetual in nine chart styles at once:
candlestick + volume, hollow candles, Heikin Ashi, OHLC bars, line with EMA 20/50, area, baseline,
step line and Renko.

- Live data from Binance USDⓈ-M Futures, falling back to OKX (BTC-USDT-SWAP) if Binance is blocked,
  and to demo data if neither is reachable. Refreshes every 3 seconds.
- Intervals: 1m, 5m, 15m, 1h, 4h, 1D. Light / dark / auto theme.
- Pan or zoom one chart and the others follow.

Open `index.html` in a browser — no build step. Charts use TradingView Lightweight Charts from a CDN.

## ETF (`flow-radar.html`)

A BTC perpetual chart with a VPVR volume profile and daily ETF inflow/outflow, styled to match the ATLAS Suite boards.

- A VPVR (volume profile of the visible range) sits inside the chart: blue = volume from candles that
  closed up, yellow = closed down, brighter rows = 70% value area, red line = point of control (POC).
  Toggle it with the VPVR button. The boxes at the top show the POC, value area high/low and open interest.
- Buttons switch the chart timeframe (5M, 15M, 1H, 4H).

### ETF inflow/outflow panel

Under the chart, with a BTC / ETH / SOL switch: daily net flows for the US spot Bitcoin, Ether or Solana ETFs (all funds combined), cumulative net inflow,
5- and 20-day totals, net assets, coins held, and a per-fund table (IBIT, FBTC, ... / ETHA, FETH, ... / BSOL, FSOL, ...). Data comes from
SoSoValue's public API.

- Opened as a file, the page reads SoSoValue directly and checks for new data every 30 minutes.
- On the hosted claude.ai page, which cannot reach SoSoValue, a daily scheduled job runs
  `scripts/etf_flows.py` for btc, eth and sol and saves the results to the page's `etf/btc`, `etf/eth` and
  `etf/sol` database documents. SoSoValue has no data for other coins' ETFs (XRP, DOGE, LTC, ...).

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


## Volume Anchor Level + Bias (`pine/unbiased_level.pine`)

A TradingView Pine Script v6 indicator rebuilt from the public description of **Unbiased Level Pro** (askaitrade,
invite-only and closed-source). It is not the original code, so its values can differ from the original's.

- **Level:** a horizontal line at the highest-volume bar of the last N bars (default 20, range 2–100). Wick anchor = low of an up
  bar / high of a down bar; body anchor = the bar's open.
- **Bias:** over the same N bars, up bar + rising volume = 3 bull; up bar + falling volume = 1 bull + 2 bear; down bar + rising
  volume = 3 bear; down bar + falling volume = 1 bear + 2 bull. Shown as bull % / bear %.
- **Table:** bias for 1m, 5m, 15m, 1H, 4H and the chart timeframe (timeframes and periods can be changed). "Aligned ▲" =
  bullish bias with price above that timeframe's level, "Aligned ▼" = bearish bias with price below it.
- **Alerts:** 41 conditions (level crosses, new level, bias flips, 55/60/65/70% thresholds, per-timeframe and all-timeframe
  alignment). Each fires once when its condition becomes true; create the alerts with "Once Per Bar Close".

Install: TradingView → Pine Editor → paste the file → Add to chart. Needs a symbol with volume data. The other-timeframe
values can change until their bars close.

## Trend Signals + Overlays (`pine/trend_signals_overlays.pine`)

A TradingView Pine Script v6 toolkit modelled on the public feature list of **LuxAlgo Signals & Overlays** (paid, closed-source).
It is written from scratch with its own math, so its signals will not match LuxAlgo's.

| Feature | How it is calculated here |
|---|---|
| Confirmation signals | Supertrend (ATR 10, factor = sensitivity / 4) flips. "+" = strong: the flip agrees with the Trend Tracer. Exits (blue/orange ×): RSI 14 leaving 70/30 in the trend's direction |
| Contrarian signals | RSI (length = sensitivity) crossing back over 30 / under 70. "+" = it was below 20 / above 80 in the last 5 bars. One exit per signal |
| Classifier 1–4 | ADX at the signal compared with the quartiles of the last 200 signals; each rating can be hidden |
| Autopilot / optimal sensitivity | Best of sensitivity 10–20 by the flip strategy's return over the last 250 bars |
| Smart Trail | Trailing stop on a modified true range (outsized bars and gaps capped), with a support (blue) / resistance (red) zone |
| Reversal Zones | 2–3 × ATR 20 bands around EMA 20 of hlc3 |
| Trend Tracer / Trend Catcher | Slow trailing stop on EMA(sensitivity) / Kaufman adaptive average (blue up, orange down) |
| Neo Cloud | Midpoints of 2× and 4× sensitivity highs/lows; brighter the older the trend |
| Candle coloring | Confirmation simple, confirmation gradient, contrarian gradient |
| TP/SL | TP1–3 at 1–3 × distance × ATR 14 from the signal close, SL at 1 ×; re-anchors when price closes beyond TP3 |
| Dashboard | Sensitivity, optimal sensitivity, trend, trend strength (2 × ADX), volatility (ATR 14 / ATR 100), squeeze (BB width rank), volume sentiment (−100…100) |

Presets (Trend Trader, Scalper, Swing Trader) switch on the matching features. Signals appear when the bar closes. 20 alert
conditions plus "Any alert() function call" for signals with their rating.
