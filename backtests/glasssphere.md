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

## 4. Longer test: Binance BNBUSDT, 2022 – 2026

Binance BNBUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest (5-minute, sampled on the hour), taker buy volume and funding. 41616 hours, evaluated every 6h after the 400-bar warm-up (6865 points), the same model and leverage mix as above. No liquidation data in the archive, so section 1 cannot be repeated here.

### Signals against the next 24h return, IC per year

| Signal | 2022 | 2023 | 2024 | 2025 | 2026 | All |
|---|---|---|---|---|---|---|
| Momentum | -0.036 | -0.059 | +0.007 | +0.011 | +0.031 | -0.009 (t -0.4) |
| Liquidity pull | -0.010 | +0.020 | +0.040 | +0.017 | -0.000 | +0.016 (t +0.7) |
| Open interest | -0.009 | -0.016 | -0.009 | +0.019 | -0.032 | -0.007 (t -0.3) |
| Funding (contrarian) | +0.032 | +0.014 | -0.083 | -0.007 | -0.108 | -0.030 (t -1.3) |
| CVD (taker buying) | +0.005 | -0.056 | -0.019 | +0.038 | -0.084 | -0.018 (t -0.7) |

### Which pool is hit first, per year

| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |
|---|---|---|---|---|---|
| 2022 | 842 | 58% | 69% | 0.203 | 39% |
| 2023 | 799 | 58% | 66% | 0.211 | 45% |
| 2024 | 841 | 61% | 71% | 0.193 | 42% |
| 2025 | 810 | 62% | 72% | 0.196 | 44% |
| 2026 | 639 | 57% | 70% | 0.199 | 41% |
| All | 3931 | 59% | 70% | 0.200 | 43% |

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 15%) | 86 | 12% |
| 20%–40% (mean 32%) | 660 | 24% |
| 40%–60% (mean 50%) | 1682 | 49% |
| 60%–80% (mean 69%) | 1183 | 74% |
| 80%–100% (mean 86%) | 320 | 92% |

### Bigger targets: which is hit first, and trading toward the more likely one

Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon (average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, close at the horizon if neither is reached; 0.1% round-trip cost. Evaluated every 12h.

| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | Avg trade (gross) | Avg trade (after cost) | t |
|---|---|---|---|---|---|---|---|---|---|
| 1 d | 0.0 × | 3420 | 1.97% / 3.18% | 62% | 70% | 45% | +0.046% | -0.054% | -1.1 |
| 1 d | 0.3 × | 3418 | 2.23% / 3.24% | 59% | 66% | 50% | +0.041% | -0.059% | -1.1 |
| 3 d | 0.0 × | 3420 | 2.68% / 4.89% | 64% | 71% | 33% | +0.025% | -0.075% | -0.5 |
| 3 d | 0.3 × | 3420 | 3.45% / 5.21% | 60% | 65% | 41% | -0.026% | -0.126% | -0.8 |
| 3 d | 0.5 × | 3420 | 4.46% / 5.73% | 56% | 63% | 52% | +0.065% | -0.035% | -0.2 |
| 7 d | 0.3 × | 3420 | 5.14% / 8.07% | 59% | 66% | 35% | +0.156% | +0.056% | +0.2 |
| 7 d | 0.5 × | 3384 | 7.04% / 8.81% | 55% | 59% | 50% | +0.008% | -0.092% | -0.2 |

## 4. Longer test: Binance XRPUSDT, 2022 – 2026

Binance XRPUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest (5-minute, sampled on the hour), taker buy volume and funding. 41496 hours, evaluated every 6h after the 400-bar warm-up (6845 points), the same model and leverage mix as above. No liquidation data in the archive, so section 1 cannot be repeated here.

### Signals against the next 24h return, IC per year

| Signal | 2022 | 2023 | 2024 | 2025 | 2026 | All |
|---|---|---|---|---|---|---|
| Momentum | -0.024 | -0.056 | +0.052 | +0.014 | +0.005 | +0.002 (t +0.1) |
| Liquidity pull | -0.008 | +0.033 | +0.001 | +0.075 | +0.024 | +0.028 (t +1.2) |
| Open interest | +0.016 | -0.046 | +0.016 | +0.047 | -0.018 | +0.005 (t +0.2) |
| Funding (contrarian) | +0.126 | +0.049 | -0.017 | +0.054 | -0.013 | +0.016 (t +0.7) |
| CVD (taker buying) | -0.001 | -0.067 | -0.008 | +0.079 | +0.041 | +0.003 (t +0.1) |

### Which pool is hit first, per year

| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |
|---|---|---|---|---|---|
| 2022 | 874 | 58% | 67% | 0.204 | 36% |
| 2023 | 800 | 60% | 68% | 0.208 | 45% |
| 2024 | 928 | 59% | 68% | 0.206 | 36% |
| 2025 | 910 | 62% | 70% | 0.199 | 37% |
| 2026 | 638 | 61% | 67% | 0.207 | 41% |
| All | 4150 | 60% | 68% | 0.205 | 39% |

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 16%) | 83 | 10% |
| 20%–40% (mean 32%) | 742 | 23% |
| 40%–60% (mean 50%) | 1949 | 47% |
| 60%–80% (mean 68%) | 1008 | 72% |
| 80%–100% (mean 88%) | 368 | 89% |

