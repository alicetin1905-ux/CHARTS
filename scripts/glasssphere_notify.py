"""Sends the hourly GlassSphere alerts to ntfy, one message per coin with something to report.

Usage: glasssphere_notify.py SITE_DIR BASE_URL [--now HH:MM]

Settings are in alerts.json at the repo root:
  - "munten": coin -> true / false, which coins send alerts (a coin left out sends them)
  - "stil": quiet hours "van" - "tot" in "tijdzone"; inside them nothing is sent, except liquidation waves when
    "behalve_liquidatiegolf" is true

The topic comes from the NTFY_TOPIC environment variable; without it the messages are only printed (dry run).
Messages go through ntfy's JSON publishing, so the title, emoji tags, priority, the tap target (the coin's tab) and an
"Open BTC" button all survive in UTF-8.
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glasssphere_alert as AL      # noqa: E402
import glasssphere_snapshot as SN   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ntfy emoji tags, most important kind first; the first kind present sets the leading emoji
KIND_TAG = {'wave': 'boom', 'hit': 'dart', 'switch': 'arrows_counterclockwise', 'near': 'eyes'}
VERDICT_TAG = {'up': 'chart_with_upwards_trend', 'down': 'chart_with_downwards_trend', 'neutral': 'left_right_arrow'}


def minutes(hhmm):
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def quiet(cfg, now=None):
    """True inside the quiet hours (a window over midnight, 23:00 - 07:00, works too)."""
    s = cfg.get('stil')
    if not s:
        return False
    if now is None:
        t = dt.datetime.now(ZoneInfo(s.get('tijdzone', 'Europe/Amsterdam')))
        now = t.hour * 60 + t.minute
    a, b = minutes(s['van']), minutes(s['tot'])
    return a <= now < b if a <= b else now >= a or now < b


def message(r, key, base):
    """The ntfy JSON for one coin's check result."""
    name = SN.ASSETS[key]['name']
    kinds = [k for k in KIND_TAG if any(kind == k for kind, _ in r['alerts'])]
    tags = [KIND_TAG[k] for k in kinds] + ([VERDICT_TAG[r['verdict']]] if r['verdict'] else [])
    url = f'{base.rstrip("/")}/#{key}'
    return {
        'title': f'GlassSphere {name}',
        'message': r['status'] + '\n' + '\n'.join('• ' + text for _, text in r['alerts']),
        'tags': tags,
        'priority': 4 if kinds and kinds[0] in ('wave', 'hit') else 3,
        'click': url,
        'icon': f'{base.rstrip("/")}/icons/coins/{key}.png',
        'actions': [{'action': 'view', 'label': f'Open {name}', 'url': url, 'clear': True}],
    }


def main():
    site, base = sys.argv[1], sys.argv[2]
    now = minutes(sys.argv[sys.argv.index('--now') + 1]) if '--now' in sys.argv else None
    cfg = json.load(open(os.path.join(ROOT, 'alerts.json')))
    on = cfg.get('munten', {})
    is_quiet = quiet(cfg, now)
    only_waves = is_quiet and cfg['stil'].get('behalve_liquidatiegolf', True)
    topic = os.environ.get('NTFY_TOPIC')
    print('stille uren' + (' (alleen liquidatiegolven)' if only_waves else '') if is_quiet else 'geen stille uren')
    for key, A in SN.ASSETS.items():
        path = os.path.join(site, A['file'])
        if not os.path.exists(path):
            continue
        try:
            r = AL.check(json.load(open(path)))
        except Exception as e:   # one broken file must not stop the other coins
            print(f'== {A["name"]}: fout {e}')
            continue
        print(f'== {A["name"]}: {r["status"]}')
        for _, text in r['alerts']:
            print('   MELDING: ' + text)
        if not on.get(key, True):
            print('   (meldingen uit voor deze munt)')
            continue
        if is_quiet:
            r['alerts'] = [a for a in r['alerts'] if a[0] == 'wave'] if only_waves else []
        if not r['alerts']:
            continue
        msg = message(r, key, base)
        if not topic:
            print('   droog: ' + json.dumps(msg, ensure_ascii=False))
            continue
        req = urllib.request.Request('https://ntfy.sh', data=json.dumps(dict(msg, topic=topic)).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=20).read()
            print('   verstuurd')
        except Exception as e:
            print(f'   versturen mislukt: {e}')


if __name__ == '__main__':
    main()
