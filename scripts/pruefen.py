"""Prüft Datenbestand und erzeugte Seiten, bevor etwas veröffentlicht wird."""
import json
import re
from pathlib import Path

r = Path(__file__).resolve().parents[1]
d = json.loads((r / 'data/oberstaufen.json').read_text())
docs = r / 'docs'

# --- Daten -----------------------------------------------------------------
assert d['kommune'] == 'Markt Oberstaufen'
assert d['sitzungen'] and d['tagesordnungspunkte']
ids = {m['id'] for m in d['sitzungen']}
assert len(ids) == len(d['sitzungen'])
assert len({t['id'] for t in d['tagesordnungspunkte']}) == len(d['tagesordnungspunkte'])
assert all(t['sitzung_id'] in ids for t in d['tagesordnungspunkte'])
assert all(d['von'] <= m['datum'] <= d['bis'] for m in d['sitzungen'])
assert sum(m['n_tops'] for m in d['sitzungen']) == len(d['tagesordnungspunkte'])
if 'auswertung' in d:
    assert d['auswertung']['tops_mit_berichtslink'] <= len(d['tagesordnungspunkte'])
    assert d['auswertung']['tops_mit_abstimmungshinweis'] <= d['auswertung']['tops_mit_beschlusshinweis']

# --- Navigation: auf jeder Seite dieselbe Leiste ---------------------------
BEREICHE = ['Startseite', 'Termine', 'Themen', 'Suche', 'Erkenntnisse', 'Archiv', 'Wer entscheidet was']
seiten = sorted(docs.rglob('*.html'))
assert seiten, 'Keine Seiten erzeugt'
for seite in seiten:
    text = seite.read_text(encoding='utf-8')
    leiste = re.search(r'<nav aria-label="Bereiche">(.*?)</nav>', text, re.S)
    assert leiste, f'{seite.relative_to(docs)}: keine Navigationsleiste'
    benennungen = re.findall(r'<a href="[^"]*"[^>]*>([^<]+)</a>', leiste.group(1))
    assert benennungen == BEREICHE, f'{seite.relative_to(docs)}: abweichende Leiste {benennungen}'
    # Relative Ziele müssen von dieser Seite aus existieren
    for ziel in re.findall(r'<a href="([^":#]+\.html)"', leiste.group(1)):
        assert (seite.parent / ziel).resolve().exists(), f'{seite.relative_to(docs)}: {ziel} fehlt'

# --- Pflichtseiten ---------------------------------------------------------
start = (docs / 'index.html').read_text(encoding='utf-8')
assert 'Ratsakten Oberstaufen' in start
for bereich in BEREICHE:
    assert bereich in start, f'Startseite nennt {bereich} nicht'

berichte = sorted((docs / 'report').glob('*.html'))
assert len(berichte) == 1, f'Genau ein Report erwartet, gefunden: {len(berichte)}'
report = berichte[0].read_text(encoding='utf-8')
assert 'Ratsanalyse Oberstaufen' in report
assert f'report/{berichte[0].name}' in start, 'Startseite verlinkt den Report nicht'
for kapitel in ['Woher die Daten stammen', 'Wo Beschlüsse nachlesbar sind',
                'Was dieser Report nicht kann', 'Haftungsausschluss']:
    assert kapitel in report, f'Report ohne Kapitel: {kapitel}'
assert 'Bad Waldsee' not in report and 'Bad Waldsee' not in start, 'Rest aus der Vorlage'

befunde = (docs / 'befunde.html').read_text(encoding='utf-8')
assert '<h1>Erkenntnisse</h1>' in befunde
assert befunde.count('<div class="row">') >= 8, 'Erkenntnisseite ist zu dünn'
assert f'report/{berichte[0].name}' in befunde, 'Erkenntnisse verlinken den Report nicht'

# --- Zahlen im Report stimmen mit den Daten überein -------------------------
assert f'<b>{len(d["sitzungen"])}</b>' in report
assert f'<b>{len(d["tagesordnungspunkte"])}</b>' in report

# --- Personenschutz: keine Personennennung in der Ausgabe -------------------
import importlib.util
spec = importlib.util.spec_from_file_location('gen', r / 'scripts/oberstaufen.py')
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

# Nicht gegen die bereits bereinigten Daten pruefen — das pruefte sich selbst.
# Stattdessen die Erkennungsmuster erneut auf alles anwenden, was ausgeliefert
# wird. Findet ein Muster dort noch eine Rolle mit folgendem Namen, eine
# Vertretungsklammer oder eine Fraktionszuordnung, ist die Schwaerzung undicht.
MUSTER = [('Rolle mit Namen', gen.PERSON), ('Vertretungsklammer', gen.VERTRETUNG),
          ('Fraktion mit Namen', gen.FRAKTION), ('Name mit Fraktion', gen.NAME_PARTEI)]

veroeffentlicht = [(seite.relative_to(r), seite.read_text(encoding='utf-8')) for seite in seiten]
veroeffentlicht.append((Path('data/oberstaufen.json'), (r / 'data/oberstaufen.json').read_text(encoding='utf-8')))
for tabelle in sorted((r / 'data/csv').glob('*.csv')):
    veroeffentlicht.append((tabelle.relative_to(r), tabelle.read_text(encoding='utf-8-sig')))

treffer = []
for pfad, text in veroeffentlicht:
    for bezeichnung, muster in MUSTER:
        for fund in muster.finditer(text):
            treffer.append(f'{pfad}: {bezeichnung} — {fund.group(0)[:60]!r}')
assert not treffer, 'Personenbezug in der Ausgabe:\n  ' + '\n  '.join(treffer[:8])

zurueck = sum(1 for t in d['tagesordnungspunkte'] if t.get('beschlusshinweis_zurueckgehalten'))
assert zurueck == d['auswertung'].get('tops_mit_zurueckgehaltenem_hinweis')
assert not any(t.get('beschlusshinweis') and t.get('beschlusshinweis_zurueckgehalten')
               for t in d['tagesordnungspunkte']), 'Auszug trotz Zurueckhaltung gespeichert'

print(f"Personenschutz: {zurueck} Auszüge zurückgehalten, "
      f"kein Personenbezug in {len(veroeffentlicht)} ausgelieferten Dateien")

print(f"Geprüft: {len(ids)} Termine, {len(d['tagesordnungspunkte'])} TOP, "
      f"{len(d['berichte'])} Berichte/Anlagen, {len(seiten)} Seiten, Report {berichte[0].name}")