### Bigger targets: which is hit first, and trading toward the more likely one

Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon (average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, close at the horizon if neither is reached; 0.1% round-trip cost. Evaluated every 12h.

| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | Avg trade (gross) | Avg trade (after cost) | t |
|---|---|---|---|---|---|---|---|---|---|
| 1 d | 0.0 × | 3410 | 2.77% / 4.19% | 61% | 68% | 47% | -0.071% | -0.171% | -2.5 |
| 1 d | 0.3 × | 3410 | 3.03% / 4.26% | 59% | 64% | 51% | -0.093% | -0.193% | -2.7 |
| 3 d | 0.0 × | 3410 | 3.96% / 6.94% | 63% | 67% | 35% | -0.206% | -0.306% | -1.6 |
| 3 d | 0.3 × | 3410 | 4.88% / 7.30% | 59% | 61% | 42% | -0.186% | -0.286% | -1.4 |
| 3 d | 0.5 × | 3410 | 6.11% / 7.87% | 56% | 60% | 53% | +0.057% | -0.043% | -0.2 |
| 7 d | 0.3 × | 3410 | 6.82% / 9.77% | 58% | 63% | 32% | +0.189% | +0.089% | +0.2 |
| 7 d | 0.5 × | 3241 | 8.91% / 10.65% | 55% | 58% | 45% | +0.093% | -0.007% | -0.0 |

## 4. Longer test: Binance DOGEUSDT, 2022 – 2026

Binance DOGEUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest (5-minute, sampled on the hour), taker buy volume and funding. 41616 hours, evaluated every 6h after the 400-bar warm-up (6865 points), the same model and leverage mix as above. No liquidation data in the archive, so section 1 cannot be repeated here.

### Signals against the next 24h return, IC per year

| Signal | 2022 | 2023 | 2024 | 2025 | 2026 | All |
|---|---|---|---|---|---|---|
| Momentum | +0.025 | -0.067 | +0.086 | +0.030 | -0.008 | +0.029 (t +1.2) |
| Liquidity pull | -0.004 | -0.020 | +0.065 | +0.064 | +0.031 | +0.034 (t +1.4) |
| Open interest | +0.011 | -0.041 | +0.059 | +0.073 | +0.001 | +0.030 (t +1.2) |
| Funding (contrarian) | +0.037 | +0.035 | -0.077 | +0.030 | +0.047 | -0.021 (t -0.9) |
| CVD (taker buying) | +0.032 | -0.065 | +0.049 | +0.053 | +0.042 | +0.026 (t +1.1) |

### Which pool is hit first, per year

| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |
|---|---|---|---|---|---|
| 2022 | 945 | 57% | 68% | 0.198 | 31% |
| 2023 | 863 | 55% | 66% | 0.206 | 41% |
| 2024 | 1048 | 57% | 68% | 0.207 | 28% |
| 2025 | 1034 | 60% | 69% | 0.202 | 29% |
| 2026 | 640 | 55% | 67% | 0.209 | 41% |
| All | 4530 | 57% | 68% | 0.204 | 34% |

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 16%) | 81 | 7% |
| 20%–40% (mean 32%) | 772 | 24% |
| 40%–60% (mean 50%) | 2009 | 49% |
| 60%–80% (mean 69%) | 1180 | 71% |
| 80%–100% (mean 87%) | 488 | 90% |

