"""Longer backtest of the liquidity map reading on Binance BTCUSDT perpetual, 1H, Jan 2022 - Sep 2026.

Same evaluation as liquidity_backtest.py (stops, liquidations from hourly OI growth, signals, first touch of the
largest pool above / below), on the free Binance archive at data.binance.vision:
  klines   1H candles with taker buy volume (monthly files)
  metrics  open interest every 5 minutes (daily files, from Dec 2021)
  funding  funding rate (monthly files)
The archive has no liquidations, so the liquidation model cannot be calibrated here (that is done on Gate).

    python3 scripts/liquidity_backtest_long.py [cache_dir]      (default data/)
Appends a section to backtests/liquidity_map.md (replacing an earlier one).
"""
import csv
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import liquidity_backtest as BT  # noqa: E402
import liquidity_model as M  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'data'), 'binance_btc_archive')
OUT = os.path.join(ROOT, 'backtests', 'liquidity_map.md')
ARCHIVE = 'https://data.binance.vision/data/futures/um/'
FIRST, LAST = (2022, 1), (2026, 9)
STEP = 6          # evaluate every 6 hours
MARK = '## 4. Longer test: Binance, 2022 – 2026'


def months():
    y, m = FIRST
    while (y, m) <= LAST:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_csv(url):
    """Rows of the one CSV inside a zip from the archive, cached; [] when the file does not exist."""
    path = os.path.join(CACHE, url.rsplit('/', 1)[1])
    if not os.path.exists(path):
        for i in range(4):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'curl/8.0'})
                with urllib.request.urlopen(req, timeout=60) as r:
                    open(path, 'wb').write(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return []
                time.sleep(2 ** i)
            except Exception:
                time.sleep(2 ** i)
        else:
            return []
    z = zipfile.ZipFile(path)
    rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), 'utf-8')))
    return rows[1:] if rows and not rows[0][0][:1].isdigit() else rows


def load():
    os.makedirs(CACHE, exist_ok=True)
    ms = list(months())
    days = []
    t = time.mktime((FIRST[0], FIRST[1], 1, 12, 0, 0, 0, 0, 0)) - time.timezone
    end = time.mktime((LAST[0] + (LAST[1] == 12), LAST[1] % 12 + 1, 1, 12, 0, 0, 0, 0, 0)) - time.timezone
    while t < end:
        days.append(time.strftime('%Y-%m-%d', time.gmtime(t)))
        t += 86400
    with ThreadPoolExecutor(16) as ex:
        kl = ex.map(lambda ym: fetch_csv(f'{ARCHIVE}monthly/klines/BTCUSDT/1h/BTCUSDT-1h-{ym[0]}-{ym[1]:02d}.zip'), ms)
        fr = ex.map(lambda ym: fetch_csv(f'{ARCHIVE}monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{ym[0]}-{ym[1]:02d}.zip'), ms)
        me = ex.map(lambda d: fetch_csv(f'{ARCHIVE}daily/metrics/BTCUSDT/BTCUSDT-metrics-{d}.zip'), days)
        kl, fr, me = list(kl), list(fr), list(me)
    candles, stats = [], {}
    for rows in kl:
        for r in rows:
            t, q, tbq = int(r[0]), float(r[7]), float(r[10])
            candles.append([t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), q])
            sell = q - tbq
            stats.setdefault(str(t // 1000), {})['taker'] = tbq / sell if sell > 0 else 1.0
    for rows in me:
        for r in rows:
            ts = time.strptime(r[0], '%Y-%m-%d %H:%M:%S')
            if ts.tm_min == 0:
                sec = int(time.mktime(ts) - time.timezone)
                stats.setdefault(str(sec), {})['oi'] = float(r[3])
    funding = sorted((int(r[0]) // 1000, float(r[2])) for rows in fr for r in rows)
    candles.sort()
    # keep hours that have both OI and taker data; others count as missing
    stats = {k: v for k, v in stats.items() if v.get('oi', 0) > 0 and 'taker' in v}
    return candles, stats, funding


def main():
    candles, stats, funding = load()
    print(f'{len(candles)} candles, {len(stats)} hours with OI, {len(funding)} funding points')
    rows = BT.evaluate(candles, stats, funding, 'taker', M.LEV, STEP)
    keys = ['mom', 'pull', 'oi', 'fund', 'cvd']
    names = {'mom': 'Momentum', 'pull': 'Liquidity pull', 'oi': 'Open interest', 'fund': 'Funding (contrarian)',
             'cvd': 'CVD (taker buying)'}
    years = sorted({time.gmtime(r['t'] / 1000).tm_year for r in rows})
    by_year = {y: [r for r in rows if time.gmtime(r['t'] / 1000).tm_year == y] for y in years}
    lines = [MARK, '',
             f'Binance BTCUSDT perpetual from the free archive at data.binance.vision: 1H candles, open interest '
             f'(5-minute, sampled on the hour), taker buy volume and funding. {len(candles)} hours, evaluated every '
             f'{STEP}h after the {M.WINDOW}-bar warm-up ({len(rows)} points), the same model and leverage mix as above. '
             'No liquidation data in the archive, so section 1 cannot be repeated here.', '',
             '### Signals against the next 24h return, IC per year', '',
             '| Signal | ' + ' | '.join(str(y) for y in years) + ' | All |',
             '|---|' + '---|' * (len(years) + 1)]
    for k in keys:
        cells = [f'{BT.stats_for(by_year[y], k, STEP)[0]:+.3f}' for y in years]
        a = BT.stats_for(rows, k, STEP)
        lines.append(f'| {names[k]} | ' + ' | '.join(cells) + f' | {a[0]:+.3f} (t {a[1]:+.1f}) |')
    lines += ['', '### Which pool is hit first, per year', '',
              '| Year | Points with a hit | Pull side first | Closer side first | Brier (distance rule) | Neither within 24h |',
              '|---|---|---|---|---|---|']
    for label, rs in [(str(y), by_year[y]) for y in years] + [('All', rows)]:
        hits = [r for r in rs if r['touch'] and r['touch'][0] in ('up', 'down')]
        tot = [r for r in rs if r['touch']]
        if not hits:
            continue
        pull_ok = sum((r['pull'] > 0) == (r['touch'][0] == 'up') for r in hits) / len(hits)
        near_ok = sum(r['touch'][1] == r['touch'][0] for r in hits) / len(hits)
        brier = sum((r['touch'][2] - (r['touch'][0] == 'up')) ** 2 for r in hits) / len(hits)
        none = sum(1 for r in tot if r['touch'][0] is None) / len(tot)
        lines.append(f'| {label} | {len(hits)} | {pull_ok:.0%} | {near_ok:.0%} | {brier:.3f} | {none:.0%} |')
    lines += ['', '| Predicted P(up first) | Points | Actually up first |', '|---|---|---|']
    hits = [r for r in rows if r['touch'] and r['touch'][0] in ('up', 'down')]
    for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
        b = [r for r in hits if lo <= r['touch'][2] < hi]
        if b:
            lines.append(f'| {lo:.0%}–{min(hi, 1):.0%} (mean {sum(r["touch"][2] for r in b) / len(b):.0%}) | {len(b)} | '
                         f'{sum(r["touch"][0] == "up" for r in b) / len(b):.0%} |')
    text = open(OUT).read()
    if MARK in text:
        text = text[:text.index(MARK)].rstrip() + '\n'
    open(OUT, 'w').write(text + '\n' + '\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
