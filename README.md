# FlipFinder Limburg V3 — Live-ready architectuur

Deze versie bevat een echte kleine backend en database-opzet naast de iPhone/PWA-interface.

## Wat werkt al
- Flask backend
- SQLite database
- API endpoint voor woningen
- demo-import
- filters op plaats, vraagprijs, winst en ROI
- meldingsregels opslaan
- rekenmodel in de iPhone-interface
- statusscherm voor koppelingen
- aparte adapters voor woningfeed, Kadaster en pushlogica

## Starten op computer/server
1. Installeer Python 3.11+
2. `pip install -r requirements.txt`
3. `python server/app.py`
4. Open `http://localhost:8000`

## iPhone
Voor iPhone moet deze backend op een HTTPS-host staan, bijvoorbeeld Render, Railway, Fly.io of een eigen VPS.
Open daarna de HTTPS-url in Safari en kies: Deel > Zet op beginscherm.

## Nodig voor echte live data
- officiële/toegestane woningfeed of API
- eventueel Kadaster API-toegang
- productiehosting met HTTPS
- pushprovider (Web Push/APNs)

## Veiligheid
Zet API-keys alleen op de server in environment variables. Nooit in `public/index.html`.

## Belangrijk
De meegeleverde woningen zijn demo-data. De live koppellaag is voorbereid maar nog niet verbonden met een externe betaalde/geautoriseerde feed.
