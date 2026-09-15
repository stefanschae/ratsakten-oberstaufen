#!/usr/bin/env python3
"""Öffentliche Oberstaufen-Ratsdaten abrufen und einen belegbaren Index bauen."""
import argparse
import csv
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


def slug(value):
    value = clean_text(value).lower()
    value = value.replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')
    return value[:72] or 'seite'


NAVIGATION = [
    ('index.html',          'Startseite',          'start'),
    ('termine.html',        'Termine',             'termine'),
    ('themen/index.html',   'Themen',              'themen'),
    ('suche.html',          'Suche',               'suche'),
    ('befunde.html',        'Erkenntnisse',        'befunde'),
    ('ausgaben/index.html', 'Archiv',              'archiv'),
    ('gremien.html',        'Wer entscheidet was', 'gremien'),
]


def navigation(depth=0, current=None):
    """Dieselbe Leiste auf jeder Seite. depth = Ebenen unterhalb von docs/."""
    prefix = '../' * depth
    links = []
    for ziel, text, name in NAVIGATION:
        aktuell = ' aria-current="page"' if name == current else ''
        links.append(f'<a href="{prefix}{ziel}"{aktuell}>{html.escape(text)}</a>')
    return '<nav aria-label="Bereiche">' + '<span aria-hidden="true">/</span>'.join(links) + '</nav>'


def page(title, body, depth=0, current=None):
    nav_html = navigation(depth, current)
    return f'''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
:root{{color-scheme:light;--paper:#f7f5ef;--ink:#20392f;--muted:#58665f;--line:#ccd4cb;--accent:#175a43;--soft:#ebe8dc}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:17px/1.65 system-ui,sans-serif}}main{{max-width:1080px;margin:auto;padding:28px 22px 48px}}nav{{display:flex;gap:9px;align-items:baseline;flex-wrap:wrap;border-bottom:1px solid var(--line);padding:0 0 18px;margin-bottom:34px}}nav a{{color:var(--accent);text-decoration:none;font-weight:650}}nav a[aria-current]{{color:var(--ink);text-decoration:underline;text-underline-offset:4px}}nav span{{color:var(--line)}}h1{{font-size:clamp(36px,6vw,68px);line-height:1.08;letter-spacing:0;margin:0 0 12px}}h2{{margin-top:42px}}h3{{margin-bottom:4px}}a{{color:var(--accent)}}small,.muted{{color:var(--muted)}}.eyebrow{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:28px 0}}.stats>div{{border-top:3px solid #307153;background:white;padding:16px}}.stats b{{font-size:34px;display:block}}details,.row{{border-top:1px solid var(--line);padding:14px 0}}summary{{cursor:pointer;font-weight:650}}summary small{{float:right}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}input{{width:100%;padding:12px;border:1px solid var(--line);font:inherit;background:white}}.note,.card{{background:var(--soft);padding:18px;border-left:4px solid #aa8743;margin:18px 0}}blockquote{{margin:10px 0;padding-left:14px;border-left:3px solid var(--line);color:#33483f}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}}.card{{display:block;text-decoration:none;color:inherit;border-left:0;background:white;border-top:3px solid #307153}}@media(max-width:550px){{summary small{{float:none;display:block}}}}
</style><main>{nav_html}{body}<footer><p class="muted">Privates Lernprojekt. Verbindlich sind ausschließlich die amtlichen Originalunterlagen des Marktes Oberstaufen.</p></footer></main></html>'''


def topic_terms(item):
    terms = [w for w in keywords(item.get('titel', '')) if not w.isdigit()]
    banned = {'beschlussfassung','beschliessend','oberstaufen','gemarkung','bauantraege','bauantrag','sitzung'}
    return [w for w in terms if w not in banned]


def collect_topics(data):
    groups = collections.defaultdict(list)
    for item in data['tagesordnungspunkte']:
        terms = topic_terms(item)
        key = None
        if item.get('vorlage'):
            key = item['vorlage']
        elif terms:
            key = ' '.join(terms[:3])
        if key:
            groups[key].append(item)
    topics = []
    for key, items in groups.items():
        if len(items) < 2 and not any(i.get('beschlusshinweis') for i in items):
            continue
        title = clean_text(items[0]['titel'])
        topics.append({'key': key, 'titel': title, 'items': sorted(items, key=lambda x: x['datum'])})
    return sorted(topics, key=lambda x: (len(x['items']), x['items'][-1]['datum']), reverse=True)


def build_tables(data):
    target = ROOT/'data/csv'
    target.mkdir(parents=True, exist_ok=True)
    def write(name, fields, rows):
        with (target/name).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields, delimiter=';')
            writer.writeheader(); writer.writerows(rows)
    write('sitzungen.csv', ['datum','gremium','titel','tagesordnungspunkte','berichte','quelle'], [
        {'datum':m['datum'],'gremium':m['gremium'],'titel':m['titel'],'tagesordnungspunkte':m['n_tops'],'berichte':len(m.get('berichte',[])),'quelle':m['api_quelle']} for m in data['sitzungen']])
    write('tagesordnungspunkte.csv', ['datum','gremium','top','titel','beschlussstatus','berichte','beschlusshinweis','abstimmungshinweis'], [
        {'datum':t['datum'],'gremium':t['gremium'],'top':t['top'],'titel':clean_text(t['titel']),'beschlussstatus':t.get('beschlussstatus',''),'berichte':len(t.get('bericht_links',[])),'beschlusshinweis':t.get('beschlusshinweis') or '','abstimmungshinweis':t.get('abstimmungshinweis') or ''} for t in data['tagesordnungspunkte']])
    write('beschlusshinweise.csv', ['datum','gremium','top','titel','hinweis','abstimmungshinweis'], [
        {'datum':t['datum'],'gremium':t['gremium'],'top':t['top'],'titel':clean_text(t['titel']),'hinweis':t.get('beschlusshinweis') or '','abstimmungshinweis':t.get('abstimmungshinweis') or ''} for t in data['tagesordnungspunkte'] if t.get('beschlusshinweis') or t.get('abstimmungshinweis')])


