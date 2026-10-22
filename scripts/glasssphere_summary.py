"""The GlassSphere verdict of every asset, for the coin tiles on the start page of index.html.

Reads the data files written by glasssphere_snapshot.py in DIR and writes DIR/glasssphere-summary.json:
  ts      when written
  assets  {asset: {price, ts, hz: {days: {v, prob, first, other}}, wave?}} for 1, 3 and 7 days, where v is 'up' or 'down'
          (the more likely first target, at 60% or more), 'neutral' (below 60%) or null (no target on one side).
          The same calculation as the reading on the page (glasssphere_alert.verdict).
          wave: {usd, side, x, t} when Gate's liquidations in the last hour were a wave (glasssphere_alert.liq_wave).

    python3 scripts/glasssphere_summary.py DIR
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glasssphere_alert as AL  # noqa: E402
import glasssphere_snapshot as SN  # noqa: E402


def main():
    out_dir = sys.argv[1]
    assets = {}
    for key, A in SN.ASSETS.items():
        try:
            snap = json.load(open(os.path.join(out_dir, A['file'])))
        except (OSError, ValueError) as e:
            print(f'{key:5s} skipped: {e}')
            continue
        oi = {t: v for t, v in snap['oi']}
        taker = {t: v for t, v in snap['taker']}
        hz = {}
        for days in (1, 3, 7):
            price, up, dn, p_up = AL.verdict(snap['candles'], oi, taker, days)
            if p_up is None:
                hz[days] = {'v': None}
                continue
            prob = max(p_up, 1 - p_up)
            first, other = (up, dn) if p_up >= 0.5 else (dn, up)
            v = 'neutral' if round(prob * 100) < 60 else 'up' if p_up > 0.5 else 'down'
            hz[days] = {'v': v, 'prob': round(prob, 4), 'first': first[0], 'other': other[0]}
        assets[key] = {'price': snap['price'], 'ts': snap['ts'], 'hz': hz}
        wave = AL.liq_wave(snap, A['name'])
        if wave:
            assets[key]['wave'] = wave
        print(f'{key:5s} ' + '  '.join(f'{d}d {h["v"]} {h.get("prob", "")}' for d, h in hz.items()))
    path = os.path.join(out_dir, 'glasssphere-summary.json')
    json.dump({'ts': int(time.time() * 1000), 'assets': assets}, open(path, 'w'), separators=(',', ':'))
    print(f'wrote {path} ({os.path.getsize(path)} bytes)')


if __name__ == '__main__':
    main()
