"""Hourly check for GlassSphere: the first-target verdict on the page's standard horizon (1 day) now and an
hour ago, and alerts.

Reads a data file written by `glasssphere_snapshot.py DIR --asset ...` (BTC, ETH, SOL, ..., WLD) and prints one status line,
then one line per alert starting with "MELDING:". glasssphere_notify.py sends them to ntfy (check() here gives them as
data). Alerts:
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

DECIMALS = {'BTC': 0, 'ETH': 1, 'SOL': 2, 'BNB': 2, 'XRP': 4, 'DOGE': 5, 'SUI': 4, 'ENA': 5, 'PEPE': 9,
            'WLD': 4, 'NEAR': 3,
            'HYPE': 2, 'ZEC': 2, 'LTC': 2, 'ADA': 4, 'UNI': 3, 'LINK': 3}   # as the page shows prices
# smallest liquidation wave worth an alert, USD per hour (Gate's liquidations in the smaller coins are small)
DAYS = 1   # the standard horizon of the page
LIQ_MIN = {'BTC': 1e6, 'ETH': 5e5, 'SOL': 2e5, 'BNB': 2.5e4, 'XRP': 1e5, 'DOGE': 1e5, 'SUI': 5e4, 'ENA': 2.5e4,
           'PEPE': 1e5, 'WLD': 5e4, 'NEAR': 5e4,
           'HYPE': 1e5, 'ZEC': 1e5, 'LTC': 5e4, 'ADA': 5e4, 'UNI': 5e4, 'LINK': 5e4}


def verdict(candles, oi, taker, days=3):
    price = candles[-1][4]
    book = M.LiqBook('taker')
    for k, c in enumerate(candles):
        book.step(k, c, oi.get(c[0]), oi.get(c[0] + 3600000), taker.get(c[0]))
    stops = M.estimate_stops(candles, price)
    pl = M.pools(stops, [(q[0], q[1], q[2]) for q in book.pos], price, rng=0.15)
    up, dn, p_up = M.first_targets(pl, price, M.r24_of(candles), days=days)
    return price, up, dn, p_up


def liq_wave(snap, asset):
    """Gate's real liquidations in the last hour, when over LIQ_MIN and 3x the hourly average of the day before."""
    liq = snap.get('liqReal') or []
    if len(liq) <= 25:
        return None
    prev = [r[1] + r[2] for r in liq[-25:-1]]
    lu, su = liq[-1][1], liq[-1][2]
    avg = sum(prev) / len(prev)
    if lu + su > LIQ_MIN.get(asset, 1e6) and lu + su > 3 * avg:
        return {'usd': round(lu + su), 'side': 'longs' if lu > su else 'shorts', 'x': round((lu + su) / max(avg, 1)),
                't': liq[-1][0]}
    return None


def label(p_up):
    if p_up is None:
        return 'Geen doelen'
    prob = max(p_up, 1 - p_up)
    return 'Neutraal' if round(prob * 100) < 60 else 'Eerst omhoog' if p_up > 0.5 else 'Eerst omlaag'


DEC = 0


def fmt(p):
    """Dutch notation: 81.700 / 2.399,5 / 104,36"""
    return f'{p:,.{DEC}f}'.replace(',', ' ').replace('.', ',').replace(' ', '.')


def check(snap):
    """The status line and the alerts of one data file: {asset, price, verdict ('up' / 'down' / 'neutral' / None),
    status, alerts: [(kind, text)]} with kind near / hit / switch / wave."""
    global DEC
    asset = snap.get('asset', 'BTC')   # files from before ETH / SOL have no asset field: BTC
    DEC = DECIMALS.get(asset, 2)
    candles = snap['candles']
    oi = {t: v for t, v in snap['oi']}
    taker = {t: v for t, v in snap['taker']}
    days = DAYS
    price, up, dn, p_up = verdict(candles, oi, taker, days)
    _, up0, dn0, p_up0 = verdict(candles[:-1], oi, taker, days)
    lab, lab0 = label(p_up), label(p_up0)
    alerts = []
    if p_up is None:
        status = f'{lab} · prijs {fmt(price)}'
    else:
        first, other = (up, dn) if p_up >= 0.5 else (dn, up)
        status = (f'{lab} {round(max(p_up, 1 - p_up) * 100)}% ({"1 dag" if days == 1 else f"{days} dagen"}): '
                  f'{fmt(first[0])} vóór {fmt(other[0])} · prijs {fmt(price)}')
        if abs(first[0] / price - 1) <= 0.003:
            alerts.append(('near', f'prijs {fmt(price)} zit binnen 0,3% van het eerste doel {fmt(first[0])}'))
    last = candles[-1]
    if up0 and last[2] >= up0[0]:
        alerts.append(('hit', f'cluster boven {fmt(up0[0])} geraakt in het laatste uur (hoog {fmt(last[2])})'))
    if dn0 and last[3] <= dn0[0]:
        alerts.append(('hit', f'cluster onder {fmt(dn0[0])} geraakt in het laatste uur (laag {fmt(last[3])})'))
    if lab in ('Eerst omhoog', 'Eerst omlaag') and lab0 in ('Eerst omhoog', 'Eerst omlaag') and lab != lab0:
        alerts.append(('switch', f'eerste doel wisselde van kant: {lab0} → {lab}'))
    w = liq_wave(snap, asset)
    if w:
        alerts.append(('wave', f'liquidatiegolf op Gate: ${w["usd"] / 1e6:.1f}M in het laatste uur, vooral {w["side"]} '
                               f'({w["x"]}× het uurgemiddelde)'))
    v = {'Eerst omhoog': 'up', 'Eerst omlaag': 'down', 'Neutraal': 'neutral'}.get(lab)
    return {'asset': asset, 'price': price, 'verdict': v, 'status': status, 'alerts': alerts}


def main():
    r = check(json.load(open(sys.argv[1])))
    print(r['status'])
    for _, text in r['alerts']:
        print('MELDING: ' + text)

if __name__ == '__main__':
    main()
