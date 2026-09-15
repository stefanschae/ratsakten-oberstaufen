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


def page(title, body, nav=''):
    nav_html = nav or '<nav><a href="index.html">Start</a><a href="termine.html">Termine</a><a href="suche.html">Suche</a><a href="gremien.html">Gremien</a><a href="themen/index.html">Themen</a><a href="ausgaben/index.html">Ausgaben</a><a href="befunde.html">Befunde</a></nav>'
    return f'''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
:root{{color-scheme:light;--paper:#f7f5ef;--ink:#20392f;--muted:#58665f;--line:#ccd4cb;--accent:#175a43;--soft:#ebe8dc}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:17px/1.65 system-ui,sans-serif}}main{{max-width:1080px;margin:auto;padding:28px 22px 48px}}nav{{display:flex;gap:14px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding:0 0 18px;margin-bottom:34px}}nav a{{color:var(--accent);text-decoration:none;font-weight:650}}h1{{font-size:clamp(36px,6vw,68px);line-height:1.08;letter-spacing:0;margin:0 0 12px}}h2{{margin-top:42px}}h3{{margin-bottom:4px}}a{{color:var(--accent)}}small,.muted{{color:var(--muted)}}.eyebrow{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:28px 0}}.stats>div{{border-top:3px solid #307153;background:white;padding:16px}}.stats b{{font-size:34px;display:block}}details,.row{{border-top:1px solid var(--line);padding:14px 0}}summary{{cursor:pointer;font-weight:650}}summary small{{float:right}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}input{{width:100%;padding:12px;border:1px solid var(--line);font:inherit;background:white}}.note,.card{{background:var(--soft);padding:18px;border-left:4px solid #aa8743;margin:18px 0}}blockquote{{margin:10px 0;padding-left:14px;border-left:3px solid var(--line);color:#33483f}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}}.card{{display:block;text-decoration:none;color:inherit;border-left:0;background:white;border-top:3px solid #307153}}@media(max-width:550px){{summary small{{float:none;display:block}}}}
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
    aus = data.get('auswertung', {})
    cards = ''.join(f'<div><b>{v}</b>{k}</div>' for k, v in [
        ('Sitzungstermine', len(data['sitzungen'])), ('öffentliche TOP', len(data['tagesordnungspunkte'])),
        ('Berichte/Anlagen', len(data['berichte'])), ('TOP mit Berichtslink', aus.get('tops_mit_berichtslink',0)),
        ('Beschlusshinweise', aus.get('tops_mit_beschlusshinweis',0)), ('Abstimmungshinweise', aus.get('tops_mit_abstimmungshinweis',0))])
    index_body = f'<p class="eyebrow">ALLGÄU · ÖFFENTLICHE RATSUNTERLAGEN</p><h1>Ratsakten Oberstaufen</h1><p>Eine lokale, nachprüfbare Auswertung öffentlicher Ratsunterlagen des Marktes Oberstaufen.</p><div class="stats">{cards}</div><div class="cards"><a class="card" href="termine.html"><h3>Termine</h3><p>Alle Sitzungen mit öffentlichen Tagesordnungspunkten.</p></a><a class="card" href="suche.html"><h3>Suche</h3><p>Durchsuchbarer Index aller TOP, Berichte und Hinweise.</p></a><a class="card" href="themen/index.html"><h3>Themen</h3><p>Wiederkehrende Vorgänge im Zeitverlauf.</p></a><a class="card" href="ausgaben/index.html"><h3>Ausgaben</h3><p>Wochenarchiv der Aktenlage.</p></a><a class="card" href="befunde.html"><h3>Befunde</h3><p>Was die Datenlage hergibt und wo Grenzen bleiben.</p></a><a class="card" href="gremien.html"><h3>Gremien</h3><p>Entscheidungskörper und erfasste Aktivität.</p></a></div><p class="note">Beschluss- und Abstimmungshinweise sind maschinell aus amtlichen PDF-Texten abgeleitet und am Original zu prüfen.</p>'
    (docs/'index.html').write_text(page('Ratsakten Oberstaufen', index_body), encoding='utf-8')

    by_meeting = collections.defaultdict(list)
    for t in data['tagesordnungspunkte']: by_meeting[t['sitzung_id']].append(t)
    term_body = '<h1>Termine</h1><p>Alle erfassten Sitzungstermine aus dem öffentlichen RIS.</p>'
    for m in sorted(data['sitzungen'], key=lambda x:x['datum'], reverse=True):
        term_body += f'<details><summary>{html.escape(m["datum"])} · {html.escape(m["gremium"])} <small>{m["n_tops"]} TOP</small></summary>'
        if m.get('berichte'): term_body += '<p>' + ', '.join(f'<a href="{html.escape(b["url"],quote=True)}">{html.escape(b["titel"])}</a>' for b in m['berichte']) + '</p>'
        term_body += '<ol>' + ''.join(f'<li><b>TOP {html.escape(t["top"])}</b> {html.escape(clean_text(t["titel"]))}</li>' for t in by_meeting[m['id']]) + '</ol></details>'
    (docs/'termine.html').write_text(page('Termine · Ratsakten Oberstaufen', term_body), encoding='utf-8')

    grem = collections.defaultdict(lambda:{'sitzungen':0,'tops':0,'berichte':0,'beschluss':0})
    for m in data['sitzungen']:
        g=grem[m['gremium']]; g['sitzungen']+=1; g['tops']+=m['n_tops']; g['berichte']+=len(m.get('berichte',[]))
    for t in data['tagesordnungspunkte']:
        if t.get('beschlusshinweis'): grem[t['gremium']]['beschluss']+=1
    rows=''.join(f'<tr><td>{html.escape(k)}</td><td>{v["sitzungen"]}</td><td>{v["tops"]}</td><td>{v["berichte"]}</td><td>{v["beschluss"]}</td></tr>' for k,v in sorted(grem.items()))
    (docs/'gremien.html').write_text(page('Gremien · Ratsakten Oberstaufen', f'<h1>Gremien</h1><table><tr><th>Gremium</th><th>Sitzungen</th><th>TOP</th><th>Berichte</th><th>Beschlusshinweise</th></tr>{rows}</table>'), encoding='utf-8')

    search_rows = [{'datum':t['datum'],'gremium':t['gremium'],'top':t['top'],'titel':clean_text(t['titel']),'hinweis':t.get('beschlusshinweis') or '', 'abstimmung':t.get('abstimmungshinweis') or ''} for t in data['tagesordnungspunkte']]
    search_json = json.dumps(search_rows, ensure_ascii=False)
    search_body = f'<h1>Suche</h1><input id="q" placeholder="Suchbegriff eingeben"><div id="out"></div><script>const data={search_json};const q=document.getElementById("q"),out=document.getElementById("out");function draw(){{let s=q.value.toLowerCase();let rows=data.filter(x=>!s||Object.values(x).join(" ").toLowerCase().includes(s)).slice(0,250);out.innerHTML="<p>"+rows.length+" Treffer angezeigt</p>"+rows.map(x=>`<div class=row><b>${{x.datum}} · ${{x.gremium}} · TOP ${{x.top}}</b><br>${{x.titel}}${{x.hinweis?`<blockquote>${{x.hinweis}}</blockquote>`:""}}${{x.abstimmung?`<small>Abstimmung: ${{x.abstimmung}}</small>`:""}}</div>`).join("")}}q.addEventListener("input",draw);draw();</script>'
    (docs/'suche.html').write_text(page('Suche · Ratsakten Oberstaufen', search_body), encoding='utf-8')

    topics = collect_topics(data)[:60]
    topic_cards = []
    for topic in topics:
        fname = slug(topic['key']) + '.html'
        topic_cards.append(f'<a class="card" href="{fname}"><h3>{html.escape(topic["titel"][:110])}</h3><p>{len(topic["items"])} Stationen · {topic["items"][0]["datum"]} bis {topic["items"][-1]["datum"]}</p></a>')
        rows = ''.join(f'<div class="row"><b>{html.escape(i["datum"])} · {html.escape(i["gremium"])} · TOP {html.escape(i["top"])}</b><p>{html.escape(clean_text(i["titel"]))}</p>' + (f'<blockquote>{html.escape(i["beschlusshinweis"])}</blockquote>' if i.get('beschlusshinweis') else '') + '</div>' for i in topic['items'])
        nav = '<nav><a href="../index.html">Start</a><a href="index.html">Themen</a><a href="../suche.html">Suche</a></nav>'
        (docs/'themen'/fname).write_text(page(topic['titel'], f'<h1>{html.escape(topic["titel"])}</h1>{rows}', nav), encoding='utf-8')
    (docs/'themen/index.html').write_text(page('Themen · Ratsakten Oberstaufen', f'<h1>Themen</h1><p>Wiederkehrende oder mit Beschlusshinweisen belegte Vorgänge.</p><div class="cards">{"".join(topic_cards)}</div>', '<nav><a href="../index.html">Start</a><a href="../suche.html">Suche</a><a href="../termine.html">Termine</a></nav>'), encoding='utf-8')

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
        (folder/fname).write_text(page(f'Aktenlage KW {w}/{y}', content, '<nav><a href="../../index.html">Start</a><a href="../index.html">Ausgaben</a></nav>'), encoding='utf-8')
    arch_rows = ''.join(f'<tr><td><a href="{p}">KW {w}/{y}</a></td><td>{n}</td><td>{tops}</td></tr>' for y,w,p,n,tops in archive)
    (docs/'ausgaben/index.html').write_text(page('Ausgaben · Ratsakten Oberstaufen', f'<h1>Ausgaben</h1><table><tr><th>Ausgabe</th><th>Sitzungen</th><th>TOP</th></tr>{arch_rows}</table>', '<nav><a href="../index.html">Start</a><a href="../termine.html">Termine</a></nav>'), encoding='utf-8')

    bef = f'<h1>Befunde</h1><div class="stats">{cards}</div><div class="note"><b>Datenlage:</b> Es gibt amtliche Sitzungsberichte vor allem für den Marktgemeinderat. Ausschüsse und Sitzungen ohne Bericht bleiben im RIS sichtbar, aber ohne belastbaren Beschlussauszug.</div><div class="note"><b>Grenze:</b> Eine maschinelle Zuordnung ersetzt keine Prüfung im Original-PDF. Fehlende Abstimmungshinweise bedeuten nicht, dass keine Abstimmung stattfand.</div>'
    (docs/'befunde.html').write_text(page('Befunde · Ratsakten Oberstaufen', bef), encoding='utf-8')


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
    build_extra_pages(data)

if __name__=='__main__':main()
