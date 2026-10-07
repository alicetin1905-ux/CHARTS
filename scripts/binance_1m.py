"""Binance USD-M futures 1m candles from data.binance.vision (monthly zips). Needs numpy.

Writes data/binance_1m/<SYMBOL>.npz with t (open time ms UTC), o, h, l, c, v (base volume), tb (taker buy base volume).

    python3 scripts/binance_1m.py [SYMBOL ...]        default: BTC ETH SOL XRP DOGE SUI, 2022-10 .. 2026-09
"""
import io, os, sys, time, urllib.request, zipfile
from multiprocessing import Pool

import numpy as np

BASE = 'https://data.binance.vision/data/futures/um/monthly/klines'
OUT = os.path.join('data', 'binance_1m')
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'SUIUSDT']
MONTHS = [f'{y}-{m:02d}' for y in range(2022, 2027) for m in range(1, 13) if '2022-10' <= f'{y}-{m:02d}' <= '2026-09']


def get(url, tries=5):
    for i in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=120).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # coin not listed yet that month
            time.sleep(2 ** i)
        except Exception:
            time.sleep(2 ** i)
    raise SystemExit(f'cannot download {url}')


def month(job):
    sym, m = job
    raw = get(f'{BASE}/{sym}/1m/{sym}-1m-{m}.zip')
    if raw is None:
        return None
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        txt = z.read(z.namelist()[0]).decode()
    lines = [ln for ln in txt.splitlines() if ln and ln[0].isdigit()]  # newer files have a header row
    a = np.array([[x for j, x in enumerate(ln.split(',')) if j in (0, 1, 2, 3, 4, 5, 9)] for ln in lines], dtype=np.float64)
    return a


def main():
    syms = sys.argv[1:] or SYMBOLS
    os.makedirs(OUT, exist_ok=True)
    with Pool(8) as pool:
        for sym in syms:
            f = os.path.join(OUT, f'{sym}.npz')
            if os.path.exists(f):
                print(sym, 'cached')
                continue
            parts = [p for p in pool.map(month, [(sym, m) for m in MONTHS]) if p is not None]
            a = np.concatenate(parts)
            a = a[np.argsort(a[:, 0], kind='stable')]
            a = a[np.concatenate([[True], np.diff(a[:, 0]) > 0])]
            np.savez(f, t=a[:, 0].astype(np.int64), o=a[:, 1], h=a[:, 2], l=a[:, 3], c=a[:, 4], v=a[:, 5], tb=a[:, 6])
            print(sym, len(a), 'candles', time.strftime('%Y-%m-%d', time.gmtime(a[0, 0] / 1000)), '->',
                  time.strftime('%Y-%m-%d', time.gmtime(a[-1, 0] / 1000)))


if __name__ == '__main__':
    main()
