# GlassSphere reading — backtest

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

## 4. Longer test: Binance, 2022 – 2026

Binance BTCUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest (5-minute, sampled on the hour), taker buy volume and funding. 41616 hours, evaluated every 6h after the 400-bar warm-up (6865 points), the same model and leverage mix as above. No liquidation data in the archive, so section 1 cannot be repeated here.

### Signals against the next 24h return, IC per year

| Signal | 2022 | 2023 | 2024 | 2025 | 2026 | All |
|---|---|---|---|---|---|---|
| Momentum | -0.034 | +0.060 | +0.022 | -0.036 | +0.028 | +0.010 (t +0.4) |
| Liquidity pull | -0.019 | +0.040 | -0.010 | -0.025 | -0.013 | -0.001 (t -0.0) |
| Open interest | -0.023 | +0.093 | +0.039 | -0.024 | -0.049 | +0.010 (t +0.4) |
| Funding (contrarian) | +0.141 | +0.090 | -0.016 | +0.011 | +0.024 | +0.026 (t +1.1) |
| CVD (taker buying) | +0.031 | +0.102 | +0.044 | +0.069 | +0.012 | +0.042 (t +1.8) |

### Which pool is hit first, per year

| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |
|---|---|---|---|---|---|
| 2022 | 798 | 63% | 68% | 0.206 | 43% |
| 2023 | 736 | 58% | 67% | 0.209 | 49% |
| 2024 | 861 | 64% | 69% | 0.204 | 41% |
| 2025 | 828 | 56% | 65% | 0.207 | 43% |
| 2026 | 635 | 58% | 65% | 0.213 | 41% |
| All | 3858 | 60% | 67% | 0.207 | 44% |

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 15%) | 93 | 5% |
| 20%–40% (mean 32%) | 769 | 28% |
| 40%–60% (mean 50%) | 1666 | 49% |
| 60%–80% (mean 68%) | 1102 | 75% |
| 80%–100% (mean 85%) | 228 | 87% |

### Bigger targets: which is hit first, and trading toward the more likely one

Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon (average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, close at the horizon if neither is reached; 0.1% round-trip cost. Evaluated every 12h.

| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | Avg trade (gross) | Avg trade (after cost) | t |
|---|---|---|---|---|---|---|---|---|---|
| 1 d | 0.0 × | 3412 | 1.79% / 2.88% | 62% | 66% | 44% | -0.067% | -0.167% | -3.7 |
| 1 d | 0.3 × | 3409 | 2.02% / 2.95% | 59% | 63% | 49% | -0.048% | -0.148% | -3.1 |
| 3 d | 0.0 × | 3419 | 2.35% / 4.24% | 64% | 69% | 32% | -0.106% | -0.206% | -1.6 |
| 3 d | 0.3 × | 3419 | 3.10% / 4.49% | 59% | 64% | 41% | -0.092% | -0.192% | -1.4 |
| 3 d | 0.5 × | 3416 | 3.99% / 5.02% | 56% | 60% | 54% | -0.037% | -0.137% | -0.9 |
| 7 d | 0.3 × | 3420 | 4.35% / 7.00% | 60% | 66% | 35% | +0.214% | +0.114% | +0.4 |
| 7 d | 0.5 × | 3416 | 6.17% / 7.97% | 55% | 60% | 50% | +0.034% | -0.066% | -0.2 |
