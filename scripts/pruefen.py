import json
from pathlib import Path
r=Path(__file__).resolve().parents[1];d=json.loads((r/'data/oberstaufen.json').read_text());page=(r/'docs/index.html').read_text()
assert d['kommune']=='Markt Oberstaufen'
assert d['sitzungen'] and d['tagesordnungspunkte']
ids={m['id'] for m in d['sitzungen']};assert len(ids)==len(d['sitzungen'])
assert len({t['id'] for t in d['tagesordnungspunkte']})==len(d['tagesordnungspunkte'])
assert all(t['sitzung_id'] in ids for t in d['tagesordnungspunkte'])
assert all(d['von']<=m['datum']<=d['bis'] for m in d['sitzungen'])
assert sum(m['n_tops'] for m in d['sitzungen'])==len(d['tagesordnungspunkte'])
assert 'Ratsakten Oberstaufen' in page and 'Erweiterter Datenstand' in page
if 'auswertung' in d:
    assert d['auswertung']['tops_mit_berichtslink'] <= len(d['tagesordnungspunkte'])
    assert d['auswertung']['tops_mit_abstimmungshinweis'] <= d['auswertung']['tops_mit_beschlusshinweis']
print(f"Geprüft: {len(ids)} Termine, {len(d['tagesordnungspunkte'])} TOP, {len(d['berichte'])} Berichte/Anlagen")
