# Ratsakten Oberstaufen

Öffentliche Tagesordnungen des Marktes Oberstaufen im Allgäu, ergänzt um Links zu amtlichen Sitzungsberichten, Beschlusshinweise, Abstimmungshinweise und statische Übersichtsseiten. Technische Ableitung aus [Ratsakten Bad Waldsee – AmannLabs.eu](https://github.com/dominikamann/ratsakten-bad-waldsee).

## Stand und Grenzen

Erweiterter Datenstand ab 1. Januar 2024. Die Seiten unter `docs/` zeigen den importierten Zeitraum und die tatsächlich erfassten Kennzahlen. Ein Kalendereintrag beweist nicht, dass eine Sitzung stattgefunden hat. Tagesordnungstitel belegen keine Beschlussergebnisse; Beschluss- und Abstimmungshinweise erscheinen nur, wenn ein maschinell lesbarer amtlicher PDF-Bericht ausreichend sicher zugeordnet werden konnte. Die zusätzlichen amtlichen Berichte umfassen auch andere Jahre und Versammlungen.

Die Bad-Waldsee-Daten und Skripte bleiben unverändert unter `referenz_bad_waldsee/` als technische Referenz erhalten. Sie fließen nicht in die Oberstaufen-Ausgabe ein. Deren früherer Wochenlauf ist gemeindespezifisch und darf nicht für Oberstaufen verwendet werden.

## Lokal öffnen und aktualisieren

`docs/index.html` im Browser öffnen. Benötigt für Aktualisierungen: Python 3.9 oder neuer, Internetzugang. Keine Zusatzpakete.

```sh
./scripts/wochenlauf.sh
# Nur aus vorhandenen Daten neu bauen:
./scripts/wochenlauf.sh --offline
# Vorhandene Daten zusätzlich mit Berichts-/Beschlusshinweisen anreichern:
python3 scripts/oberstaufen.py --offline --auswerten
# Abweichender Zeitraum:
./scripts/wochenlauf.sh --von 2025-01-01 --bis 2026-09-15
```

Der Lauf aktualisiert lokale Dateien. GitHub Desktop zeigt die Änderungen zur Übernahme und zum Push an. Keine automatische Veröffentlichung oder Terminplanung eingerichtet.

## Quellen und Methode

- [Amtlicher Einstieg in die Ratsinfo](https://www.bsp-oberstaufen.info/kontakt-info/ratsinfo)
- [Ratsinformationssystem Markt Oberstaufen](https://ris.komuna.net/oberstaufen/Home.mvc)
- [Amtliche Sitzungsberichte und Anlagen](https://www.oberstaufen.info/rathaus-buergerservice/rathaus-aktuell/berichte-aus-sitzungen-versammlungen)

Das Portal verwendet die öffentlich ausgelieferte Komuna-Gast-API. Der Import prüft die Gemeindeidentität und Mandant 32. Ausschließlich explizit öffentliche Sitzungsteile und nicht gesperrte Tagesordnungspunkte werden übernommen. Zwischen Aufrufen liegen 0,5 Sekunden Pause. Ein gescheiterter Abruf überschreibt den letzten Datenbestand nicht. Die API ist eine interne Anwendungsschnittstelle und kann sich ändern.

Die Berichtszuordnung nutzt Datum, Gremium und Themenwörter aus TOP-Titeln und PDF-Texten. Ein verknüpfter Sitzungsbericht ist ein Quellenhinweis zur Sitzung; ein konkreter Beschlusshinweis wird nur angezeigt, wenn der Text zusätzlich zum TOP passt. Abstimmungshinweise sind reine Texterkennungen, zum Beispiel Zahlenverhältnisse oder Wörter wie „einstimmig“, und ersetzen keine juristische Prüfung.

## Struktur

- `data/oberstaufen.json`: Quellenkennungen, Abrufzeit, Termine, öffentliche TOP, Berichtslinks, Beschlusshinweise und Abstimmungshinweise.
- `docs/index.html`: Startseite mit Einstiegen.
- `docs/termine.html`: alle Sitzungen mit öffentlichen Tagesordnungspunkten.
- `docs/suche.html`: durchsuchbarer Index aller TOP, Hinweise und Abstimmungen.
- `docs/gremien.html`: Aktivität nach Gremium.
- `docs/befunde.html`: Datenlage, Abdeckung und Grenzen.
- `docs/themen/`: wiederkehrende Vorgänge im Zeitverlauf.
- `docs/ausgaben/`: Wochenarchiv der Aktenlage.
- `data/csv/`: Tabellen zum Prüfen und Weiterverarbeiten.
- `scripts/oberstaufen.py`: neuer Oberstaufen-Import und Berichtserzeugung.
- `scripts/pruefen.py`, `tests/`: Datenkonsistenz und Schutz gegen Gemeindeverwechslungen/geschützte Tagesordnungsteile.
- `referenz_bad_waldsee/`: unveränderte Referenz, keine Oberstaufen-Ergebnisse.

## Herkunft und Lizenzen

Code und Skripte der Vorlage: MIT, Copyright AmannLabs.eu. Übernommene Texte und Tabellen der Referenz: CC BY 4.0. Siehe `LICENSE`; Herkunft und Bearbeitung sind hier kenntlich gemacht. Die Lizenzbehauptungen der ursprünglichen Vorlage über Bad-Waldsee-Unterlagen werden nicht auf Oberstaufener Verwaltungsunterlagen übertragen. Für Originalunterlagen gelten die jeweiligen Rechte ihrer Herausgeber.

Privates Lernprojekt. Verbindlich sind ausschließlich die amtlichen Originalunterlagen.
