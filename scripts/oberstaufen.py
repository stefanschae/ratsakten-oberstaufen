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
from io import BytesIO

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

ROOT = Path(__file__).resolve().parents[1]
API = 'https://risapi1.komuna.net/'
PORTAL = 'https://ris.komuna.net/oberstaufen/Home.mvc'
BERICHTE = 'https://www.oberstaufen.info/rathaus-buergerservice/rathaus-aktuell/berichte-aus-sitzungen-versammlungen'
HEADERS = {'x-orgkey': 'RIS51', 'x-uniqueid': 'ris.komuna.net/oberstaufen', 'x-customname': 'oberstaufen', 'User-Agent': 'Ratsakten-Oberstaufen/1.0 (public research)'}
MONTHS = {'januar':1, 'februar':2, 'märz':3, 'maerz':3, 'april':4, 'mai':5, 'juni':6, 'juli':7, 'august':8, 'september':9, 'oktober':10, 'november':11, 'dezember':12}
STOPWORDS = set('aus der des die das und oder am im in zur zum für fuer mit von eine einen einem einer den dem beschließend beschliessend vorberatung vorberatend kenntnisnahme bekanntgaben anträge antraege tagesordnung sitzung marktgemeinderates marktgemeinderat oberstaufen'.split())


def get(url, timeout=45):
    headers = HEADERS if url.startswith(API) else {'User-Agent': HEADERS['User-Agent']}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        return response.read()


def api(path):
    time.sleep(0.5)
    return json.loads(get(API + path))


def public_items(detail):
    if detail.get('clientid') != 32 or detail.get('clientname') != 'Markt Oberstaufen':
        raise ValueError('Unerwartete Gemeinde in Sitzungsdaten')
    return [item for part in detail.get('parts', []) if part.get('protectedpart') is False
            for item in part.get('agendaitems', []) if item.get('restricted') is False]


def clean_text(value):
    return ' '.join((value or '').replace('\r', ' ').replace('\n', ' ').split())


def keywords(value):
    words = re.findall(r'[A-Za-zÄÖÜäöüß0-9]{4,}', clean_text(value).lower())
    return [w.replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss') for w in words if w not in STOPWORDS]


def parse_german_date(value):
    m = re.search(r'(\d{1,2})\.\s*([A-Za-zÄÖÜäöüß]+)\s+(\d{4})', value)
    if not m:
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})', value)
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}' if m else None
    month = MONTHS.get(m.group(2).lower().replace('ä','ae'))
    return dt.date(int(m.group(3)), month, int(m.group(1))).isoformat() if month else None


def infer_committee(title):
    low = title.lower()
    if 'marktgemeinderat' in low or 'mgr' in low:
        return 'Marktgemeinderat'
    if 'bau' in low and 'umwelt' in low:
        return 'Bau- und Umweltausschuss'
    if 'tourismus' in low:
        return 'Tourismusausschuss'
    return None


def extract_pdf_text(url):
    if PdfReader is None:
        return ''
    try:
        reader = PdfReader(BytesIO(get(url, timeout=12)))
        return clean_text(' '.join(page.extract_text() or '' for page in reader.pages))
    except Exception:
        return ''


def enrich_reports(reports, with_pdf_text=False, start=None, end=None):
    enriched = []
    for report in reports:
        row = dict(report)
        row['datum'] = parse_german_date(row['titel'] + ' ' + urllib.parse.unquote(row['url']))
        row['gremium'] = infer_committee(row['titel'] + ' ' + row['url'])
        row['typ'] = 'sitzungsbericht' if row['datum'] and row['gremium'] else 'anlage'
        in_range = row['datum'] and (start is None or start <= row['datum'] <= end)
        if with_pdf_text and row['typ'] == 'sitzungsbericht' and in_range:
            print('PDF-Text:', row['datum'], row['titel'], flush=True)
            row['text'] = extract_pdf_text(row['url'])
        enriched.append(row)
    return enriched


def report_excerpt(text, terms):
    if not text:
        return None
    low = text.lower().replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    positions = [low.find(term) for term in terms[:8] if low.find(term) >= 0]
    if not positions:
        return None
    pos = min(positions)
    start = max(0, text.rfind('.', 0, pos - 1) + 1)
    end = text.find('.', pos + 180)
    end = end if end > pos else min(len(text), pos + 360)
    return clean_text(text[start:end + 1])[:420]


