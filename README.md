# OP Hunter – GitHub Pages

Browserbasiertes One Piece TCG Dashboard mit automatischer Shopprüfung über GitHub Actions.

## Enthalten

- `index.html` – Dashboard
- `scanner.py` – Cloud-Scanner
- `data/shops.json` – 50 Händler aus der Excel-Datei
- `data/products.json` – Produktkatalog
- `data/results.json` – Ergebnisse des letzten Scans
- `.github/workflows/op-hunter.yml` – Zeitplan und Veröffentlichung

## Zeitplan

Der Workflow wird um 04:00 und 05:00 UTC angestoßen. Das Python-Skript führt nur den Lauf
aus, der 06:00 Uhr in der Zeitzone Europe/Berlin entspricht. Damit werden Sommer- und
Winterzeit berücksichtigt.

## Wichtige Einschränkung

Einige Shops blockieren automatisierte Zugriffe durch Captchas, Cloudflare, Login-Pflicht
oder andere Schutzmaßnahmen. Diese Shops können nicht zuverlässig ausgewertet werden.
Das Dashboard zeigt nur positiv erkannte Bestellungen und Vorbestellungen.


## Automatische Neuerscheinungen

Vor jedem geplanten oder manuellen Scan lädt `update_products.py` die offizielle
ONE PIECE CARD GAME Produktübersicht und Produktmeldungen. Neue offizielle
Produktseiten werden automatisch in `data/products.json` aufgenommen. Danach
prüft `scanner.py` alle Katalogprodukte bei den 50 Händlern.

Es werden ausschließlich direkte Produktdetailseiten mit eindeutigem
Bestell- oder Vorbestellstatus veröffentlicht. Suchseiten und ausverkaufte
Produkte werden nicht als Angebot gespeichert.
