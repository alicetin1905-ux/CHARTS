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


## Liquidity Trail Signals settings search (`scripts/liquidity_trail_backtest.py`)

Backtest of **Liquidity Trail Signals [BOSWaves]** on BTCUSDT.P: 780 settings (MA Length × ATR Length × Trail
Distance) × 13 exit/entry variants on 15m – 1D, 1×, after fees. Settings are picked on 2020 – Oct 2024 and checked on
Oct 2024 – Oct 2026. Only MA Length, ATR Length, Trail Distance, Entry Mode and the TP R values change the signals; the
zone, label and extend inputs are drawing options. Full tables: [`backtests/liquidity_trail_signals.md`](backtests/liquidity_trail_signals.md).

| Chart | MA / ATR / Trail | 2020–26 | Buy & hold | Max drawdown | Last 2 years (B&H +39%) | Trades / year |
|---|---|---|---|---|---|---|
| **12H** | **200 / 10 / 1.0** | **+1,114%** | +848% | 49% (B&H 77%) | **+95%** | ~7 |
| 6H | 200 / 10 / 1.0 | +1,743% | +1,187% | 50% | +84% | ~15 |
| 4H | 200 / 10 / 1.25 | +1,427% | +798% | 45% | +60% | ~20 |
| 4H | 28 / 15 / 1.25 (default) | +131% | +798% | 58% | −24% | ~56 |

- Signal Change entry, exit on the opposite flip (the trail at entry as the stop). TP1–3 exits do worse, and Trail
  Retest mode rarely triggers.
- The default settings lose money below 12H after fees. MA 150–200 is what works on 4H – 12H; ATR Length barely matters.
- 200 is the indicator's maximum MA Length, and 12H gives only ~40 trades in six years. Past results do not predict future ones.

Run it: `python3 scripts/liquidity_trail_backtest.py` (reuses `data/` from the ribbon backtest, ~10 minutes).

## High-frequency strategy search, Bybit data (`scripts/pf_search.py`, `scripts/rsi_pullback.py`)

Goal: a good profit factor with at least 250 trades a year on BYBIT BTCUSDT.P, on 15m, 1H, 4H, 12H and 1D.
Candles come from Bybit's public file server (`scripts/bybit_data.py`: MT4 15m candles to Nov 2024, then candles
built from Bybit's trade files; Bybit's REST API is geo-blocked here). Train Apr 2020 – Oct 2024, test Oct 2024 – Oct 2026.

`pf_search.py` tests ~7,600 variants per timeframe: own strategy types (RSI and Bollinger mean reversion, liquidity
sweeps, Donchian breakouts, opening-range breakouts, time of day, volume spikes, Liquidity Trail) and 14 community or
built-in TradingView strategies (UT Bot, Range Filter, Chandelier Exit, Supertrend, SSL Channel, Squeeze Momentum,
MACD, RSI, Bollinger, Keltner, Parabolic SAR, Hull, MA cross, Stochastic). Full tables: [`backtests/pf_search.md`](backtests/pf_search.md).

- **With market orders (taker 0.055% + slippage), nothing with 250+ trades a year has PF above 1 on the test window,
  on any timeframe.** The community strategies land at test PF 0.6 – 0.99. Fees are the problem: 250 round trips
  cost ~37% a year.
- 12H and 1D cannot reach 250 trades a year (730 / 365 candles a year).
- Mean reversion entered with **limit orders** (maker 0.02%) is the one thing that holds up.

`rsi_pullback.py` re-tests that with realistic fills (a limit fills only if price trades through it; stops and time
exits pay taker + slippage). Full tables: [`backtests/rsi_pullback.md`](backtests/rsi_pullback.md).

| 1H RSI(3) pullback, limit orders | Train 2020–24 | Test 2024–26 | Whole period |
|---|---|---|---|
| Profit factor | 1.14 | 1.11 | 1.13 |
| Trades / year | 256 | 268 | 260 |
| Win rate | 70% | 67% | 69% |
| Return / max drawdown | +69% / 20% | +15% / 11% | +95% / 20% |

Rules: RSI(3) < 25 and close > EMA 50 → buy limit 0.1 × ATR below the close (one candle); stop 3 × ATR; when RSI(3)
closes above 50, sell limit at that close, else at market next candle; max 48 candles. Shorts mirrored. Every year
2020 – 2026 had PF ≥ 1.00 (2023 flat). The edge is thin (~11% a year): it depends on limit fills, and with all-taker
fees it loses. TradingView version: [`strategies/rsi_pullback_limit.pine`](strategies/rsi_pullback_limit.pine).
