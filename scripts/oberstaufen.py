#!/usr/bin/env python3
"""Öffentliche Oberstaufen-Ratsdaten abrufen und einen belegbaren Index bauen."""
import argparse
import base64
import collections
import datetime as dt
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
API = 'https://risapi1.komuna.net/'
PORTAL = 'https://ris.komuna.net/oberstaufen/Home.mvc'
BERICHTE = 'https://www.oberstaufen.info/rathaus-buergerservice/rathaus-aktuell/berichte-aus-sitzungen-versammlungen'
HEADERS = {'x-orgkey': 'RIS51', 'x-uniqueid': 'ris.komuna.net/oberstaufen', 'x-customname': 'oberstaufen', 'User-Agent': 'Ratsakten-Oberstaufen/1.0 (public research)'}


def get(url):
    headers = HEADERS if url.startswith(API) else {'User-Agent': HEADERS['User-Agent']}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as response:
        return response.read()


def api(path):
    time.sleep(0.5)
    return json.loads(get(API + path))


def public_items(detail):
    if detail.get('clientid') != 32 or detail.get('clientname') != 'Markt Oberstaufen':
        raise ValueError('Unerwartete Gemeinde in Sitzungsdaten')
    return [item for part in detail.get('parts', []) if part.get('protectedpart') is False
            for item in part.get('agendaitems', []) if item.get('restricted') is False]