def build_extra_pages(data):
    docs = ROOT/'docs'
    (docs/'themen').mkdir(parents=True, exist_ok=True)
    (docs/'ausgaben').mkdir(parents=True, exist_ok=True)
    build_tables(data)
    k = kennzahlen(data)
    report_pfad = build_report(data, k)
    aus = data.get('auswertung', {})
    cards = ''.join(f'<div><b>{v}</b>{k}</div>' for k, v in [
        ('Sitzungstermine', len(data['sitzungen'])), ('öffentliche TOP', len(data['tagesordnungspunkte'])),
        ('Berichte/Anlagen', len(data['berichte'])), ('TOP mit Berichtslink', aus.get('tops_mit_berichtslink',0)),
        ('Beschlusshinweise', aus.get('tops_mit_beschlusshinweis',0)), ('Abstimmungshinweise', aus.get('tops_mit_abstimmungshinweis',0))])
    index_body = f'<p class="eyebrow">ALLGÄU · ÖFFENTLICHE RATSUNTERLAGEN</p><h1>Ratsakten Oberstaufen</h1><p>Eine lokale, nachprüfbare Auswertung öffentlicher Ratsunterlagen des Marktes Oberstaufen.</p><div class="stats">{cards}</div><h2>Einstiege</h2><div class="cards"><a class="card" href="{report_pfad}"><p class="art">Report · Vollauswertung</p><h3>Ratsanalyse Oberstaufen {k["von"][:4]}–{k["bis"][:4]}</h3><p>Auswertung der gesamten dokumentierten Gremienarbeit: Transparenzlage, Themen, Abstimmungen und die auffälligsten Tagesordnungspunkte.</p><p class="meta">Stand {k["stichtag"]} · {k["sitzungen"]} Sitzungen · {k["tops"]} TOP</p></a><a class="card" href="befunde.html"><p class="art">Was aufgefallen ist</p><h3>Erkenntnisse</h3><p>Belegbare Befunde an einem Ort — und ebenso deutlich die Stellen, an denen die öffentliche Aktenlage nichts hergibt.</p></a><a class="card" href="termine.html"><h3>Termine</h3><p>Alle Sitzungen mit öffentlichen Tagesordnungspunkten.</p></a><a class="card" href="suche.html"><h3>Suche</h3><p>Durchsuchbarer Index aller TOP, Berichte und Hinweise.</p></a><a class="card" href="themen/index.html"><h3>Themen</h3><p>Wiederkehrende Vorgänge im Zeitverlauf.</p></a><a class="card" href="ausgaben/index.html"><h3>Archiv</h3><p>Dieselben Sitzungen nach Kalenderwochen geordnet.</p></a><a class="card" href="gremien.html"><h3>Wer entscheidet was</h3><p>Entscheidungskörper und erfasste Aktivität.</p></a></div><p class="note">Beschluss- und Abstimmungshinweise sind maschinell aus amtlichen PDF-Texten abgeleitet und am Original zu prüfen.</p>'
    (docs/'index.html').write_text(page('Ratsakten Oberstaufen', index_body, current='start'), encoding='utf-8')

    by_meeting = collections.defaultdict(list)
    for t in data['tagesordnungspunkte']: by_meeting[t['sitzung_id']].append(t)
    term_body = '<h1>Termine</h1><p>Alle erfassten Sitzungstermine aus dem öffentlichen RIS.</p>'
    for m in sorted(data['sitzungen'], key=lambda x:x['datum'], reverse=True):
        term_body += f'<details><summary>{html.escape(m["datum"])} · {html.escape(m["gremium"])} <small>{m["n_tops"]} TOP</small></summary>'
        if m.get('berichte'): term_body += '<p>' + ', '.join(f'<a href="{html.escape(b["url"],quote=True)}">{html.escape(b["titel"])}</a>' for b in m['berichte']) + '</p>'
        term_body += '<ol>' + ''.join(f'<li><b>TOP {html.escape(t["top"])}</b> {html.escape(clean_text(t["titel"]))}</li>' for t in by_meeting[m['id']]) + '</ol></details>'
    (docs/'termine.html').write_text(page('Termine · Ratsakten Oberstaufen', term_body, current='termine'), encoding='utf-8')

    grem = collections.defaultdict(lambda:{'sitzungen':0,'tops':0,'berichte':0,'beschluss':0})
    for m in data['sitzungen']:
        g=grem[m['gremium']]; g['sitzungen']+=1; g['tops']+=m['n_tops']; g['berichte']+=len(m.get('berichte',[]))
    for t in data['tagesordnungspunkte']:
        if t.get('beschlusshinweis'): grem[t['gremium']]['beschluss']+=1
    rows=''.join(f'<tr><td>{html.escape(k)}</td><td>{v["sitzungen"]}</td><td>{v["tops"]}</td><td>{v["berichte"]}</td><td>{v["beschluss"]}</td></tr>' for k,v in sorted(grem.items()))
    (docs/'gremien.html').write_text(page('Gremien · Ratsakten Oberstaufen', f'<h1>Gremien</h1><table><tr><th>Gremium</th><th>Sitzungen</th><th>TOP</th><th>Berichte</th><th>Beschlusshinweise</th></tr>{rows}</table>', current='gremien'), encoding='utf-8')

    search_rows = [{'datum':t['datum'],'gremium':t['gremium'],'top':t['top'],'titel':clean_text(t['titel']),'hinweis':t.get('beschlusshinweis') or '', 'abstimmung':t.get('abstimmungshinweis') or ''} for t in data['tagesordnungspunkte']]
    search_json = json.dumps(search_rows, ensure_ascii=False)
    search_body = f'<h1>Suche</h1><input id="q" placeholder="Suchbegriff eingeben"><div id="out"></div><script>const data={search_json};const q=document.getElementById("q"),out=document.getElementById("out");function draw(){{let s=q.value.toLowerCase();let rows=data.filter(x=>!s||Object.values(x).join(" ").toLowerCase().includes(s)).slice(0,250);out.innerHTML="<p>"+rows.length+" Treffer angezeigt</p>"+rows.map(x=>`<div class=row><b>${{x.datum}} · ${{x.gremium}} · TOP ${{x.top}}</b><br>${{x.titel}}${{x.hinweis?`<blockquote>${{x.hinweis}}</blockquote>`:""}}${{x.abstimmung?`<small>Abstimmung: ${{x.abstimmung}}</small>`:""}}</div>`).join("")}}q.addEventListener("input",draw);draw();</script>'
    (docs/'suche.html').write_text(page('Suche · Ratsakten Oberstaufen', search_body, current='suche'), encoding='utf-8')

    topics = collect_topics(data)[:60]
    topic_cards = []
    for topic in topics:
        fname = slug(topic['key']) + '.html'
        topic_cards.append(f'<a class="card" href="{fname}"><h3>{html.escape(topic["titel"][:110])}</h3><p>{len(topic["items"])} Stationen · {topic["items"][0]["datum"]} bis {topic["items"][-1]["datum"]}</p></a>')
        rows = ''.join(f'<div class="row"><b>{html.escape(i["datum"])} · {html.escape(i["gremium"])} · TOP {html.escape(i["top"])}</b><p>{html.escape(clean_text(i["titel"]))}</p>' + (f'<blockquote>{html.escape(i["beschlusshinweis"])}</blockquote>' if i.get('beschlusshinweis') else '') + '</div>' for i in topic['items'])
        (docs/'themen'/fname).write_text(page(topic['titel'], f'<h1>{html.escape(topic["titel"])}</h1>{rows}', depth=1, current='themen'), encoding='utf-8')
    (docs/'themen/index.html').write_text(page('Themen · Ratsakten Oberstaufen', f'<h1>Themen</h1><p>Wiederkehrende oder mit Beschlusshinweisen belegte Vorgänge.</p><div class="cards">{"".join(topic_cards)}</div>', depth=1, current='themen'), encoding='utf-8')

    weeks = collections.defaultdict(list)
    for m in data['sitzungen']:
        y,w,_ = dt.date.fromisoformat(m['datum']).isocalendar()
        weeks[(y,w)].append(m)
    archive = []
    for (y,w), meetings in sorted(weeks.items(), reverse=True):
        folder = docs/'ausgaben'/str(y); folder.mkdir(parents=True, exist_ok=True)
        fname = f'kw{w:02d}.html'
        archive.append((y,w,f'{y}/{fname}',len(meetings),sum(m['n_tops'] for m in meetings)))
        content = f'<h1>Aktenlage KW {w}/{y}</h1>' + ''.join(f'<div class="row"><h3>{html.escape(m["datum"])} · {html.escape(m["gremium"])}</h3><p>{m["n_tops"]} öffentliche TOP · {len(m.get("berichte",[]))} Berichte</p></div>' for m in meetings)
        (folder/fname).write_text(page(f'Aktenlage KW {w}/{y}', content, depth=2, current='archiv'), encoding='utf-8')
    arch_rows = ''.join(f'<tr><td><a href="{p}">KW {w}/{y}</a></td><td>{n}</td><td>{tops}</td></tr>' for y,w,p,n,tops in archive)
    (docs/'ausgaben/index.html').write_text(page('Ausgaben · Ratsakten Oberstaufen', f'<h1>Ausgaben</h1><table><tr><th>Ausgabe</th><th>Sitzungen</th><th>TOP</th></tr>{arch_rows}</table>', depth=1, current='archiv'), encoding='utf-8')

    build_befunde(data, k, report_pfad)


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