def vote_hint(text):
    if not text:
        return None
    patterns = [
        r'(\d+)\s*:\s*(\d+)(?:\s*:\s*(\d+))?',
        r'(\d+)\s+Ja(?:-|\s*)Stimmen?.{0,60}?(\d+)\s+Nein',
        r'einstimmig',
        r'mehrheitlich',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return clean_text(m.group(0))
    return None


def enrich_decisions(data):
    meetings = {m['id']: m for m in data['sitzungen']}
    reports = enrich_reports(data.get('berichte', []), with_pdf_text=True, start=data.get('von'), end=data.get('bis'))
    by_date = collections.defaultdict(list)
    for report in reports:
        if report.get('datum'):
            by_date[report['datum']].append(report)

    linked_reports = collections.defaultdict(list)
    linked_tops = 0
    for item in data['tagesordnungspunkte']:
        title = item['titel']
        is_deciding = 'beschließend' in title.lower() or 'beschliessend' in title.lower()
        item['beschlussstatus'] = 'beschließend vorgesehen' if is_deciding else 'kein beschließender TOP-Titel'
        item['bericht_links'] = []
        item['beschlusshinweis'] = None
        item['abstimmungshinweis'] = None
        item_terms = keywords(title)
        for report in by_date.get(item['datum'], []):
            meeting = meetings[item['sitzung_id']]
            same_committee = not report.get('gremium') or report['gremium'] == meeting['gremium']
            text = report.get('text', '')
            overlap = set(item_terms) & set(keywords(report['titel'] + ' ' + text[:12000]))
            if same_committee and (report['typ'] == 'sitzungsbericht' or len(overlap) >= 2):
                item['bericht_links'].append({'titel': report['titel'], 'url': report['url'], 'typ': report['typ']})
                linked_reports[item['sitzung_id']].append(report['url'])
                excerpt = report_excerpt(text, item_terms) if len(overlap) >= 2 else None
                if excerpt and len(overlap) >= 2:
                    item['beschlusshinweis'] = excerpt
                    item['abstimmungshinweis'] = vote_hint(excerpt)
                linked_tops += 1
                break
    for meeting in data['sitzungen']:
        urls = sorted(set(linked_reports.get(meeting['id'], [])))
        meeting['berichte'] = [next({'titel': r['titel'], 'url': r['url'], 'typ': r['typ']} for r in reports if r['url'] == url) for url in urls]
    data['berichte'] = [{k:v for k,v in r.items() if k != 'text'} for r in reports]
    data['auswertung'] = {
        'beschliessende_tops': sum(1 for t in data['tagesordnungspunkte'] if t['beschlussstatus'] == 'beschließend vorgesehen'),
        'tops_mit_berichtslink': sum(1 for t in data['tagesordnungspunkte'] if t.get('bericht_links')),
        'tops_mit_beschlusshinweis': sum(1 for t in data['tagesordnungspunkte'] if t.get('beschlusshinweis')),
        'tops_mit_abstimmungshinweis': sum(1 for t in data['tagesordnungspunkte'] if t.get('abstimmungshinweis')),
    }
    return data


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


def fetch(start, end, enrich=False):
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
    if enrich:
        result = enrich_decisions(result)
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
        body += f'<details><summary>{e(m["datum"])} · {e(m["gremium"])} <small>{m["n_tops"]} TOP</small></summary><p>Quellenkennung: Sitzung {m["id"]}</p>'
        if m.get('berichte'):
            body += '<p>Verknüpfte Sitzungsberichte/Anlagen: ' + ', '.join(f'<a href="{e(x["url"],quote=True)}">{e(x["titel"])}</a>' for x in m['berichte']) + '</p>'
        body += '<ol>'
        for t in grouped[m['id']]:
            body += f'<li value="1"><strong>TOP {e(t["top"])}</strong> {e(t["titel"])}'
            body += f'<br><small>{e(t.get("beschlussstatus","nicht ausgewertet"))}</small>'
            if t.get('bericht_links'):
                body += '<br><small>Bericht: ' + ', '.join(f'<a href="{e(x["url"],quote=True)}">{e(x["titel"])}</a>' for x in t['bericht_links']) + '</small>'
            if t.get('beschlusshinweis'):
                body += f'<blockquote>{e(t["beschlusshinweis"])}</blockquote>'
            if t.get('abstimmungshinweis'):
                body += f'<small>Abstimmungshinweis: {e(t["abstimmungshinweis"])}</small>'
            body += '</li>'
        body += ('<li>Keine öffentlichen Tagesordnungspunkte im Gastabruf verfügbar.</li>' if not grouped[m['id']] else '')
        body += '</ol><p><a href="'+PORTAL+'">Im Ratsinformationssystem nach Datum und Gremium prüfen →</a></p></details>'
    body += '<h2>Amtliche Berichte und Anlagen</h2><p>Separater Bestand der Gemeindewebsite; umfasst auch andere Jahre und Versammlungen. Berichte sind keine vollständige Sammlung aller Beschlüsse.</p><ul>'
    body += ''.join(f'<li><a href="{e(x["url"],quote=True)}">{e(x["titel"])}</a></li>' for x in data['berichte'])+'</ul>'
    content = '''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ratsakten Oberstaufen</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f7f5ef;color:#20392f;font:17px/1.65 system-ui,sans-serif}main{max-width:1000px;margin:auto;padding:40px 22px}h1{font-size:clamp(36px,6vw,68px);line-height:1.08;letter-spacing:-2px}h2{margin-top:48px}a{color:#175a43}small,.muted{color:#58665f}.stats{display:flex;gap:16px;flex-wrap:wrap}.stats>div{flex:1;min-width:150px;border-top:3px solid #307153;background:white;padding:18px}.stats b{font-size:38px;display:block}details{border-top:1px solid #ccd4cb;padding:14px 0}summary{cursor:pointer;font-weight:650}summary small{float:right}li{margin:8px 0}details ol{list-style:none;padding:0 18px}.note{background:#ebe8dc;padding:18px;border-left:4px solid #aa8743}footer{border-top:1px solid #ccd4cb;margin-top:48px;padding-top:20px;font-size:14px}@media(max-width:550px){summary small{float:none;display:block}}
</style><main><p class="muted">ALLGÄU · ÖFFENTLICHE RATSUNTERLAGEN</p><h1>Ratsakten<br>Oberstaufen.</h1><p>Was steht auf der Tagesordnung des Marktgemeinderates und seiner Ausschüsse?</p>'''
    auswertung = data.get('auswertung', {})
    stats = f'<div><b>{len(data["sitzungen"])}</b>Sitzungstermine</div><div><b>{len(data["tagesordnungspunkte"])}</b>öffentliche TOP</div><div><b>{len(counts)}</b>Gremien</div>'
    if auswertung:
        stats += f'<div><b>{auswertung.get("tops_mit_berichtslink",0)}</b>TOP mit Berichtslink</div><div><b>{auswertung.get("tops_mit_abstimmungshinweis",0)}</b>Abstimmungshinweise</div>'
    content += f'<p class="muted">Erfasst: {e(data["von"])} bis {e(data["bis"])} · Abruf: {e(data["abgerufen"][:10])}</p><div class="stats">{stats}</div>'
    content += '<p class="note"><strong>Erweiterter Datenstand: Tagesordnung, Berichtszuordnung und Entscheidungshinweise.</strong> Beschluss- und Abstimmungshinweise werden nur angezeigt, wenn sie aus verknüpften amtlichen PDF-Berichten maschinell lesbar zugeordnet werden konnten. Fehlende Hinweise bedeuten nicht, dass es keinen Beschluss gab.</p>'+body
    content += '<footer>Privates Lernprojekt. Maßgeblich sind die Originalunterlagen des Marktes Oberstaufen. <a href="'+BERICHTE+'">Amtliche Berichte</a> · <a href="'+PORTAL+'">Ratsinfo</a><p>Technische Grundlage: <a href="https://github.com/dominikamann/ratsakten-bad-waldsee">Ratsakten Bad Waldsee – AmannLabs.eu</a>. Für Oberstaufen neu angebunden und bearbeitet. Lizenzhinweise im Repository.</p></footer></main></html>'
    (ROOT/'docs/index.html').write_text(content, encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--von',default='2024-01-01');parser.add_argument('--bis',default=dt.date.today().isoformat());parser.add_argument('--offline',action='store_true');parser.add_argument('--auswerten',action='store_true');args=parser.parse_args()
    start=dt.date.fromisoformat(args.von);end=dt.date.fromisoformat(args.bis)
    if start>end or end>dt.date.today():parser.error('Ungültiger Zeitraum oder zukünftiger Stichtag')
    data=json.loads((ROOT/'data/oberstaufen.json').read_text()) if args.offline else fetch(start.isoformat(),end.isoformat(), args.auswerten)
    if args.offline and args.auswerten:
        data = enrich_decisions(data)
        target = ROOT/'data/oberstaufen.json'; temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'); temp.replace(target)
    build(data)

if __name__=='__main__':main()
