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

## 4. Longer test: Binance BTCUSDT, 2022 – 2026

Binance BTCUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest (5-minute, sampled on the hour), taker buy volume and funding. 41616 hours, evaluated every 6h after the 400-bar warm-up (6865 points), the same model and leverage mix as above. No liquidation data in the archive, so section 1 cannot be repeated here.

### Signals against the next 24h return, IC per year

| Signal | 2022 | 2023 | 2024 | 2025 | 2026 | All |
|---|---|---|---|---|---|---|
| Momentum | -0.034 | +0.060 | +0.022 | -0.036 | +0.028 | +0.010 (t +0.4) |
| Liquidity pull | -0.033 | +0.033 | -0.007 | -0.025 | -0.014 | -0.005 (t -0.2) |
| Open interest | -0.023 | +0.093 | +0.039 | -0.024 | -0.049 | +0.010 (t +0.4) |
| Funding (contrarian) | +0.141 | +0.090 | -0.016 | +0.011 | +0.024 | +0.026 (t +1.1) |
| CVD (taker buying) | +0.031 | +0.102 | +0.044 | +0.069 | +0.012 | +0.042 (t +1.8) |

### Which pool is hit first, per year

| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |
|---|---|---|---|---|---|
| 2022 | 773 | 59% | 68% | 0.204 | 44% |
| 2023 | 743 | 56% | 66% | 0.213 | 49% |
| 2024 | 857 | 63% | 69% | 0.204 | 41% |
| 2025 | 833 | 56% | 65% | 0.207 | 43% |
| 2026 | 635 | 58% | 65% | 0.213 | 41% |
| All | 3841 | 59% | 67% | 0.208 | 44% |

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 15%) | 87 | 7% |
| 20%–40% (mean 32%) | 717 | 28% |
| 40%–60% (mean 50%) | 1664 | 49% |
| 60%–80% (mean 69%) | 1098 | 74% |
| 80%–100% (mean 86%) | 275 | 88% |

### Bigger targets: which is hit first, and trading toward the more likely one

Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon (average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, close at the horizon if neither is reached; 0.1% round-trip cost. Evaluated every 12h.

| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | Avg trade (gross) | Avg trade (after cost) | t |
|---|---|---|---|---|---|---|---|---|---|
| 1 d | 0.0 × | 3420 | 1.79% / 2.88% | 62% | 66% | 44% | -0.055% | -0.155% | -3.4 |
| 1 d | 0.3 × | 3418 | 2.03% / 2.97% | 59% | 63% | 50% | -0.051% | -0.151% | -3.2 |
| 3 d | 0.0 × | 3420 | 2.35% / 4.29% | 65% | 69% | 32% | -0.095% | -0.195% | -1.6 |
| 3 d | 0.3 × | 3420 | 3.13% / 4.52% | 59% | 64% | 41% | -0.044% | -0.144% | -1.0 |
| 3 d | 0.5 × | 3420 | 4.00% / 5.05% | 56% | 59% | 54% | -0.056% | -0.156% | -1.0 |
| 7 d | 0.3 × | 3420 | 4.40% / 7.00% | 59% | 65% | 35% | +0.176% | +0.076% | +0.2 |
| 7 d | 0.5 × | 3418 | 6.18% / 7.96% | 55% | 61% | 50% | +0.107% | +0.007% | +0.0 |

## 4. Longer test: Binance ETHUSDT, 2022 – 2026

Binance ETHUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest (5-minute, sampled on the hour), taker buy volume and funding. 41616 hours, evaluated every 6h after the 400-bar warm-up (6865 points), the same model and leverage mix as above. No liquidation data in the archive, so section 1 cannot be repeated here.

### Signals against the next 24h return, IC per year

| Signal | 2022 | 2023 | 2024 | 2025 | 2026 | All |
|---|---|---|---|---|---|---|
| Momentum | -0.003 | -0.026 | +0.044 | -0.002 | +0.001 | +0.006 (t +0.3) |
| Liquidity pull | -0.015 | -0.008 | +0.064 | +0.024 | -0.063 | +0.007 (t +0.3) |
| Open interest | -0.029 | -0.008 | -0.033 | -0.066 | +0.000 | -0.032 (t -1.3) |
| Funding (contrarian) | +0.068 | +0.071 | +0.030 | +0.033 | +0.067 | +0.031 (t +1.3) |
| CVD (taker buying) | +0.034 | -0.009 | +0.022 | +0.044 | +0.029 | +0.018 (t +0.8) |

### Which pool is hit first, per year

| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |
|---|---|---|---|---|---|
| 2022 | 924 | 57% | 67% | 0.206 | 33% |
| 2023 | 802 | 57% | 66% | 0.211 | 45% |
| 2024 | 878 | 66% | 71% | 0.199 | 40% |
| 2025 | 908 | 56% | 67% | 0.204 | 38% |
| 2026 | 663 | 56% | 72% | 0.192 | 39% |
| All | 4175 | 59% | 68% | 0.203 | 39% |

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 15%) | 90 | 4% |
| 20%–40% (mean 32%) | 753 | 25% |
| 40%–60% (mean 50%) | 1785 | 50% |
| 60%–80% (mean 69%) | 1232 | 74% |
| 80%–100% (mean 87%) | 315 | 90% |

