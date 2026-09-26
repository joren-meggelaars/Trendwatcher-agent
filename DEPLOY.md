# Deployment op de Ubuntu 24.04 Docker-VM

Alle drie de componenten (Postgres, scoring-service + admin-GUI, scheduler)
draaien als containers via `docker compose`. Docker/Docker Compose worden
door jou op de VM geïnstalleerd — dit document begint bij een schone VM met
een werkende `docker`/`docker compose`-installatie.

## 1. Clonen en configureren

```bash
git clone <repo-url> trendwatcher-agent
cd trendwatcher-agent

cp -n .env.example .env
nano .env   # of vim/etc.
```

Vul in `.env` minimaal in:
- `BIND_ADDRESS` — het interne (LAN-)IP-adres van deze VM.
- `POSTGRES_PASSWORD` — een eigen sterk wachtwoord (niet de placeholder laten staan).
- `VOYAGE_API_KEY`
- `GRAPH_TENANT_ID` / `GRAPH_CLIENT_ID` / `GRAPH_CLIENT_SECRET` / `DIGEST_MAILBOX` / `DIGEST_TO_EMAIL`
- `ADMIN_USERNAME` en `ADMIN_PASSWORD_HASH` (zie stap 3 hieronder — de hash genereer je pas ná de build)
- `SESSION_SECRET_KEY` (zie stap 3)

`.env` wordt **nooit** vanuit git gehaald — dit bestand bestaat alleen lokaal
op de VM, met de echte secrets. `git status` mag dit bestand nooit als
wijziging tonen (staat in `.gitignore` op elk niveau).

## 2. Builden

```bash
docker compose build
```

Bouwt alle drie de services (`postgres` is een kant-en-klare image, wordt
alleen gepulld). Dit slaagt zonder errors als acceptatiecriterium.

## 3. Admin-wachtwoord en sessie-sleutel genereren

Dit kan pas ná `docker compose build`, want het draait via de zojuist
gebouwde scoring-service-image (die de juiste bcrypt-library al aan boord heeft):

```bash
docker compose run --rm scoring-service python -m scripts.hash_admin_password
docker compose run --rm scoring-service python -c "import secrets; print(secrets.token_hex(32))"
```

Zet beide outputs in `.env` als `ADMIN_PASSWORD_HASH` en `SESSION_SECRET_KEY`.

**Let op**: verdubbel elke `$` in de bcrypt-hash naar `$$` (bijv. `$2b$12$...`
wordt `$$2b$$12$$...`). Docker Compose interpreteert `$naam` in `.env`-
waarden zelf als een variabele-verwijzing; zonder deze escaping wordt de
hash stilzwijgend kapotgemaakt (`WARN ... variable is not set`) en kun je
niet inloggen op de admin-GUI.

## 4. Starten

```bash
docker compose up -d
```

Start alle vier de containers:
- `postgres`: de database;
- `scoring-service`: de interne JSON-API (scoren, bronnen, digests, ...), **zonder login en daarom niet gepubliceerd**: alleen bereikbaar voor de andere containers;
- `web`: de admin-GUI, **het enige dat naar buiten gaat** (poort 8000 op `BIND_ADDRESS`). Bevat geen enkele API-route;
- `scheduler`: de achtergrondjobs.

`scoring-service` wacht op Postgres' healthcheck (niet alleen op "container
bestaat"); `web` en `scheduler` wachten op de gezondheidscheck van
`scoring-service`.

## 5. Initiële bronnenlijst seeden

Eenmalig, direct na de eerste start — de Postgres-database begint leeg
(schone start, geen data-migratie vanuit de oude SQLite-instantie):

```bash
docker compose run --rm scoring-service python -m scripts.seed_sources
```

Voegt de vaste bronnenlijst toe (zie `scripts/seed_sources.py`), met per bron
een categorie en een status:

- de oorspronkelijke 20 bronnen als `"kandidaat"`;
- bronnen waarvan de feed is gevalideerd (HTTP 200, parseerbaar, ≥1 item) als
  `"actief"` — die pakt de scheduler meteen op;
- bronnen zonder werkende feed als `"gedeactiveerd"`, met de reden in de
  notitie (zichtbaar in `/admin/sources`).