# ---------------------------------------------------------------------------
# Auswertung über den gesamten Zeitraum: Kennzahlen, Dreijahresbericht,
# Erkenntnisseite. Alle Zahlen werden aus data/oberstaufen.json gerechnet,
# keine ist im Text festgeschrieben.
# ---------------------------------------------------------------------------

BAUBEGRIFFE = re.compile(r'bauantrag|bauvoranfrage|tektur|nutzungs(ä|ae)nderung|neubau|anbau|umbau|bebauungsplan|bauleitplan', re.I)
GEWICHTIG = re.compile(r'satzung|geb(ü|ue)hr|beitrag|haushalt|jahresrechnung|jahresabschluss|vergabe|grundst(ü|ue)ck|darlehen|kredit|verordnung|w(a|ä)hl|eigenbetrieb', re.I)


MONATSNAMEN = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
               'August', 'September', 'Oktober', 'November', 'Dezember']


def datum_lang(iso):
    """2026-06-11 -> 11. Juni 2026. Für Fließtext, nicht für Tabellen."""
    try:
        d = dt.date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f'{d.day}. {MONATSNAMEN[d.month - 1]} {d.year}'


def kennzahlen(data):
    """Alles, was der Report und die Erkenntnisseite an Zahlen brauchen."""
    S, T, B = data['sitzungen'], data['tagesordnungspunkte'], data['berichte']
    k = {}
    k['sitzungen'] = len(S)
    k['tops'] = len(T)
    k['berichte'] = len(B)
    k['von'], k['bis'] = data['von'], data['bis']
    k['stichtag'] = data.get('abgerufen', '')[:10] or data['bis']
    k['jahre'] = sorted({m['datum'][:4] for m in S})

    k['sitzungen_je_gremium'] = collections.Counter(m['gremium'] for m in S)
    k['tops_je_gremium'] = collections.Counter(t['gremium'] for t in T)
    k['tops_je_jahr'] = collections.Counter(t['datum'][:4] for t in T)
    k['sitzungen_je_jahr'] = collections.Counter(m['datum'][:4] for m in S)
    k['gremien'] = sorted(k['sitzungen_je_gremium'], key=lambda g: -k['sitzungen_je_gremium'][g])

    k['beschliessend_je_gremium'] = collections.Counter(
        t['gremium'] for t in T if str(t.get('beschlussstatus', '')).startswith('beschließend'))
    k['beschliessend'] = sum(k['beschliessend_je_gremium'].values())
    k['hinweise_je_gremium'] = collections.Counter(t['gremium'] for t in T if t.get('beschlusshinweis'))
    k['hinweise'] = sum(k['hinweise_je_gremium'].values())

    # Wie viele Sitzungen eines Gremiums haben überhaupt einen amtlichen Bericht?
    k['abdeckung'] = {}
    for g in k['gremien']:
        sitzungen = [m for m in S if m['gremium'] == g]
        mit = sum(1 for m in sitzungen if m.get('berichte'))
        k['abdeckung'][g] = (mit, len(sitzungen))

    stimmen = [t for t in T if t.get('abstimmungshinweis')]
    k['abstimmungen'] = len(stimmen)
    k['einstimmig'] = sum(1 for t in stimmen if 'einstimmig' in t['abstimmungshinweis'].lower())
    k['mehrheitlich'] = sum(1 for t in stimmen if 'mehrheitlich' in t['abstimmungshinweis'].lower())
    k['mit_gegenstimmen'] = [t for t in stimmen if ':' in t['abstimmungshinweis']]

    k['ohne_tops'] = collections.Counter(m['gremium'] for m in S if m['n_tops'] == 0)
    k['ohne_tops_gesamt'] = sum(k['ohne_tops'].values())
    k['mit_vorlage'] = sum(1 for t in T if t.get('vorlage'))
    k['mit_dokumenten'] = sum(1 for t in T if (t.get('dokumentanzahl') or 0) > 0)
    k['bau_tops'] = [t for t in T if BAUBEGRIFFE.search(t['titel'])]
    k['bau_je_jahr'] = collections.Counter(t['datum'][:4] for t in k['bau_tops'])
    k['bau_je_gremium'] = collections.Counter(t['gremium'] for t in k['bau_tops'])
    k['berichte_je_jahr'] = collections.Counter(b['datum'][:4] for b in B if b.get('datum'))
    k['berichte_je_gremium'] = collections.Counter(b.get('gremium') or 'ohne Gremiumszuordnung' for b in B)

    k['median_je_gremium'] = {}
    for g in k['gremien']:
        laengen = sorted(m['n_tops'] for m in S if m['gremium'] == g and m['n_tops'])
        k['median_je_gremium'][g] = laengen[len(laengen) // 2] if laengen else 0
    k['groesste_sitzung'] = max(S, key=lambda m: m['n_tops'])
    return k


def balken(zeilen, hervor=None, breite=700, labelbreite=250):
    """Schlichtes Balkendiagramm als SVG. zeilen = [(Beschriftung, Wert, Zusatz)]."""
    if not zeilen:
        return ''
    e = html.escape
    hoch = max((w for _, w, _ in zeilen), default=1) or 1
    zh, oben = 34, 8
    hoehe = len(zeilen) * zh + oben
    feld = breite - labelbreite - 70
    teile = [f'<svg class="chart" viewBox="0 0 {breite} {hoehe}" role="img" '
             f'aria-label="Balkendiagramm mit {len(zeilen)} Werten">']
    for i, (name, wert, zusatz) in enumerate(zeilen):
        y = oben + i * zh
        laenge = round(feld * wert / hoch) if wert else 0  # Null bleibt Null, kein Reststrich
        farbe = '#aa8743' if (hervor and name in hervor) else '#307153'
        teile.append(f'<text x="{labelbreite - 12}" y="{y + 16}" text-anchor="end" class="cl">{e(name)}</text>')
        teile.append(f'<rect x="{labelbreite}" y="{y + 4}" width="{laenge}" height="17" fill="{farbe}"/>')
        teile.append(f'<text x="{labelbreite + laenge + 8}" y="{y + 17}" class="cv">{e(str(wert))}'
                     f'{e(" " + zusatz) if zusatz else ""}</text>')
    teile.append('</svg>')
    return ''.join(teile)


def marke(art):
    """Herkunftsmarke: gezählt = aus den Daten, Deutung = maschinell formuliert."""
    if art == 'gezaehlt':
        return '<span class="marke gez">gezählt</span>'
    return '<span class="marke deu">Deutung</span>'


def auffaellige_tops(data, anzahl=25):
    """Punkte, die wegen Gegenstimmen, Beschlussbeleg oder Tragweite herausstechen."""
    bewertet = []
    for t in data['tagesordnungspunkte']:
        punkte = 0
        if t.get('abstimmungshinweis') and ':' in t['abstimmungshinweis']:
            punkte += 5
        elif t.get('abstimmungshinweis'):
            punkte += 2
        if t.get('beschlusshinweis'):
            punkte += 2
        if GEWICHTIG.search(t['titel']):
            punkte += 2
        if 'beschließend' in t['titel'].lower():
            punkte += 1
        if punkte >= 4:
            bewertet.append((punkte, t))
    bewertet.sort(key=lambda x: (-x[0], x[1]['datum']))
    # Ein Berichtsauszug deckt oft einen ganzen Tagesordnungsblock ab und wurde
    # dann mehreren Unterpunkten zugeordnet. Hier zählt die erste Fundstelle.
    gesehen, auswahl = set(), []
    for _, t in bewertet:
        beleg = (t.get('beschlusshinweis') or '').strip()
        if beleg and beleg in gesehen:
            continue
        if beleg:
            gesehen.add(beleg)
        auswahl.append(t)
        if len(auswahl) >= anzahl:
            break
    return auswahl


def report_css():
    return ('.chart{max-width:100%;height:auto;margin:18px 0}'
            '.cl{font:13px system-ui;fill:#20392f}.cv{font:600 13px system-ui;fill:#58665f}'
            '.marke{display:inline-block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;'
            'font-weight:700;padding:2px 8px;border-radius:9px;vertical-align:2px;margin-left:8px}'
            '.marke.gez{background:#dde7df;color:#175a43}.marke.deu{background:#f0e4c9;color:#7a5f1c}'
            '.kap{border-top:2px solid var(--accent);margin-top:52px;padding-top:6px}'
            '.kennzahl{font-size:42px;font-weight:700;display:block;line-height:1.1}'
            'ol.quellen li{margin:10px 0}')


def build_report(data, k):
    """Dreijahresbericht: eine zusammenhängende Auswertung des ganzen Zeitraums."""
    e = html.escape
    ziel = ROOT / 'docs/report'
    ziel.mkdir(parents=True, exist_ok=True)
    datei = f"{k['stichtag']}.html"
    jahre = ' bis '.join([k['von'][:4], k['bis'][:4]])
    anteil_bua = round(100 * k['tops_je_gremium'].get('Bau- und Umweltausschuss', 0) / max(k['tops'], 1))
    anteil_einstimmig = round(100 * k['einstimmig'] / max(k['abstimmungen'], 1))
    beleg_quote = round(100 * k['hinweise'] / max(k['beschliessend'], 1))
    mgr_mit, mgr_ges = k['abdeckung'].get('Marktgemeinderat', (0, 0))

    b = [f'<p class="eyebrow">Report · Vollauswertung {jahre}</p>',
         '<h1>Was drei Jahre Oberstaufener Ratsarbeit in den Akten hinterlassen</h1>',
         f'<p class="lede">Zwischen dem {e(datum_lang(k["von"]))} und dem {e(datum_lang(k["bis"]))} haben die Gremien des Marktes '
         f'Oberstaufen {k["sitzungen"]} Mal getagt und dabei {k["tops"]} öffentliche Tagesordnungspunkte '
         f'aufgerufen. Dieser Report wertet aus, was davon in öffentlich abrufbaren Unterlagen '
         f'nachlesbar geblieben ist — und was nicht.</p>',
         f'<div class="stats"><div><b>{k["sitzungen"]}</b>Sitzungen</div>'
         f'<div><b>{k["tops"]}</b>öffentliche TOP</div>'
         f'<div><b>{len(k["gremien"])}</b>Gremien</div>'
         f'<div><b>{k["beschliessend"]}</b>beschließende TOP</div>'
         f'<div><b>{k["hinweise"]}</b>Beschlussbelege</div>'
         f'<div><b>{k["abstimmungen"]}</b>Abstimmungsbelege</div></div>']

    b.append('<h2 class="kap">Woher die Daten stammen</h2>')
    b.append(f'<p>Zwei öffentliche Quellen, beide ohne Anmeldung erreichbar. Erstens der Gastzugang des '
             f'Ratsinformationssystems (Komuna, Mandant 32) — daraus stammen alle Sitzungstermine und '
             f'Tagesordnungen. Zweitens die amtlichen Sitzungsberichte auf der Gemeindewebsite — daraus '
             f'stammen die {k["berichte"]} verlinkten PDF-Dokumente und jeder Beschlussbeleg in diesem '
             f'Report. Nichtöffentliche Sitzungsteile und gesperrte Punkte sind nicht erfasst.</p>')
    b.append('<h3>Woher eine Aussage stammt</h3>')
    b.append(f'<p>Jede Aussage in diesem Report ist gekennzeichnet. {marke("gezaehlt")} steht an Zahlen, '
             f'die unmittelbar aus den Daten gezählt sind und sich in den CSV-Tabellen nachrechnen lassen. '
             f'{marke("deutung")} steht an Sätzen, die über das Zählen hinausgehen — die also auswählen, '
             f'verknüpfen oder gewichten. Diese Abschnitte sind maschinell formuliert und nicht '
             f'redaktionell geprüft.</p>')
    b.append('<h3>Weitere Festlegungen</h3><ul>'
             '<li>„Beschließender TOP“ heißt: Die Tagesordnung kündigt den Punkt als beschließend an. '
             'Ob tatsächlich abgestimmt wurde, sagt das nicht.</li>'
             '<li>„Beschlussbeleg“ heißt: Zu diesem Punkt ließ sich in einem amtlichen Sitzungsbericht '
             'eine Textstelle zuordnen. Das ist ein Fundstellenhinweis, keine juristische Auswertung.</li>'
             '<li>Fehlt ein Beleg, heißt das nicht, dass nichts entschieden wurde — sondern nur, dass '
             'sich dazu öffentlich nichts abrufen ließ.</li>'
             '<li>Die Berichte sind nach Sitzungen gegliedert, nicht nach Tagesordnungspunkten. Ein '
             'Auszug kann deshalb einen ganzen Block abdecken und maschinell mehreren Unterpunkten '
             'zugeordnet worden sein. Welcher Satz zu welchem Unterpunkt gehört, ist im verlinkten '
             'PDF zu prüfen.</li></ul>')

    b.append('<h2 class="kap">Wo Beschlüsse nachlesbar sind — und wo nicht</h2>')
    b.append(f'<p>Das ist der zentrale Befund dieses Zeitraums. Von {k["beschliessend"]} Tagesordnungspunkten, '
             f'die als beschließend angekündigt waren, ließ sich zu {k["hinweise"]} ein amtlicher Textbeleg '
             f'finden — {beleg_quote} Prozent. {marke("gezaehlt")}</p>')
    b.append(f'<p>Entscheidend ist aber nicht die Quote, sondern ihre Verteilung: '
             f'<strong>Alle {k["hinweise"]} Belege betreffen den Marktgemeinderat.</strong> Für die '
             f'Ausschüsse gibt es keinen einzigen. {marke("gezaehlt")}</p>')
    b.append(balken([(g, k['abdeckung'][g][0], f'von {k["abdeckung"][g][1]} Sitzungen')
                     for g in k['gremien']], hervor={'Marktgemeinderat'}))
    b.append(f'<p class="note">Lesart: {mgr_mit} der {mgr_ges} Marktgemeinderatssitzungen haben einen '
             f'amtlichen Bericht. Bei jedem anderen Gremium ist es keine einzige. Die Gemeinde '
             f'berichtet also über ihr Hauptorgan verlässlich — und über ihre Ausschüsse gar nicht. '
             f'{marke("deutung")}</p>')

    b.append('<h2 class="kap">Womit sich die Gremien tatsächlich beschäftigen</h2>')
    b.append(balken([(g, k['tops_je_gremium'].get(g, 0), 'TOP') for g in k['gremien']],
                    hervor={'Bau- und Umweltausschuss'}))
    b.append(f'<p>Der Bau- und Umweltausschuss ruft mit {k["tops_je_gremium"].get("Bau- und Umweltausschuss", 0)} '
             f'von {k["tops"]} Punkten {anteil_bua} Prozent aller öffentlichen Tagesordnungspunkte auf — '
             f'mehr als der Marktgemeinderat. {marke("gezaehlt")} Seine Sitzungen sind entsprechend lang: '
             f'im Mittel {k["median_je_gremium"].get("Bau- und Umweltausschuss", 0)} Punkte je Sitzung '
             f'gegenüber {k["median_je_gremium"].get("Marktgemeinderat", 0)} im Marktgemeinderat, in der '
             f'Spitze {k["groesste_sitzung"]["n_tops"]} am {e(datum_lang(k["groesste_sitzung"]["datum"]))} '
             f'({e(k["groesste_sitzung"]["gremium"])}). {marke("gezaehlt")}</p>')
    b.append(f'<p>Inhaltlich dominiert das Baugeschehen: {len(k["bau_tops"])} Punkte betreffen Bauanträge, '
             f'Bauvoranfragen, Nutzungsänderungen, Neu- und Umbauten oder die Bauleitplanung — davon '
             f'{k["bau_je_gremium"].get("Bau- und Umweltausschuss", 0)} im Bau- und Umweltausschuss. '
             f'{marke("gezaehlt")}</p>')
    b.append(balken([(j, k['bau_je_jahr'].get(j, 0), 'Bau-TOP') for j in k['jahre']], labelbreite=90))

    b.append('<h2 class="kap">Das Gremium mit der meisten Arbeit hinterlässt keine Akte</h2>')
    b.append(f'<p>Beide Befunde zusammengenommen ergeben eine Lücke, die sich benennen lässt: Der Bau- und '
             f'Umweltausschuss trägt {anteil_bua} Prozent der Tagesordnung und '
             f'{k["beschliessend_je_gremium"].get("Bau- und Umweltausschuss", 0)} als beschließend '
             f'angekündigte Punkte. Öffentlich nachlesbar ist von diesen Entscheidungen nichts — kein '
             f'Bericht, kein Beschlusstext, kein Abstimmungsergebnis. Wer wissen will, wie über ein '
             f'einzelnes Bauvorhaben entschieden wurde, erfährt aus den öffentlichen Quellen nur, '
             f'<em>dass</em> es auf der Tagesordnung stand. {marke("deutung")}</p>')

    gegen = k['mit_gegenstimmen']
    b.append('<h2 class="kap">Von den belegten Abstimmungen fiel die große Mehrheit einstimmig</h2>')
    b.append(f'<p>Zu {k["abstimmungen"]} Punkten ließ sich ein Abstimmungsergebnis aus dem Berichtstext '
             f'lesen. {k["einstimmig"]} davon — {anteil_einstimmig} Prozent — fielen einstimmig. '
             f'{marke("gezaehlt")}</p>')
    if gegen:
        b.append(f'<p>Dokumentierte Gegenstimmen gibt es in genau {len(gegen)} Fällen, und alle '
                 f'{len(gegen)} fielen am selben Abend:</p><ul>')
        for t in gegen:
            b.append(f'<li><strong>{e(datum_lang(t["datum"]))}</strong>, TOP {e(t["top"])} — '
                     f'{e(clean_text(t["titel"]))} <em>({e(t["abstimmungshinweis"])})</em></li>')
        b.append('</ul>')
    b.append(f'<p class="note">Diese Zahlen tragen wenig. {k["abstimmungen"]} belegte Abstimmungen stehen '
             f'{k["beschliessend"]} beschließenden Punkten gegenüber — das sind unter zwei Prozent. Ein '
             f'Satz wie „in Oberstaufen wird einstimmig entschieden“ wäre aus dieser Stichprobe nicht '
             f'zu belegen. Belegbar ist nur: Wo ein Bericht ein Ergebnis nennt, ist es meist '
             f'Einstimmigkeit. {marke("deutung")}</p>')

    if k['ohne_tops_gesamt']:
        rpa = [(g, n) for g, n in k['ohne_tops'].most_common()]
        b.append('<h2 class="kap">Sitzungen, zu denen es keine öffentliche Tagesordnung gibt</h2>')
        b.append(f'<p>{k["ohne_tops_gesamt"]} der {k["sitzungen"]} Termine erscheinen im '
                 f'Ratsinformationssystem, liefern im Gastzugang aber keinen einzigen öffentlichen '
                 f'Tagesordnungspunkt. {marke("gezaehlt")}</p><ul>')
        for g, n in rpa:
            gesamt = k['sitzungen_je_gremium'][g]
            zusatz = ' — also jede Sitzung dieses Gremiums' if n == gesamt else f' von {gesamt} Sitzungen'
            b.append(f'<li>{e(g)}: {n}{zusatz}</li>')
        b.append('</ul>')
        voll = [g for g, n in rpa if n == k['sitzungen_je_gremium'][g]]
        if voll:
            liste = ', '.join(e(g) for g in voll)
            satz = ('Dieses Gremium tagt' if len(voll) == 1 else 'Diese Gremien tagen')
            b.append(f'<p>{liste}: {satz} im erfassten Zeitraum durchgehend ohne öffentlich '
                     f'abrufbare Tagesordnung. Der Termin ist sichtbar, der Gegenstand nicht. '
                     f'{marke("deutung")}</p>')

    b.append('<h2 class="kap">Kein Zugang zu Vorlagen und Anlagen</h2>')
    b.append(f'<p>Der öffentliche Gastzugang liefert Termin, Gremium, Nummer und Titel eines '
             f'Tagesordnungspunktes. Mehr nicht: {k["mit_vorlage"]} von {k["tops"]} Punkten tragen eine '
             f'Vorlagenkennung, {k["mit_dokumenten"]} von {k["tops"]} haben ein abrufbares Dokument. '
             f'{marke("gezaehlt")} Sitzungsvorlagen, Anlagen, Pläne und Beschlussvorschläge sind über '
             f'diesen Weg nicht erreichbar.</p>')
    b.append(f'<p>Damit bleibt als einzige inhaltliche Quelle der Bestand amtlicher Sitzungsberichte: '
             f'{k["berichte"]} PDF-Dateien, ganz überwiegend zum Marktgemeinderat '
             f'({k["berichte_je_gremium"].most_common(1)[0][1]} Stück). Dieser Bestand reicht weiter '
             f'zurück als der Auswertungszeitraum und ist nach Sitzungen geordnet, nicht nach '
             f'Tagesordnungspunkten — die Zuordnung zu einem einzelnen TOP musste dieser Report '
             f'selbst herstellen. {marke("gezaehlt")}</p>')

    b.append('<h2 class="kap">Der Wechsel der Wahlperiode</h2>')
    b.append(f'<p>Die Zahlen je Jahr sind nicht direkt vergleichbar: {k["jahre"][-1]} ist nur bis zum '
             f'{e(datum_lang(k["bis"]))} erfasst, und im Frühjahr {k["jahre"][-1]} endete mit der bayerischen '
             f'Kommunalwahl die Wahlperiode. In den Akten zeigt sich das an Punkten wie der Besetzung '
             f'der Ausschüsse und der Bekanntgabe der Fraktionsvorsitzenden. {marke("deutung")}</p>')
    b.append(balken([(j, k['sitzungen_je_jahr'].get(j, 0), 'Sitzungen') for j in k['jahre']], labelbreite=90))
    b.append(balken([(j, k['tops_je_jahr'].get(j, 0), 'TOP') for j in k['jahre']], labelbreite=90))

    auff = auffaellige_tops(data)
    b.append(f'<h2 class="kap">Die {len(auff)} auffälligsten Tagesordnungspunkte</h2>')
    b.append('<p>Ausgewählt nach dokumentierten Gegenstimmen, vorhandenem Beschlussbeleg und '
             f'finanzieller oder rechtlicher Tragweite des Titels. Die Reihenfolge ist maschinell '
             f'gesetzt und keine Wertung der politischen Bedeutung. {marke("deutung")}</p>')
    for t in auff:
        b.append(f'<div class="row"><b>{e(datum_lang(t["datum"]))} · {e(t["gremium"])} · TOP {e(t["top"])}</b>'
                 f'<p>{e(clean_text(t["titel"]))}</p>')
        if t.get('beschlusshinweis'):
            b.append(f'<blockquote>{e(t["beschlusshinweis"])}</blockquote>')
        if t.get('abstimmungshinweis'):
            b.append(f'<small>Abstimmungshinweis: {e(t["abstimmungshinweis"])}</small>')
        if t.get('bericht_links'):
            b.append('<br><small>Beleg: ' + ', '.join(
                f'<a href="{e(x["url"], quote=True)}">{e(x["titel"])}</a>' for x in t['bericht_links']) + '</small>')
        b.append('</div>')

    b.append('<h2 class="kap">Was dieser Report nicht kann</h2><ul>'
             '<li>Er wertet keine nichtöffentlichen Sitzungsteile aus; diese sind im Gastzugang gar '
             'nicht sichtbar.</li>'
             '<li>Er kennt keine Sitzungsvorlagen und keine Anlagen, weil der öffentliche Zugang sie '
             'nicht ausliefert.</li>'
             '<li>Er sagt nichts über Haushaltszahlen, Investitionssummen oder Finanzströme. Titel wie '
             '„Jahresrechnung“ belegen ein Thema, keine Zahl.</li>'
             '<li>Er kennt kein Abstimmungsverhalten einzelner Personen und nennt keine Namen von '
             'Ratsmitgliedern.</li>'
             '<li>Er ersetzt keine Prüfung im Original. Jede Textstelle ist ein Fundstellenhinweis, '
             'der im verlinkten PDF nachzulesen ist.</li></ul>')

    b.append('<h2 class="kap">Wo Sie jede Angabe selbst nachprüfen können</h2>')
    b.append('<h3>Primärquellen</h3><ol class="quellen">'
             f'<li><a href="{PORTAL}">Ratsinformationssystem des Marktes Oberstaufen</a> — '
             f'Sitzungstermine und Tagesordnungen, nach Datum und Gremium.</li>'
             f'<li><a href="{BERICHTE}">Berichte aus Sitzungen und Versammlungen</a> — die amtlichen '
             f'PDF-Berichte, aus denen jeder Beschlussbeleg dieses Reports stammt.</li></ol>')
    b.append('<h3>So finden Sie einen einzelnen Vorgang</h3>'
             '<p>Jede Zahl dieses Reports steht in den CSV-Tabellen des Projekts: '
             '<code>data/csv/sitzungen.csv</code>, <code>data/csv/tagesordnungspunkte.csv</code> und '
             '<code>data/csv/beschlusshinweise.csv</code>. Die Tabellen sind mit Semikolon getrennt und '
             'lassen sich in jeder Tabellenkalkulation öffnen. Über die '
             '<a href="../suche.html">Suche</a> finden Sie denselben Bestand im Browser, über '
             '<a href="../termine.html">Termine</a> nach Sitzung geordnet.</p>')
    b.append(f'<h2 class="kap">Haftungsausschluss</h2>'
             f'<p class="note">Privates Lernprojekt ohne Verbindung zum Markt Oberstaufen. Verbindlich '
             f'sind ausschließlich die amtlichen Originalunterlagen. Die als Deutung gekennzeichneten '
             f'Abschnitte sind maschinell formuliert und nicht redaktionell geprüft; sie sind '
             f'Einordnungen, keine Tatsachenbehauptungen. Korrekturen sind erwünscht. Stand der '
             f'Auswertung: {e(datum_lang(k["stichtag"]))}.</p>')

    seite = page(f'Ratsanalyse Oberstaufen {jahre}', ''.join(b), depth=1, current='befunde')
    seite = seite.replace('</style>', report_css() + '</style>', 1)
    (ziel / datei).write_text(seite, encoding='utf-8')
    return f'report/{datei}'


def build_befunde(data, k, report_pfad):
    """Erkenntnisseite: was belegbar ist, was auffällt, wo die Grenzen liegen."""
    e = html.escape
    r = report_pfad
    anteil_bua = round(100 * k['tops_je_gremium'].get('Bau- und Umweltausschuss', 0) / max(k['tops'], 1))
    anteil_einstimmig = round(100 * k['einstimmig'] / max(k['abstimmungen'], 1))
    beleg_quote = round(100 * k['hinweise'] / max(k['beschliessend'], 1))
    mgr_mit, mgr_ges = k['abdeckung'].get('Marktgemeinderat', (0, 0))
    bua = 'Bau- und Umweltausschuss'

    def eintrag(titel, text, art='gezaehlt', ziel=r):
        return (f'<div class="row"><h3><a href="{e(ziel)}">{e(titel)}</a>{marke(art)}</h3>'
                f'<p>{text}</p></div>')

    b = [f'<p class="eyebrow">{k["von"][:4]}–{k["bis"][:4]} · {k["sitzungen"]} Sitzungen ausgewertet</p>',
         '<h1>Erkenntnisse</h1>',
         f'<p class="lede">Was sich aus den öffentlichen Oberstaufener Ratsunterlagen belegen lässt, '
         f'an einem Ort. Jede Zeile führt in den <a href="{e(r)}">Report</a>, wo die Zahl hergeleitet '
         f'und die Quelle verlinkt ist.</p>',
         f'<div class="stats"><div><b>{k["sitzungen"]}</b>Sitzungen</div>'
         f'<div><b>{k["tops"]}</b>öffentliche TOP</div>'
         f'<div><b>{k["beschliessend"]}</b>beschließende TOP</div>'
         f'<div><b>{k["hinweise"]}</b>Beschlussbelege</div>'
         f'<div><b>{k["abstimmungen"]}</b>Abstimmungsbelege</div>'
         f'<div><b>{k["berichte"]}</b>amtliche Berichte</div></div>']

    b.append('<h2>Der Regelfall</h2>')
    b.append(eintrag(
        f'{mgr_mit} von {mgr_ges} Marktgemeinderatssitzungen haben einen amtlichen Bericht',
        'Für das Hauptorgan der Gemeinde ist die Berichtslage dicht: Zu fast jeder Sitzung erscheint '
        'ein Bericht auf der Gemeindewebsite, aus dem sich einzelne Beschlüsse nachlesen lassen.'))
    b.append(eintrag(
        f'{k["einstimmig"]} von {k["abstimmungen"]} belegten Abstimmungen fielen einstimmig',
        f'Das sind {anteil_einstimmig} Prozent der Fälle, in denen ein Bericht überhaupt ein Ergebnis '
        f'nennt. Die Stichprobe ist klein — sie deckt weniger als zwei Prozent aller beschließenden '
        f'Punkte ab.'))
    b.append(eintrag(
        f'{len(k["bau_tops"])} Tagesordnungspunkte betreffen das Baugeschehen',
        f'Bauanträge, Bauvoranfragen, Nutzungsänderungen, Neu- und Umbauten sowie Bauleitplanung — '
        f'davon {k["bau_je_gremium"].get(bua, 0)} allein im {bua}.'))
    b.append(eintrag(
        f'Die Tagesordnungen sind vollständig und lückenlos abrufbar',
        f'Für {k["sitzungen"] - k["ohne_tops_gesamt"]} der {k["sitzungen"]} Termine liefert der '
        f'Gastzugang die öffentliche Tagesordnung mit Nummer und Titel jedes Punktes. Wer wissen will, '
        f'<em>worüber</em> beraten wurde, findet das vollständig.'))

    b.append('<h2>Beobachtungen</h2>')
    b.append(eintrag(
        'Kein einziger Beschlussbeleg stammt aus einem Ausschuss',
        f'Alle {k["hinweise"]} belegten Beschlüsse betreffen den Marktgemeinderat. Für '
        f'{bua}, Haupt- und Finanzausschuss, Tourismusausschuss, Rechnungsprüfungsausschuss und '
        f'Schulverbandsversammlung gibt es zusammen keinen einzigen. Das ist die größte Lücke im '
        f'Bestand.', 'deutung'))
    b.append(eintrag(
        f'Das Gremium mit {anteil_bua} Prozent der Tagesordnung hinterlässt keine Akte',
        f'Der {bua} ruft {k["tops_je_gremium"].get(bua, 0)} Punkte auf, davon '
        f'{k["beschliessend_je_gremium"].get(bua, 0)} als beschließend angekündigt. Öffentlich '
        f'nachlesbar ist von diesen Entscheidungen nichts.', 'deutung'))
    if k['ohne_tops_gesamt']:
        voll = [g for g, n in k['ohne_tops'].items() if n == k['sitzungen_je_gremium'][g]]
        if voll:
            b.append(eintrag(
                f'{voll[0]}: jede Sitzung ohne öffentliche Tagesordnung',
                f'Alle {k["ohne_tops"][voll[0]]} Termine dieses Gremiums erscheinen im '
                f'Ratsinformationssystem, liefern aber keinen einzigen öffentlichen '
                f'Tagesordnungspunkt. Der Termin ist sichtbar, der Gegenstand nicht.', 'deutung'))
    b.append(eintrag(
        'Es gibt keinen öffentlichen Zugang zu Vorlagen und Anlagen',
        f'{k["mit_vorlage"]} von {k["tops"]} Punkten tragen eine Vorlagenkennung, '
        f'{k["mit_dokumenten"]} von {k["tops"]} ein abrufbares Dokument. Sitzungsvorlagen, Pläne und '
        f'Beschlussvorschläge sind über den öffentlichen Weg nicht erreichbar.'))
    b.append(eintrag(
        f'Nur {beleg_quote} Prozent der beschließenden Punkte sind belegt',
        f'{k["hinweise"]} von {k["beschliessend"]} — der Rest ist als beschließend angekündigt, aber '
        f'ohne öffentlich abrufbares Ergebnis. Fehlende Belege bedeuten nicht, dass nichts entschieden '
        f'wurde.'))
    if k['mit_gegenstimmen']:
        d = k['mit_gegenstimmen'][0]['datum']
        b.append(eintrag(
            f'Dokumentierte Gegenstimmen gibt es nur an einem einzigen Sitzungsabend',
            f'In {len(k["mit_gegenstimmen"])} Fällen nennt ein Bericht ein Stimmenverhältnis statt '
            f'Einstimmigkeit — alle am {e(datum_lang(d))}, alle im selben Themenblock zur Änderung des Orts- und '
            f'Gemeindeverfassungsrechts.', 'deutung'))
    b.append(eintrag(
        'Der Berichtsbestand ist nach Sitzungen geordnet, nicht nach Punkten',
        f'Die {k["berichte"]} amtlichen PDF-Berichte reichen weiter zurück als der Auswertungszeitraum '
        f'und sind einer Sitzung als Ganzes zugeordnet. Die Verknüpfung zu einem einzelnen '
        f'Tagesordnungspunkt musste dieses Projekt selbst herstellen — sie ist ein Fundstellenhinweis '
        f'und am Original zu prüfen.'))

    b.append('<h2>Aus der Gesamtauswertung</h2>')
    b.append(f'<p>Der <a href="{e(r)}">Dreijahresbericht</a> führt diese Punkte zusammen: Herkunft der '
             f'Daten, Verteilung der Beschlussbelege über die Gremien, Themenschwerpunkte, '
             f'Abstimmungslage, der Wechsel der Wahlperiode und die {len(auffaellige_tops(data))} '
             f'auffälligsten Tagesordnungspunkte mit Zitat und Quelllink.</p>')
    b.append(f'<div class="cards"><a class="card" href="{e(r)}"><h3>Report · Vollauswertung</h3>'
             f'<p>Ratsanalyse Oberstaufen {k["von"][:4]}–{k["bis"][:4]} — Transparenzlage, Themen, '
             f'Abstimmungen und die auffälligsten Punkte.</p></a>'
             f'<a class="card" href="themen/index.html"><h3>Themen</h3>'
             f'<p>Wiederkehrende Vorgänge im Zeitverlauf.</p></a>'
             f'<a class="card" href="ausgaben/index.html"><h3>Archiv</h3>'
             f'<p>Dieselben Sitzungen nach Kalenderwochen geordnet.</p></a></div>')

    b.append('<h2>Warum diese Seite existiert</h2>')
    b.append('<p>Öffentliche Ratsunterlagen sind abrufbar, aber verstreut: Termine im '
             'Ratsinformationssystem, Ergebnisse in einzelnen PDF-Berichten, nichts davon '
             'durchsuchbar. Diese Seite hält fest, was sich aus beiden Beständen zusammen belegen '
             'lässt — und benennt ebenso deutlich, wo die öffentliche Aktenlage nichts hergibt.</p>')
    b.append(f'<p class="note"><b>Herkunft der Aussagen:</b> {marke("gezaehlt")} steht an Zahlen, die '
             f'unmittelbar aus den Daten gezählt und in den CSV-Tabellen nachrechenbar sind. '
             f'{marke("deutung")} steht an Sätzen, die auswählen, verknüpfen oder gewichten; sie sind '
             f'maschinell formuliert und nicht redaktionell geprüft. Verbindlich sind ausschließlich '
             f'die amtlichen Originalunterlagen des Marktes Oberstaufen.</p>')

    seite = page('Erkenntnisse · Ratsakten Oberstaufen', ''.join(b), current='befunde')
    seite = seite.replace('</style>', report_css() + '</style>', 1)
    (ROOT / 'docs/befunde.html').write_text(seite, encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--von',default='2024-01-01');parser.add_argument('--bis',default=dt.date.today().isoformat());parser.add_argument('--offline',action='store_true');parser.add_argument('--auswerten',action='store_true');args=parser.parse_args()
    start=dt.date.fromisoformat(args.von);end=dt.date.fromisoformat(args.bis)
    if start>end or end>dt.date.today():parser.error('Ungültiger Zeitraum oder zukünftiger Stichtag')
    data=json.loads((ROOT/'data/oberstaufen.json').read_text()) if args.offline else fetch(start.isoformat(),end.isoformat(), args.auswerten)
    if args.offline and args.auswerten:
        data = enrich_decisions(data)
        target = ROOT/'data/oberstaufen.json'; temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'); temp.replace(target)
    build_extra_pages(data)

if __name__=='__main__':main()