### Bigger targets: which is hit first, and trading toward the more likely one

Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon (average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, close at the horizon if neither is reached; 0.1% round-trip cost. Evaluated every 12h.

| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | Avg trade (gross) | Avg trade (after cost) | t |
|---|---|---|---|---|---|---|---|---|---|
| 1 d | 0.0 × | 3420 | 2.31% / 3.81% | 62% | 67% | 43% | -0.062% | -0.162% | -2.7 |
| 1 d | 0.3 × | 3420 | 2.64% / 3.87% | 59% | 64% | 49% | -0.042% | -0.142% | -2.2 |
| 3 d | 0.0 × | 3420 | 3.21% / 6.13% | 65% | 70% | 32% | -0.063% | -0.163% | -1.0 |
| 3 d | 0.3 × | 3420 | 4.27% / 6.51% | 59% | 64% | 42% | -0.124% | -0.224% | -1.2 |
| 3 d | 0.5 × | 3420 | 5.48% / 7.14% | 56% | 60% | 54% | -0.066% | -0.166% | -0.8 |
| 7 d | 0.3 × | 3420 | 6.20% / 9.20% | 59% | 61% | 34% | -0.115% | -0.215% | -0.5 |
| 7 d | 0.5 × | 3374 | 8.51% / 10.13% | 55% | 55% | 48% | -0.329% | -0.429% | -0.9 |

## 4. Longer test: Binance SOLUSDT, 2022 – 2026

Binance SOLUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest (5-minute, sampled on the hour), taker buy volume and funding. 41496 hours, evaluated every 6h after the 400-bar warm-up (6845 points), the same model and leverage mix as above. No liquidation data in the archive, so section 1 cannot be repeated here.

### Signals against the next 24h return, IC per year

| Signal | 2022 | 2023 | 2024 | 2025 | 2026 | All |
|---|---|---|---|---|---|---|
| Momentum | +0.022 | +0.065 | -0.026 | -0.008 | +0.002 | +0.021 (t +0.9) |
| Liquidity pull | +0.002 | +0.028 | +0.021 | +0.001 | -0.023 | +0.014 (t +0.6) |
| Open interest | +0.010 | +0.043 | +0.004 | +0.013 | +0.017 | +0.021 (t +0.9) |
| Funding (contrarian) | -0.012 | +0.094 | -0.018 | -0.024 | -0.068 | -0.012 (t -0.5) |
| CVD (taker buying) | +0.063 | +0.108 | -0.019 | +0.072 | +0.024 | +0.053 (t +2.2) |

### Which pool is hit first, per year

| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |
|---|---|---|---|---|---|
| 2022 | 1075 | 53% | 67% | 0.204 | 21% |
| 2023 | 1007 | 60% | 66% | 0.209 | 31% |
| 2024 | 1034 | 62% | 66% | 0.211 | 29% |
| 2025 | 1005 | 58% | 69% | 0.200 | 31% |
| 2026 | 696 | 61% | 69% | 0.198 | 36% |
| All | 4817 | 59% | 67% | 0.205 | 29% |

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 16%) | 114 | 10% |
| 20%–40% (mean 32%) | 858 | 29% |
| 40%–60% (mean 50%) | 2116 | 51% |
| 60%–80% (mean 69%) | 1247 | 72% |
| 80%–100% (mean 88%) | 482 | 91% |

### Bigger targets: which is hit first, and trading toward the more likely one

Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon (average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, close at the horizon if neither is reached; 0.1% round-trip cost. Evaluated every 12h.

| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | Avg trade (gross) | Avg trade (after cost) | t |
|---|---|---|---|---|---|---|---|---|---|
| 1 d | 0.0 × | 3410 | 3.33% / 5.22% | 62% | 70% | 40% | +0.026% | -0.074% | -0.9 |
| 1 d | 0.3 × | 3410 | 3.68% / 5.33% | 59% | 65% | 46% | -0.005% | -0.105% | -1.2 |
| 3 d | 0.0 × | 3410 | 4.42% / 8.46% | 65% | 71% | 29% | +0.061% | -0.039% | -0.2 |
| 3 d | 0.3 × | 3385 | 5.87% / 8.81% | 59% | 65% | 39% | +0.047% | -0.053% | -0.2 |
| 3 d | 0.5 × | 3349 | 7.71% / 9.61% | 55% | 60% | 51% | +0.136% | +0.036% | +0.1 |
| 7 d | 0.3 × | 3360 | 8.10% / 10.82% | 57% | 59% | 25% | -0.108% | -0.208% | -0.4 |
| 7 d | 0.5 × | 3098 | 10.19% / 12.45% | 54% | 55% | 38% | -0.114% | -0.214% | -0.3 |