Idempotent, sleutel is de feed-URL. Een bron die al bestaat blijft zoals hij
is: status, type en notities worden **nooit** aangepast; alleen een nog lege
categorie wordt ingevuld. De uitvoer vermeldt precies wat is toegevoegd en wat
is aangeraakt. Ook op een bestaande database (bijv. de VM, waar de tabel
`sources` de kolommen `category` en `notes` nog niet heeft) is het script — en
de scoring-service bij het opstarten — veilig: ontbrekende nullable kolommen
worden automatisch toegevoegd (`app/migrations.py`, SQLite en PostgreSQL).

**Na het seeden van nieuwe actieve bronnen:** de scheduler haalt en scoort ze
op de achtergrond (`ingest`, elke `INGEST_INTERVAL_MINUTES`, standaard 30; de
eerste run één interval na de start van de scheduler). Per bron worden
maximaal `MAX_NEW_ENTRIES_PER_SOURCE` (standaard 10) van de nieuwste items
gescoord en de oudere achterstand wordt als gezien gemarkeerd. De digest om
`DIGEST_HOUR` scoort niets meer en mailt wat er dan al gescoord is, dus hij
wacht nooit op de scoring. Bij de Voyage-gratis-tier
(`SCORE_REQUEST_DELAY_SECONDS=21`) duurt het scoren van ~25 nieuwe bronnen
ruim een uur op de achtergrond; met een betaalmethode
(`SCORE_REQUEST_DELAY_SECONDS=0`) is het in minuten klaar.

### Tijdzone

De containers draaien op UTC. Met `TIMEZONE` (standaard `Europe/Amsterdam`) toont
de GUI alle tijden lokaal, staat de datum in de mail op de lokale dag, en
betekent `DIGEST_HOUR=7` (of het uur op `/admin/settings`) 07:00 lokaal. De logs
van de scheduler blijven UTC. Na het wijzigen: `docker compose up -d`.

### Instellingen achteraf aanpassen

Veel waarden uit `.env` (scorepauze, ingest-interval, dry-run, discovery, drempels
voor bronnen, ...) kun je daarna in de admin-GUI aanpassen op
`http://<BIND_ADDRESS>:8000/admin/config`, zonder herstart. `.env` blijft de
basis: een leeg veld in de GUI volgt weer `.env`. Geheimen en instellingen die
bepalen waar mail heen gaat (API-sleutels, Graph-gegevens, admin-wachtwoord,
`DIGEST_MAILBOX`, `DIGEST_TO_EMAIL`, `FEEDBACK_BASE_URL`, ...) blijven bewust
alleen via `.env` op de VM te wijzigen (daarna `docker compose up -d`).

## 6. Controleren dat alles draait

```bash
docker compose ps
```

De services `postgres`, `scoring-service` en `web` moeten `healthy` tonen (de
scheduler heeft geen healthcheck; een lopende `Up`-status is daar voldoende,
want het proces is een simpele achtergrond-loop).

```bash
docker compose logs -f web
docker compose logs -f scoring-service
docker compose logs -f scheduler
```

Snelle rooktest vanaf de VM zelf. De GUI (`web`) staat op poort 8000; de API
(`scoring-service`) is bewust niet van buiten de containers bereikbaar, dus die
test je vanuit de container:

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}      <- de GUI

docker compose exec scoring-service python -c "
import json, urllib.request
req = urllib.request.Request('http://localhost:8000/score', method='POST',
    headers={'Content-Type': 'application/json'},
    data=json.dumps({'source':'test','title':'T','url':'https://example.com','raw_content':'Test artikel over een kwetsbaarheid.'}).encode())
print(urllib.request.urlopen(req).read().decode())"
```

De admin-GUI is bereikbaar op `http://<BIND_ADDRESS>:8000/admin/login` vanaf
elk toestel binnen hetzelfde netwerk. Voor HTTPS en toegang van buitenaf: zie
"Bereikbaar van buiten (HTTPS)" hieronder.

## Bereikbaar van buiten (HTTPS)

De knoppen in de digest-mail openen de GUI. Wil je die ook buiten je netwerk
gebruiken (telefoon zonder VPN), zet dan een reverse proxy met HTTPS voor de
`web`-container. Wat daarvoor nodig is:

1. **Alleen `web` publiceren.** Laat de proxy alleen naar poort 8000 van de
   `web`-container wijzen. De `scoring-service` (de API) heeft geen login en
   staat daarom niet op een host-poort; publiceer die nooit.
