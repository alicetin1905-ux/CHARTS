"""Stop / liquidation estimates and reading signals of index.html, in Python.

Mirrors the page's JavaScript so scripts/liquidity_backtest.py tests the same logic. Keep both in step.

candles: [[open_ms, open, high, low, close, volume_usd], ...] 1H, oldest first
oi:      {open_ms: open_interest_usd} 1H
taker:   {open_ms: taker buy share 0..1} 1H (optional)
"""
import math

LEV = [(5, .30), (10, .35), (25, .25), (50, .10)]  # leverage mix that best matched real liquidations (backtest)
MAINT = 0.004
HALF_LIFE_H = 168
WINDOW = 400  # 1H bars the page works with


def estimate_stops(candles, price):
    """Just beyond swing highs / lows that price has not traded through since."""
    n = len(candles)
    out = []
    if n < 10:
        return out
    now = candles[-1][0]
    suf_hi = [0.0] * (n + 1)
    suf_lo = [0.0] * (n + 1)
    suf_hi[n], suf_lo[n] = -math.inf, math.inf
    for i in range(n - 1, -1, -1):
        suf_hi[i] = max(suf_hi[i + 1], candles[i][2])
        suf_lo[i] = min(suf_lo[i + 1], candles[i][3])
    for L, mult in ((4, 1), (12, 2)):
        for i in range(L, n - L):
            h, l = candles[i][2], candles[i][3]
            is_h = all(candles[k][2] < h for k in range(i - L, i + L + 1) if k != i)
            is_l = all(candles[k][3] > l for k in range(i - L, i + L + 1) if k != i)
            if not is_h and not is_l:
                continue
            rec = 0.5 ** ((now - candles[i][0]) / 3.6e6 / HALF_LIFE_H)
            vol = sum(candles[k][5] for k in range(max(0, i - 2), min(n, i + 3)))
            w = vol * rec * mult
            if is_h and not suf_hi[i + 1] > h:
                out.append((h * 1.001, 'buy', w))
            if is_l and not suf_lo[i + 1] < l:
                out.append((l * 0.999, 'sell', w))
    ws = sorted(o[2] for o in out)
    med = ws[len(ws) // 2] if ws else 1
    lo, hi = price * 0.85, price * 1.15
    p = math.ceil(lo / 1000) * 1000
    while p <= hi:
        out.append((p + 50 if p > price else p - 50, 'buy' if p > price else 'sell', med * 0.25))
        p += 1000
    return [o for o in out if (o[1] == 'buy') == (o[0] > price)]


class LiqBook:
    """Liquidation levels from hourly OI growth, built bar by bar (the page's estimateLiqs).

    split='candle': long share of new OI = where the candle closed in its range.
    split='taker':  half that, half the taker buy share of the hour.
    """

    def __init__(self, split='taker', lev=LEV):
        self.pos = []  # [price, side, usd, bar_index]
        self.split, self.lev = split, lev

    def step(self, k, candle, oi_a, oi_b, taker=None):
        """Apply bar k (sweeps first), then add the OI change of that hour. Returns USD liquidated by the sweep."""
        _, o, h, l, c, _ = candle
        swept = {'long': 0.0, 'short': 0.0}
        keep = []
        for q in self.pos:
            if (q[1] == 'long' and l <= q[0]) or (q[1] == 'short' and h >= q[0]):
                swept[q[1]] += q[2]
            elif k - q[3] < WINDOW:
                keep.append(q)
        self.pos = keep
        if oi_a is None or oi_b is None:
            return swept
        d = oi_b - oi_a
        if d < 0:
            r = oi_b / oi_a
            for q in self.pos:
                q[2] *= r
            return swept
        share = (c - l) / (h - l) if h > l else 0.5
        if self.split == 'taker' and taker is not None:
            share = 0.5 * share + 0.5 * taker
        entry = (o + h + l + c) / 4
        for L, w in self.lev:
            self.pos.append([entry * (1 - 1 / L + MAINT), 'long', d * share * w, k])
            self.pos.append([entry * (1 + 1 / L - MAINT), 'short', d * (1 - share) * w, k])
        return swept


def ema(xs, n):
    k = 2 / (n + 1)
    e = xs[0]
    for x in xs:
        e = x * k + e * (1 - k)
    return e


def pools(stops, liqs, price, rng=0.06, B=100):
    """Stop score + liquidation size per $B level within ±rng, each normalised to its own maximum."""
    lo, hi = price * (1 - rng), price * (1 + rng)
    bins = {}
    for p, _, w in stops:
        if lo <= p <= hi:
            bins.setdefault(math.floor(p / B) * B, [0.0, 0.0])[0] += w
    for p, _, usd in liqs:
        if lo <= p <= hi:
            bins.setdefault(math.floor(p / B) * B, [0.0, 0.0])[1] += usd
    ms = max([v[0] for v in bins.values()] + [1e-9])
    ml = max([v[1] for v in bins.values()] + [1e-9])
    return [(k + B / 2, v[0] / ms + v[1] / ml) for k, v in bins.items()]


def pull_signal(pl, price):
    up = dn = 0.0
    for p, v in pl:
        w = v / max(abs(p / price - 1) * 100, 0.25)
        if p > price:
            up += w
        else:
            dn += w
    return (up - dn) / (up + dn) if up + dn else 0.0


def momentum_signal(closes):
    ret24 = closes[-1] / closes[-25] - 1
    ef, es = ema(closes, 20), ema(closes, 50)
    return max(-1, min(1, 0.5 * math.tanh(ret24 / 0.02) + 0.5 * math.tanh((ef / es - 1) / 0.004))), ret24


def oi_signal(oi_chg, ret24):
    if oi_chg is None or abs(oi_chg) < 0.005:
        return 0.0
    if oi_chg > 0:
        return math.copysign(1, ret24) * min(1, oi_chg / 0.03)
    return -math.copysign(1, ret24) * min(1, -oi_chg / 0.03) * 0.5


def funding_signal(rate_8h):
    """Contrarian: funding above the 0.01% baseline means crowded longs."""
    return -math.tanh((rate_8h - 0.0001) / 0.0003)


def cvd_signal(vols, buy_shares):
    """Net taker buying over 24h as a share of volume, scaled so ±10% = ±0.76."""
    tot = sum(vols)
    if not tot:
        return 0.0
    net = sum(v * (2 * s - 1) for v, s in zip(vols, buy_shares)) / tot
    return math.tanh(net / 0.1)


def r24_of(candles):
    """Average high - low of the last 14 blocks of 24 bars."""
    rngs = []
    e = len(candles)
    while e >= 24 and len(rngs) < 14:
        w = candles[e - 24:e]
        rngs.append(max(x[2] for x in w) - min(x[3] for x in w))
        e -= 24
    return sum(rngs) / len(rngs)


def first_targets(pl, price, r24, days=3, lo=0.3):
    """Largest pool above and below between lo x and 1 x the expected range of `days` (r24 x sqrt(days)),
    and the random-walk P(up first). The page's analyze() does the same."""
    rh = r24 * days ** 0.5
    def best(side):
        return max(side, key=lambda q: q[1]) if side else None
    up = best([q for q in pl if lo * rh < q[0] - price <= rh])
    dn = best([q for q in pl if lo * rh < price - q[0] <= rh])
    p_up = (price - dn[0]) / (up[0] - dn[0]) if up and dn else None
    return up, dn, p_up