### Bigger targets: which is hit first, and trading toward the more likely one

Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon (average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, close at the horizon if neither is reached; 0.1% round-trip cost. Evaluated every 12h.

| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | Avg trade (gross) | Avg trade (after cost) | t |
|---|---|---|---|---|---|---|---|---|---|
| 1 d | 0.0 × | 3420 | 3.30% / 5.11% | 62% | 67% | 45% | -0.058% | -0.158% | -2.0 |
| 1 d | 0.3 × | 3420 | 3.60% / 5.20% | 59% | 64% | 50% | -0.052% | -0.152% | -1.8 |
| 3 d | 0.0 × | 3420 | 4.70% / 8.50% | 64% | 70% | 34% | -0.041% | -0.141% | -0.6 |
| 3 d | 0.3 × | 3416 | 5.77% / 8.86% | 59% | 65% | 42% | -0.025% | -0.125% | -0.5 |
| 3 d | 0.5 × | 3379 | 7.66% / 9.50% | 55% | 61% | 54% | +0.103% | +0.003% | +0.0 |
| 7 d | 0.3 × | 3396 | 8.03% / 10.94% | 57% | 62% | 30% | +0.156% | +0.056% | +0.1 |
| 7 d | 0.5 × | 3127 | 10.21% / 12.40% | 54% | 57% | 42% | +0.046% | -0.054% | -0.1 |

## 4. Longer test: Binance SUIUSDT, 2023 – 2026

Binance SUIUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest (5-minute, sampled on the hour), taker buy volume and funding. 29912 hours, evaluated every 6h after the 400-bar warm-up (4915 points), the same model and leverage mix as above. No liquidation data in the archive, so section 1 cannot be repeated here.

### Signals against the next 24h return, IC per year

| Signal | 2023 | 2024 | 2025 | 2026 | All |
|---|---|---|---|---|---|
| Momentum | -0.044 | +0.005 | -0.022 | +0.073 | +0.003 (t +0.1) |
| Liquidity pull | +0.022 | +0.038 | +0.064 | +0.030 | +0.044 (t +1.5) |
| Open interest | -0.017 | +0.027 | -0.021 | +0.086 | +0.018 (t +0.6) |
| Funding (contrarian) | -0.022 | +0.114 | +0.062 | -0.054 | +0.043 (t +1.5) |
| CVD (taker buying) | -0.056 | +0.006 | +0.035 | -0.037 | +0.000 (t +0.0) |

### Which pool is hit first, per year

| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |
|---|---|---|---|---|---|
| 2023 | 636 | 58% | 68% | 0.207 | 29% |
| 2024 | 1230 | 54% | 68% | 0.203 | 16% |
| 2025 | 1125 | 57% | 66% | 0.208 | 23% |
| 2026 | 729 | 62% | 69% | 0.203 | 33% |
| All | 3720 | 57% | 68% | 0.205 | 24% |

| Predicted P(up first) | Points | Actually up first |
|---|---|---|
| 0%–20% (mean 14%) | 95 | 11% |
| 20%–40% (mean 32%) | 661 | 24% |
| 40%–60% (mean 50%) | 1619 | 49% |
| 60%–80% (mean 68%) | 894 | 68% |
| 80%–100% (mean 88%) | 451 | 88% |

### Bigger targets: which is hit first, and trading toward the more likely one

Targets: the largest pool above and below between *min* × and 1 × the expected range of the horizon (average daily range × √days). Trade toward the more likely (nearer) target, stop at the other one, close at the horizon if neither is reached; 0.1% round-trip cost. Evaluated every 12h.

| Horizon | Min distance | Points | Target / stop (median) | Predicted P(first) | Actual | Neither | Avg trade (gross) | Avg trade (after cost) | t |
|---|---|---|---|---|---|---|---|---|---|
| 1 d | 0.0 × | 2445 | 3.86% / 6.27% | 62% | 68% | 42% | -0.092% | -0.192% | -1.7 |
| 1 d | 0.3 × | 2445 | 4.28% / 6.41% | 59% | 64% | 47% | -0.086% | -0.186% | -1.5 |
| 3 d | 0.0 × | 2445 | 5.06% / 9.64% | 65% | 70% | 23% | +0.178% | +0.078% | +0.3 |
| 3 d | 0.3 × | 2445 | 6.62% / 10.05% | 59% | 64% | 33% | +0.106% | +0.006% | +0.0 |
| 3 d | 0.5 × | 2438 | 9.14% / 11.16% | 55% | 59% | 47% | +0.157% | +0.057% | +0.2 |
| 7 d | 0.3 × | 2445 | 9.10% / 11.72% | 56% | 60% | 20% | +0.540% | +0.440% | +0.6 |
| 7 d | 0.5 × | 2110 | 11.71% / 13.52% | 53% | 56% | 33% | +0.409% | +0.309% | +0.4 |