2. **`.env` op de VM** (daarna `docker compose up -d`):
   ```
   FEEDBACK_BASE_URL=https://trend.voorbeeld.nl   # zonder slash; hier wijzen de mailknoppen heen
   SESSION_COOKIE_SECURE=true                     # sessie-cookie alleen over HTTPS
   ```
   Let op: met `SESSION_COOKIE_SECURE=true` werkt inloggen alleen nog via het
   https-adres, niet meer via `http://<BIND_ADDRESS>:8000`.
3. **De proxy moet doorgeven:** de `Host`-header, en `X-Forwarded-For` waarbij
   hij het adres van de bezoeker *achteraan toevoegt* (nginx:
   `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`). De rem op
   verkeerde wachtwoorden (5 pogingen per adres, dan 15 minuten geblokkeerd)
   werkt op dat laatste adres. HSTS zet je op de proxy.
4. **Sterk wachtwoord.** De GUI heeft één account. Er zit een rem op raden, een
   Content-Security-Policy, bescherming tegen cross-site posts en een
   sessieduur van 12 uur (of 30 dagen met "onthoud mij"), maar een zwak
   wachtwoord blijft de zwakste schakel.

Voorbeeld voor nginx:

```nginx
location / {
    proxy_pass http://10.0.100.8:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    add_header Strict-Transport-Security "max-age=31536000" always;
}
```

Zonder `FEEDBACK_BASE_URL` gebruiken de mailknoppen `http://<BIND_ADDRESS>:8000`,
dus alleen bruikbaar in je eigen netwerk.

## Via Tailscale

Tailscale draait al op de VM (`srv-doc-01`, ook voor de Grocery-agent). De GUI
kan daar met Tailscale Serve bij: HTTPS met een echt certificaat, alleen
bereikbaar voor apparaten in je tailnet, zonder iets naar het internet te
openen. Dat kan naast de NPM-route (`https://ta.krijgfeestelijkdetering.nl`)
bestaan, of die vervangen.

Vooraf, eenmalig in de Tailscale-adminconsole: MagicDNS en HTTPS-certificaten
staan aan (de Grocery-agent gebruikt dat al, dus dat is in orde). Je telefoon en
pc hebben de Tailscale-app nodig en mogen de VM bereiken (ACL).

1. **Nieuwe versie uitrollen.** De `web`-container publiceert nu ook
   `127.0.0.1:8002` (alleen de VM zelf kan daar bij):
   ```bash
   git pull && docker compose up -d
   curl -s http://127.0.0.1:8002/health        # {"status":"ok"}
   ```
2. **Serve aanzetten** op de VM. Kijk eerst wat er al staat en overschrijf dat niet:
   ```bash
   tailscale serve status                      # de Grocery-agent staat op :8443
   sudo tailscale serve --bg --https=443 http://127.0.0.1:8002
   tailscale serve status
   ```
   Is poort 443 al bezet, neem dan `--https=10000`. Uitzetten:
   `sudo tailscale serve --https=443 off`. (De syntaxis verschilt per versie;
   zie `tailscale serve --help`.)
3. **Openen:** `https://srv-doc-01.tail268074.ts.net/` (met `:10000` erachter
   als je die poort nam) en inloggen met je gewone account.
4. **Mailknoppen.** `FEEDBACK_BASE_URL` in `.env` is één adres. Kies:
   - blijft `https://ta.krijgfeestelijkdetering.nl`: de knoppen werken overal,
     ook zonder Tailscale;
   - wordt het tailnet-adres (zonder slash): de knoppen werken alleen op
     apparaten met Tailscale aan (dat geldt ook voor je werk-pc, als daar geen
     Tailscale op staat).

   Daarna `docker compose up -d`. Laat `SESSION_COOKIE_SECURE=true` staan: beide
   adressen zijn https. Inloggen werkt per adres apart (een cookie hoort bij één
   adres).
5. **Internet dicht?** Wil je de GUI niet meer publiek, verwijder dan de
   Proxy Host in NPM (en zet `FEEDBACK_BASE_URL` op het tailnet-adres).

Goed om te weten:

