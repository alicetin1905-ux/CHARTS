# Liquidity map reading — backtest

Gate BTC_USDT perpetual, 1H, 161 days evaluated every 4h after a 400-bar warm-up (2026-05-13 → 2026-10-21). Train = first 120 days of data, test = the rest (620 / 348 points). Forward return over 24h. Overlapping windows: the t-stat uses n / 6 effective points.

The order book factor is not tested (no free order book history).

## 1. Liquidation model against real liquidations

Hourly correlation of the USD the model liquidates when price sweeps its levels with Gate's real liquidations (Gate OI only, so the level is not comparable, only the timing).

| Variant | Longs | Shorts | Total |
|---|---|---|---|
| candle split, mix 10/25/50/100× = 35/30/20/15% | 0.66 | 0.79 | 0.72 |
| candle + taker split, same mix | 0.68 | 0.79 | 0.73 |
| candle + taker split, low leverage (5/10/25/50× = 30/35/25/10%) **(used)** | 0.72 | 0.82 | 0.78 |
| candle + taker split, high leverage (25/50/100× = 40/35/25%) | 0.62 | 0.65 | 0.61 |

## 2. Signals against the next 24h return

| Signal | IC train | t | Hit train | IC test | t | Hit test | n test (|s|>0.2) |
|---|---|---|---|---|---|---|---|
| Momentum | +0.155 | +1.6 | 52% | -0.196 | -1.5 | 34% | 250 |
| Liquidity pull | +0.097 | +1.0 | 48% | -0.135 | -1.0 | 33% | 233 |
| Open interest | +0.022 | +0.2 | 50% | +0.014 | +0.1 | 53% | 259 |
| Funding (contrarian) | +0.045 | +0.4 | 52% | -0.110 | -0.8 | 53% | 206 |
| CVD (taker buying) | +0.108 | +1.1 | 57% | +0.014 | +0.1 | 39% | 142 |
| Score: Old weights (30/30/15, book left out), train | +0.127 | | 52% | | | | 523 |
| Score: Old weights (30/30/15, book left out), test | -0.154 | | 33% | | | | 245 |
| Score: IC-fitted on train, train | +0.165 | | 52% | | | | 524 |
| Score: IC-fitted on train, test | -0.164 | | 37% | | | | 282 |

Fitted weights: Momentum +0.59, Liquidity pull +0.00, Open interest +0.00, Funding (contrarian) +0.00, CVD (taker buying) +0.41

## 3. Which pool is hit first

At each point: the largest stop + liquidation pool above and below the price within one average day's range. Which is reached first within 24h?

| Part | Points with a hit | Pull side first | Closer side first | When they disagree: pull side wins |
|---|---|---|---|---|
| train | 354 | 63% | 69% | 45/110 = 41% |
| test | 185 | 56% | 65% | 18/54 = 33% |
| all | 539 | 61% | 68% | 63/164 = 38% |

A random walk without drift reaches the upper of two levels first with probability (distance to lower) / (sum of distances). Does that hold for these pools?

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 14%) | 9 | 0% |
| 20%–40% (mean 32%) | 108 | 28% |
| 40%–60% (mean 50%) | 228 | 45% |
| 60%–80% (mean 69%) | 154 | 71% |
| 80%–100% (mean 86%) | 40 | 92% |

Brier score 0.208 (0.25 = coin flip). Neither pool reached within 24h: 429 of 968 points.

