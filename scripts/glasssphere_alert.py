"""Hourly check for GlassSphere: the 3-day first-target verdict (the default of the page) now and an hour ago, and alerts.

Reads a data file written by `glasssphere_snapshot.py DIR --asset ...` (BTC, ETH, SOL, ..., WLD) and prints one status line,
then one line per alert starting with "MELDING:". .github/workflows/pages.yml sends the alerts to ntfy, one message
per asset. Alerts:
  - the price is within 0.3% of the cluster it is most likely to reach first
  - a cluster from an hour ago was hit in the last hour
  - the most likely first target switched side since an hour ago
  - Gate's real liquidations in the last hour were over a minimum per asset (LIQ_MIN, BTC $1M) and 3x the hourly
    average of the day before

    python3 scripts/glasssphere_alert.py DIR/glasssphere-data.json      (or glasssphere-eth.json, ...)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glasssphere_model as M  # noqa: E402

DECIMALS = {'BTC': 0, 'ETH': 1, 'SOL': 2, 'BNB': 2, 'XRP': 4, 'DOGE': 5, 'SUI': 4, 'ENA': 5, 'GALA': 6, 'PEPE': 9,
            'WLD': 4}   # as the page shows prices
# smallest liquidation wave worth an alert, USD per hour (Gate's liquidations in the smaller coins are small)
LIQ_MIN = {'BTC': 1e6, 'ETH': 5e5, 'SOL': 2e5, 'BNB': 2.5e4, 'XRP': 1e5, 'DOGE': 1e5, 'SUI': 5e4, 'ENA': 2.5e4,
           'GALA': 1e4, 'PEPE': 1e5, 'WLD': 5e4}


def verdict(candles, oi, taker):
    price = candles[-1][4]
    book = M.LiqBook('taker')
    for k, c in enumerate(candles):
        book.step(k, c, oi.get(c[0]), oi.get(c[0] + 3600000), taker.get(c[0]))
    stops = M.estimate_stops(candles, price)
    pl = M.pools(stops, [(q[0], q[1], q[2]) for q in book.pos], price, rng=0.15)
    up, dn, p_up = M.first_targets(pl, price, M.r24_of(candles), days=3)
    return price, up, dn, p_up


def label(p_up):
    if p_up is None:
        return 'Geen doelen'
    prob = max(p_up, 1 - p_up)
    return 'Onbeslist' if prob < 0.6 else 'Eerst omhoog' if p_up > 0.5 else 'Eerst omlaag'


DEC = 0


def fmt(p):
    """Dutch notation: 81.700 / 2.399,5 / 104,36"""
    return f'{p:,.{DEC}f}'.replace(',', ' ').replace('.', ',').replace(' ', '.')


def main():
    global DEC
    snap = json.load(open(sys.argv[1]))
    asset = snap.get('asset', 'BTC')   # files from before ETH / SOL have no asset field: BTC
    DEC = DECIMALS.get(asset, 2)
    candles = snap['candles']
    oi = {t: v for t, v in snap['oi']}
    taker = {t: v for t, v in snap['taker']}
    price, up, dn, p_up = verdict(candles, oi, taker)
    _, up0, dn0, p_up0 = verdict(candles[:-1], oi, taker)
    lab, lab0 = label(p_up), label(p_up0)
    if p_up is None:
        print(f'{lab} · prijs {fmt(price)}')
    else:
        first, other = (up, dn) if p_up >= 0.5 else (dn, up)
        print(f'{lab} {round(max(p_up, 1 - p_up) * 100)}%: {fmt(first[0])} vóór {fmt(other[0])} · prijs {fmt(price)}')
        if abs(first[0] / price - 1) <= 0.003:
            print(f'MELDING: prijs {fmt(price)} zit binnen 0,3% van het eerste doel {fmt(first[0])}')
    last = candles[-1]
    if up0 and last[2] >= up0[0]:
        print(f'MELDING: cluster boven {fmt(up0[0])} geraakt in het laatste uur (hoog {fmt(last[2])})')
    if dn0 and last[3] <= dn0[0]:
        print(f'MELDING: cluster onder {fmt(dn0[0])} geraakt in het laatste uur (laag {fmt(last[3])})')
    if lab in ('Eerst omhoog', 'Eerst omlaag') and lab0 in ('Eerst omhoog', 'Eerst omlaag') and lab != lab0:
        print(f'MELDING: eerste doel wisselde van kant: {lab0} → {lab}')
    liq = snap.get('liqReal') or []
    if len(liq) > 25:
        prev = [r[1] + r[2] for r in liq[-25:-1]]
        lu, su = liq[-1][1], liq[-1][2]
        avg = sum(prev) / len(prev)
        if lu + su > LIQ_MIN.get(asset, 1e6) and lu + su > 3 * avg:
            side = 'longs' if lu > su else 'shorts'
            print(f'MELDING: liquidatiegolf op Gate: ${(lu + su) / 1e6:.1f}M in het laatste uur, vooral {side} '
                  f'({(lu + su) / max(avg, 1):.0f}× het uurgemiddelde)')


if __name__ == '__main__':
    main()