- De cross-site-controle vergelijkt `Origin` met de `Host`-header. Serve laat die
  ongemoeid (dat is bij de Grocery-agent gecontroleerd), dus formulieren werken
  zonder verdere instelling.
- De rem op verkeerde wachtwoorden (5 per adres) telt per adres uit
  `X-Forwarded-For`. Stuurt Serve dat niet mee, dan delen alle tailnet-bezoekers
  één teller (de Docker-gateway). Dat is veilig, alleen wordt iedereen samen
  15 minuten geblokkeerd na 5 foute pogingen. Controleer dit na de eerste
  keer inloggen met `docker compose logs web`.
- De server haalt zelf nooit iets op uit je tailnet: `100.64.0.0/10` (de
  Tailscale-adressen) en dus ook `*.ts.net` valt onder "intern" in
  "Veilig ophalen van bronnen".
- Alleen `web` is bereikbaar; de API (`scoring-service`) blijft intern.

## Inloggen via Authentik (optioneel)

Met de identity-platform-stack (Authentik) op dezelfde VM log je in met je
eigen account (passkey of authenticator-app) in plaats van het gedeelde
wachtwoord. Het wachtwoord blijft werken als noodtoegang als Authentik plat ligt.

1. Eenmalig op de host, als dat nog niet gebeurd is: `docker network create identity-apps`.
   De `web`-container hangt aan dat netwerk en bereikt Authentik als `http://authentik:9000`;
   zonder dat netwerk start de stack niet.
2. In `~/identity-platform/.env`: `echo "TRENDWATCHER_OIDC_SECRET=$(openssl rand -hex 32)" >> .env`,
   dan `git pull && docker compose up -d`. De blueprint `blueprints/trendwatcher.yaml` maakt
   de provider, de applicatie en de groep `trendwatcher-admin`.
3. In Authentik: zet jezelf (en wie verder mag) in de groep `trendwatcher-admin`.
4. In de `.env` van TrendWatcher het `OIDC_*`-blok invullen (zie `.env.example`):
   issuer `https://auth.<domein>/application/o/trendwatcher/`, hetzelfde secret, en elk
   adres waarop je de GUI opent eindigend op `/admin/oidc/callback` (die moeten ook in de
   blueprint staan). Dan `docker compose up -d --build web`.

Een half ingevuld blok stopt de `web`-container bij het starten met een melding welke
waarde ontbreekt (`docker compose logs web`). `OIDC_ISSUER` leeg = weer alleen het wachtwoord.
Een login via Authentik geldt `OIDC_SESSION_DAYS` dagen (standaard 7).

## Veilig ophalen van bronnen

Alles wat het systeem van internet haalt gaat via één functie (`safe_fetch.py`,
één kopie per project): de feeds die de scheduler leest, de pagina's achter
zoekresultaten, en de pagina's bij "Losse items toevoegen". Die functie:

- accepteert alleen `http://` en `https://` (geen `file:`, `ftp:`, `javascript:`
  enz.), geen adressen met gebruikersnaam/wachtwoord, en alleen de poorten
  80, 443, 8080 en 8443;
- weigert adressen die naar het eigen netwerk wijzen: localhost, `10.x`,
  `172.16–31.x`, `192.168.x`, `169.254.x` (cloud-metadata), `100.64.x`, IPv6-
  varianten daarvan, en namen die daarnaartoe wijzen. Dat geldt ook na elke
  redirect. De naam wordt één keer opgezocht en de verbinding gaat naar dat
  gecontroleerde adres, zodat een naam die na de controle iets anders gaat
  antwoorden niets oplevert;
