# OP Hunter Dashboard V3

## Neue Funktionen

- professionelle Seitenleiste und responsive Oberfläche
- ausschließlich echte Kennzahlen aus `results.json`
- kompakte Produkttabelle und optionale Kartenansicht
- Filter nach Status, Produkttyp, Release-Zeitraum, Preis und Watchlist
- bestätigte Angebote und Vorbestellungen
- Watchlist im Browser
- Release-Kalender
- Aktivitätsfeed für neue Angebote und Preisänderungen
- Preisverlauf aus mehreren Scans
- Shop-Radar und transparenter Systemstatus
- keine Demo-Alerts und keine fest codierte Verfügbarkeitszahl

## Upload in das bestehende Repository

Den vollständigen Inhalt dieses Pakets in das Repository hochladen und vorhandene
Dateien ersetzen. Wichtig: Die Workflow-Datei lautet:

`.github/workflows/main.yml`

Falls zusätzlich noch `.github/workflows/op-hunter.yml` existiert, diese alte
Workflow-Datei löschen, damit nur ein Workflow aktiv ist.

Nach dem Commit wird die Oberfläche automatisch veröffentlicht. Anschließend unter
Actions einmal `OP Hunter Scan & Deploy` manuell starten, damit Aktivitäts- und
Preisverlaufsdateien erstmals erzeugt werden.
