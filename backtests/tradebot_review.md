# TradeBot (alicetin1905-ux/TradeBot) — independent backtest check

Generated with `scripts/tradebot_stress.js`, which reruns the bot's **own** backtest code (`scripts/backtest.js` →
`simulate` / `precompute`, live setup from its `backtest/LIVE_NOW.md`: score 65, pullback limit 0.3 ATR / 4h, 2.5%
risk, 10x, 5 slots, targets 1.5 / 2.5 / 3.5R closing 25 / 25 / 50%, BTC filter, fees) with extra realism switches.
OKX 1H candles, 2020-09-27 → 2026-10-06.

## 1. The bot's result reproduces

Its own run here: 2,000 → **64,765 USDT, PF 1.40, max drawdown 28.6%, 1,744 trades** (its report: 64,976 / 1.40 /
28.6% / 1,742; one more day of data).

## 2. Realistic fills on the bot's 21 coins

The bot's backtest fills stops exactly at the stop, targets and limit entries on a touch, and has no funding. On the
demo account the 6 stop exits so far filled **0.14% worse than the stop on average** (worst 0.33%).

```
TradeBot stress test · 21 coins · 2020-09-27 → 2026-10-06 · live rules (score 65, limit 0.3 ATR / 4h, 2.5% risk, 10x, 5 slots)
coins: SOL, DOGE, SUI, ENA, WLD, DYDX, GALA, EGLD, NEAR, XLM, BLUR, SAND, AXS, ZIL, CHZ, POPCAT, GRAM, AERO, 1000PEPE, KAITO, XRP

Compound: 2000 USDT, 2.5% risk, whole period. Per year: fresh 2000 USDT, $100 fixed risk (net $).

variant                                                              end $    PF  max DD  trades    2020    2021    2022    2023    2024    2025    2026
bot backtest as is (reproduces LIVE_NOW.md)                         64,765  1.40   28.6%    1744    1260    4415    7685    2637    5269    5754    3428
stops / flip exits 0.05% slippage                                   62,010  1.38   28.8%    1744    1243    4287    7501    1650   -1984    5511    3155
stops / flip exits 0.1% slippage                                    59,179  1.36   29.0%    1744    1227    4158    7318    1583   -1945    5268    2882
stops / flip exits 0.2% slippage                                    53,371  1.31   29.6%    1744    1194    3902    6951    1463   -1994    4781    2337
stop fills at the open when price gaps through it                   64,709  1.40   28.6%    1744    1260    4415    7685    2625    5251    5754    3428
targets + limit entries must trade 0.05% through                    58,725  1.36   28.4%    1701    1260    4589    6059    3008    3935    4895    3876
funding 0.01% / 8h (longs pay, shorts receive)                      65,726  1.40   28.7%    1744    1242    4423    7810    2938    4760    5890    3550
funding 0.01% / 8h paid on every position                           59,564  1.36   28.8%    1744    1226    4181    7348    1576   -1951    5369    2959
REALISTIC: 0.1% slip + gaps + 0.02% through + funding               56,062  1.34   29.3%    1726    1209    4332    6751    1516    3990    4424    2865
PESSIMISTIC: 0.2% slip + gaps + 0.05% through + funding on all      41,589  1.25   31.4%    1701    1160    3861    5014   -2020   -1969    3550    2342
```

Realistic fills cost about a seventh of the profit (PF 1.40 → 1.34). The per-year rows (fixed $100 risk on a fresh
2,000) show how fragile single years are: 0.05% slippage turns 2024 from +5,269 into −1,984 (that year's drawdown is
~80% either way).

## 3. The same rules on 21 coins the bot did not pick

The 21 live coins were chosen from a candidate scan ranked by this same backtest (`backtest/COIN_CANDIDATES.md`).
The bot's own report already shows the full candidate set at PF 1.04. Here: 21 large, liquid alts not in the bot's
list, same rules:

```
TradeBot stress test · 21 coins · 2020-09-27 → 2026-10-06 · live rules (score 65, limit 0.3 ATR / 4h, 2.5% risk, 10x, 5 slots)
coins: ETH, BNB, ADA, AVAX, LINK, DOT, LTC, BCH, ATOM, UNI, FIL, ETC, TRX, AAVE, ALGO, XTZ, CRV, SNX, NEO, THETA, ICP

Compound: 2000 USDT, 2.5% risk, whole period. Per year: fresh 2000 USDT, $100 fixed risk (net $).

variant                                                              end $    PF  max DD  trades    2020    2021    2022    2023    2024    2025    2026
bot backtest as is (reproduces LIVE_NOW.md)                         10,320  1.05   70.4%    2297     619    3832    2367   -1935    3935    1486   -1941
stops / flip exits 0.05% slippage                                    2,686  1.01   82.1%    2297     540    3612    2072   -1927    3194    1255   -1948
stops / flip exits 0.1% slippage                                     1,248  0.99   90.3%    2297     461    3393    1778   -1936    2976     467   -1951
stops / flip exits 0.2% slippage                                       349  0.97   96.7%    2297     -71    2955    1190   -1918    2009   -2009   -1958
stop fills at the open when price gaps through it                    9,993  1.05   70.6%    2297     615    3832    2365   -1935    3935    1486   -1941
targets + limit entries must trade 0.05% through                     2,163  1.00   86.4%    2241     471    2761    2699   -1924    1971    1058   -1973
funding 0.01% / 8h (longs pay, shorts receive)                      10,823  1.06   69.1%    2297     599    3802    2518   -1930    3868    1540   -1973
funding 0.01% / 8h paid on every position                            1,744  1.00   87.2%    2297     492    3451    1895   -1930    2999     523   -1948
REALISTIC: 0.1% slip + gaps + 0.02% through + funding                1,469  0.99   89.1%    2276     436    3463    2459   -1921    2165   -1966   -1961
PESSIMISTIC: 0.2% slip + gaps + 0.05% through + funding on all          83  0.95   99.1%    2241    -220    1527    1085   -1968     578   -1980   -1968
```

**On coins it was not tuned on, the strategy breaks even before costs and loses with realistic fills** (PF 1.05 →
0.99, drawdowns of 70 – 89%). Most of the backtested edge comes from choosing, after the fact, the coins on which it
had worked.

## 4. Live demo vs backtest, Oct 1 – 6, 2026

Live: 13 closed positions, 5 winners, PF 0.64, −146 USDT (balance 1,854). Backtest, same days: 5 trades, PF 0.11.
Both lost; only two trades overlap (KAITO short, BLUR long). The live bot still used score 50 on Oct 1 – 2 and its score
includes order-flow inputs the backtest lacks, so it is not a trade-for-trade replica. Five days say nothing about
the strategy either way.

## 5. Other things to know

- The +3,149% headline comes mostly from 2020 – 2022: positions are capped at 400 margin × 10 = 4,000 USDT, so once
  the balance has grown, each trade risks well under 1%. With a fixed risk per trade, drawdowns reach ~80% in a year.
- Many coins did not exist before 2023 – 2025 (SUI, ENA, WLD, BLUR, POPCAT, AERO, KAITO, GRAM), so early years run on
  fewer coins.
- 10x leverage on small alts with stops ~1.5 ATR away.