- kapt af na 5 redirects, 30 seconden in totaal, en op een maximale grootte
  (na uitpakken; 10 MB voor feeds, 2 MB voor pagina's).

Bij het aanmaken van een bron (formulier, API, seed) worden dezelfde regels
toegepast op het adres zelf, en in de GUI worden links van feeds alleen
klikbaar als ze met `http(s)://` beginnen.

Wat dit **niet** doet: beoordelen of een site zelf kwaadaardig is. Wil je dat
ook, dan kan een DNS-filter op de VM of het netwerk (bijv. Quad9, `9.9.9.9`)
bekende malware-domeinen blokkeren zonder codewijziging.

Voor testen tegen een lokale server kun je in `.env` `SAFE_FETCH_ALLOW_PRIVATE=true`
zetten; dat laat interne adressen toe (het schema blijft beperkt tot http(s)).
Laat dit op de VM op `false`.

## 7. Geautomatiseerde tests draaien tegen de Postgres-container

De bestaande testsuite (scoring, feedback, sources, scheduler-support,
admin) draait standaard tegen een snelle in-memory SQLite-database. Om
dezelfde tests tegen de echte Postgres-container te draaien (installeert
tijdelijk de dev-dependencies, gebruikt dezelfde `DATABASE_URL` als de
draaiende stack):

```bash
docker compose run --rm scoring-service sh -c \
  "pip install --no-cache-dir uv --quiet && uv sync --frozen && uv run pytest -q"
```

Dit start een losse, tijdelijke container op hetzelfde `trendwatch-net`-netwerk,
dus `postgres` is bereikbaar. Alle tests moeten slagen — als dat zo is, is
het pgvector-embeddingpad ook daadwerkelijk doorlopen (niet alleen de
SQLite-JSON-fallback).

## 8. Data-persistentie verifiëren

```bash
# maak een bron aan (de API draait alleen binnen de containers)
docker compose exec scoring-service python -c "
import json, urllib.request
req = urllib.request.Request('http://localhost:8000/sources', method='POST',
    headers={'Content-Type': 'application/json'},
    data=json.dumps({'url':'https://example.com/feed','type':'rss'}).encode())
print(urllib.request.urlopen(req).read().decode())"

docker compose restart

docker compose exec scoring-service python -c "
import urllib.request
print(urllib.request.urlopen('http://localhost:8000/sources').read().decode()[:400])"
# de zojuist aangemaakte bron moet er nog steeds staan (of bekijk /admin/sources)
```

## Onderhoud

```bash
docker compose logs -f <service>     # logs volgen
docker compose down                  # stoppen (volume blijft bestaan)
docker compose down -v               # stoppen + Postgres-volume verwijderen (DATAVERLIES)
docker compose pull && docker compose up -d --build   # updaten na een git pull
```

`restart: unless-stopped` op alle drie de services zorgt dat ze na een
VM-reboot vanzelf weer opstarten zodra de Docker-daemon draait.

### Scheduler-cache ("al gescoord")

De scheduler onthoudt per bron welke feed-items al gescoord zijn in
`seen_items.json`. Die staat in het volume `trendwatch_scheduler_data`, zodat
een rebuild hem niet kwijtraakt (anders scoort de eerstvolgende run alles
opnieuw en krijg je duplicaten). Let op: `docker compose down -v` wist dit
volume óók.

De allereerste keer dat je dit volume introduceert is het leeg. De scoring-
service herkent al opgeslagen URL's en maakt dan geen duplicaat, maar de
scheduler wacht per scoring-aanroep nog wel `SCORE_REQUEST_DELAY_SECONDS`. Wil je dat
overslaan, kopieer dan vóór de update de oude cache uit de draaiende
container en zet hem er daarna in terug:

```bash
docker compose cp scheduler:/app/data/seen_items.json /tmp/seen_items.json   # vóór de update
# ... git pull && docker compose build && docker compose up -d ...
docker compose cp /tmp/seen_items.json scheduler:/app/data/seen_items.json
docker compose exec -u root scheduler chown app:app /app/data/seen_items.json
```

(Bestaat het bestand nog niet, dan geeft de eerste `cp` een foutmelding — dan
valt er niets over te zetten.)

### Items terugbrengen tot een startset

Items worden nergens automatisch opgeruimd. Voor een bewuste nieuwe start:
`scripts/trim_items.py` houdt N items over (standaard 50): eerst items met
feedback, dan marktontwikkeling, dan de nieuwste. Het draait **niet**
automatisch bij een build, en is standaard een dry-run.

```bash
# 1. back-up (verwijderen kan niet ongedaan gemaakt worden)
docker compose exec postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > ~/trendwatch-backup.sql

# 2. dry-run: toont wat blijft en wat weggaat, verwijdert niets
docker compose run --rm scoring-service python -m scripts.trim_items

# 3. echt verwijderen
docker compose run --rm scoring-service python -m scripts.trim_items --apply
```

Verwijderde items komen niet terug: de scheduler-cache markeert ze als al
gezien, dus alleen nieuwe feed-items komen erbij.