class ReportLinks(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.href = None; self.text = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a': self.href = dict(attrs).get('href'); self.text = []
    def handle_data(self, value):
        if self.href: self.text.append(value)
    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            title = ' '.join(' '.join(self.text).split())
            url = urllib.parse.urljoin(BERICHTE, self.href)
            if '/fileadmin/' in url and url.lower().endswith('.pdf') and title:
                self.links.append({'titel': title, 'url': url})
            self.href = None


def fetch(start, end):
    dashboard = api('web/guestdashboard')
    config = json.loads(base64.b64decode(dashboard['guiconfigcontent']))
    if config.get('name') != 'Markt Oberstaufen' or dashboard.get('startupclientid') != 32:
        raise ValueError('Gemeindezuordnung hat sich geändert; Import abgebrochen')
    records = api('web/guestmeetings?' + urllib.parse.urlencode({'client':32, 'from':start, 'until':end}))['items']
    if not records: raise ValueError('Keine Termine geliefert; bestehende Daten bleiben erhalten')
    meetings, tops = [], []
    for i, record in enumerate(records, 1):
        date = record['meetingdate'][:10]
        if record.get('clientname') != 'Markt Oberstaufen': raise ValueError('Fremde Gemeinde')
        if not start <= date <= end: continue
        detail = api('web/guestmeetings/' + str(record['id']))
        items = public_items(detail)
        meeting = {'id':record['id'], 'datum':date, 'gremium':record['committeename'], 'titel':record['name'],
                   'n_tops':len(items), 'url':PORTAL, 'api_quelle':API+'web/guestmeetings/'+str(record['id'])}
        meetings.append(meeting)
        for item in items:
            tops.append({'id':item['id'], 'sitzung_id':record['id'], 'datum':date, 'gremium':record['committeename'],
                         'top':item['numbering'], 'titel':item['name'], 'vorlage':item.get('globalid') or None,
                         'dokumentanzahl':item.get('documentcount'), 'url':PORTAL})
        print(f'{i}/{len(records)}: {date} – {len(items)} öffentliche TOP', flush=True)
    parser = ReportLinks(); parser.feed(get(BERICHTE).decode('utf-8'))
    links = list({row['url']:row for row in parser.links}.values())
    result = {'kommune':'Markt Oberstaufen', 'von':start, 'bis':end,
              'abgerufen':dt.datetime.now(dt.timezone.utc).isoformat(),
              'quelle':PORTAL, 'berichte_quelle':BERICHTE,
              'sitzungen':meetings, 'tagesordnungspunkte':tops, 'berichte':links}
    target = ROOT/'data/oberstaufen.json'; temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8'); temp.replace(target)
    return result


def build(data):
    e = html.escape
    counts = collections.Counter(m['gremium'] for m in data['sitzungen'])
    body = '<h2>Gremien im erfassten Zeitraum</h2><ul>' + ''.join(f'<li>{e(k)}: {v} Termine</li>' for k,v in counts.most_common()) + '</ul>'
    body += '<h2>Sitzungen und öffentliche Tagesordnungen</h2><p>Einträge stammen aus dem öffentlichen Gastzugang. Ein Kalendereintrag allein belegt nicht, dass eine Sitzung stattgefunden hat.</p>'
    grouped = collections.defaultdict(list)
    for item in data['tagesordnungspunkte']: grouped[item['sitzung_id']].append(item)
    for m in sorted(data['sitzungen'], key=lambda x:x['datum'], reverse=True):
        body += f'<details><summary>{e(m["datum"])} · {e(m["gremium"])} <small>{m["n_tops"]} TOP</small></summary><p>Quellenkennung: Sitzung {m["id"]}</p><ol>'
        for t in grouped[m['id']]:body += f'<li value="1"><strong>TOP {e(t["top"])}</strong> {e(t["titel"])}</li>'
        body += ('<li>Keine öffentlichen Tagesordnungspunkte im Gastabruf verfügbar.</li>' if not grouped[m['id']] else '')
        body += '</ol><p><a href="'+PORTAL+'">Im Ratsinformationssystem nach Datum und Gremium prüfen →</a></p></details>'
    body += '<h2>Amtliche Berichte und Anlagen</h2><p>Separater Bestand der Gemeindewebsite; umfasst auch andere Jahre und Versammlungen. Berichte sind keine vollständige Sammlung aller Beschlüsse.</p><ul>'
    body += ''.join(f'<li><a href="{e(x["url"],quote=True)}">{e(x["titel"])}</a></li>' for x in data['berichte'])+'</ul>'
    content = '''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ratsakten Oberstaufen</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f7f5ef;color:#20392f;font:17px/1.65 system-ui,sans-serif}main{max-width:1000px;margin:auto;padding:40px 22px}h1{font-size:clamp(36px,6vw,68px);line-height:1.08;letter-spacing:-2px}h2{margin-top:48px}a{color:#175a43}small,.muted{color:#58665f}.stats{display:flex;gap:16px;flex-wrap:wrap}.stats>div{flex:1;min-width:150px;border-top:3px solid #307153;background:white;padding:18px}.stats b{font-size:38px;display:block}details{border-top:1px solid #ccd4cb;padding:14px 0}summary{cursor:pointer;font-weight:650}summary small{float:right}li{margin:8px 0}details ol{list-style:none;padding:0 18px}.note{background:#ebe8dc;padding:18px;border-left:4px solid #aa8743}footer{border-top:1px solid #ccd4cb;margin-top:48px;padding-top:20px;font-size:14px}@media(max-width:550px){summary small{float:none;display:block}}
</style><main><p class="muted">ALLGÄU · ÖFFENTLICHE RATSUNTERLAGEN</p><h1>Ratsakten<br>Oberstaufen.</h1><p>Was steht auf der Tagesordnung des Marktgemeinderates und seiner Ausschüsse?</p>'''
    content += f'<p class="muted">Erfasst: {e(data["von"])} bis {e(data["bis"])} · Abruf: {e(data["abgerufen"][:10])}</p><div class="stats"><div><b>{len(data["sitzungen"])}</b>Sitzungstermine</div><div><b>{len(data["tagesordnungspunkte"])}</b>öffentliche TOP</div><div><b>{len(counts)}</b>Gremien</div></div>'
    content += '<p class="note"><strong>Erster Datenstand: Tagesordnungsindex.</strong> Die TOP-Titel zeigen Beratungsgegenstände, keine nachgewiesenen Beschlussergebnisse. Abstimmungen, Geldbeträge und vollständige Beschlusstexte wurden noch nicht ausgewertet. Fehlende Daten bedeuten nicht, dass keine Entscheidung getroffen wurde.</p>'+body
    content += '<footer>Privates Lernprojekt. Maßgeblich sind die Originalunterlagen des Marktes Oberstaufen. <a href="'+BERICHTE+'">Amtliche Berichte</a> · <a href="'+PORTAL+'">Ratsinfo</a><p>Technische Grundlage: <a href="https://github.com/dominikamann/ratsakten-bad-waldsee">Ratsakten Bad Waldsee – AmannLabs.eu</a>. Für Oberstaufen neu angebunden und bearbeitet. Lizenzhinweise im Repository.</p></footer></main></html>'
    (ROOT/'docs/index.html').write_text(content, encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--von',default='2024-01-01');parser.add_argument('--bis',default=dt.date.today().isoformat());parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    start=dt.date.fromisoformat(args.von);end=dt.date.fromisoformat(args.bis)
    if start>end or end>dt.date.today():parser.error('Ungültiger Zeitraum oder zukünftiger Stichtag')
    data=json.loads((ROOT/'data/oberstaufen.json').read_text()) if args.offline else fetch(start.isoformat(),end.isoformat())
    build(data)

if __name__=='__main__':main()
