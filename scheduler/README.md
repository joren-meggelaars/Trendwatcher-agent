# Scheduler

Eigen Python-scheduler die n8n vervangt voor het orkestreren van de
scoring-service. Praat uitsluitend via HTTP met de scoring-service (nooit
rechtstreeks met de database), zodat beide componenten later onafhankelijk
van elkaar naar Azure kunnen (Container App / Function).

## Setup

```bash
uv sync
cp .env.example .env
# vul minimaal SCORING_SERVICE_URL in; GRAPH_*/DIGEST_MAILBOX en SEARCH_*
# mogen leeg blijven om zonder e-mail/zoek-API te testen (zie hieronder) —
# DIGEST_DRY_RUN staat standaard op true
```

## Jobs

- **`jobs/daily_digest.py`** — haalt actieve bronnen op
  (`GET /sources?status=actief`), parst hun RSS-feed met `feedparser`, scoort
  nieuwe items via `POST /score` (met `source_id`), en verdeelt ze over twee
  categorieën (de scoring-service geeft elk item een `category`
  `"markt"`/`"nieuws"` terug, zie `app/classification.py`). Per categorie
  wordt de top-N op `relevance_score` gekozen, en de HTML-digest heeft twee
  secties: **Marktontwikkeling** (funding, overnames, marktcijfers) en
  **Nieuws**. Elke categorie wordt door de scoring-service apart gescoord op
  jouw 👍/👎 binnen díe categorie. Per item staan twee
  feedback-links (`GET /feedback-link?item_id=...&label=...`) via de
  Microsoft Graph `sendMail`-API (`POST /users/{DIGEST_MAILBOX}/sendMail`,
  zie `graph_client.py`). Met `DIGEST_DRY_RUN=true` (standaard) wordt de
  digest-HTML naar de console gelogd in plaats van verstuurd — zo te testen
  voordat de Graph-app-registratie klaarstaat.
- **`jobs/weekly_discovery.py`** — haalt recente `"interessant"`-items op
  (`GET /items/recent-feedback?label=interessant&days=...`), distilleert
  daaruit een paar zoektermen (woordfrequentie op titels, geen NLP nodig),
  zoekt daarmee via een `SearchProvider` naar kandidaat-content, registreert
  onbekende domeinen als kandidaat-bron (`discovery_method="market_sweep"`)
  en scoort de gevonden content. Sluit af met `POST /sources/evaluate-all`.
  **Zonder geconfigureerde `SearchProvider` slaat de zoekstap netjes over**
  (met een warning-log) — de rest van de job (ophalen, distilleren,
  evaluate-all) draait gewoon door.
- **`jobs/mailbox_ingest.py`** — voor bronnen zonder RSS-feed, alleen een
  nieuwsbrief (tl;dr sec, Risky Business News, SANS NewsBites). Leest
  ongelezen mail uit `DIGEST_MAILBOX` via Graph, maakt per afzender één
  `"mailbox"`-Source aan (`mailto:afzender@voorbeeld.com`, dedup op URL zoals
  elke andere bron), scoort elk bericht en markeert het daarna als gelezen
  (Graph's eigen `isRead`-vlag is de "al verwerkt"-status — geen aparte
  lokale seen-cache nodig). **Staat standaard uit** (`MAILBOX_INGEST_ENABLED=false`):
  vereist naast `Mail.Send` ook applicatiepermissie `Mail.Read` (admin
  consent) op de Graph-appregistratie.

Alle drie de jobs zijn los aan te roepen, zonder de scheduler-loop:

```bash
uv run python -m jobs.daily_digest
uv run python -m jobs.weekly_discovery
uv run python -m jobs.mailbox_ingest
```

## Digest-instellingen aanpasbaar via de admin-GUI

`DIGEST_HOUR` en `DIGEST_TOP_N` liggen niet meer alleen vast in `.env` — de
admin-GUI van de scoring-service (`/admin/settings`) kan ze via
`GET`/`PUT /settings/digest` aanpassen, opgeslagen in de Postgres-database:

- `digest_top_n` (per categorie: dus N marktontwikkeling én N nieuws) wordt
  bij elke `daily_digest`-run vers opgehaald
  (`remote_settings.fetch_digest_settings()`), dus een wijziging geldt vanaf
  de eerstvolgende run.
- `digest_hour` bepaalt de APScheduler-cron-trigger; een achtergrondtaak
  (`sync_digest_schedule`, elke `SETTINGS_SYNC_INTERVAL_SECONDS`, standaard 5
  minuten) checkt op wijzigingen en herplant de job — geen herstart nodig.
- De "verstuur nu"-knop in de admin-GUI doet een `POST` naar
  `trigger_server.py`'s interne `/trigger/digest-now`-endpoint (alleen
  bereikbaar binnen het Docker-netwerk, nooit naar de host/internet
  gepubliceerd). Die draait `daily_digest.run_now()`: mailt de beste al
  gescoorde items van de laatste `MANUAL_DIGEST_LOOKBACK_DAYS` dagen, per
  categorie de top-N (via `GET /items/top?category=markt` en `...=nieuws` op
  de scoring-service), **zonder feeds op te halen of te
  scoren** — dus geen wachttijd door Voyage's rate limit. Het geplande
  `/trigger/daily-digest` (volledige run: ophalen, scoren, mailen) blijft
  bestaan, maar staat niet achter een knop.

Als de scoring-service niet bereikbaar is, valt elke job terug op de
statische `DIGEST_HOUR`/`DIGEST_TOP_N`-waarden uit `.env`.

## Scheduler draaien

```bash
uv run python main.py
```

Start een `APScheduler`-`BlockingScheduler` met twee cron-triggers:
`daily_digest` dagelijks op `DIGEST_HOUR`, `weekly_discovery` wekelijks op
`DISCOVERY_DAY`/`DISCOVERY_HOUR` (zie `.env.example`).

## Microsoft Graph: verzenden (en later lezen) van mail

Mail loopt via de Microsoft Graph API met de client-credentials (app-only)
flow, niet via SMTP/IMAP. `graph_client.py` gebruikt `msal` om met
`GRAPH_TENANT_ID`/`GRAPH_CLIENT_ID`/`GRAPH_CLIENT_SECRET` een access token op
te halen (`get_graph_token()`); MSAL cachet dat token zelf tot vlak voor het
verloopt (~1 uur), dus elke aanroep vraagt niet opnieuw een token op.

De bijbehorende Azure AD app-registratie heeft de **application**-permissie
`Mail.Send` nodig (met admin consent) op `GRAPH_TENANT_ID`, en
`DIGEST_MAILBOX` moet een mailbox zijn die die app-registratie mag benaderen
(bijv. `jorensblogbox@meggelaars.nl`). Zolang die registratie er nog niet is,
laat `DIGEST_DRY_RUN=true` staan — dan wordt nooit echt geprobeerd een token
op te halen of mail te versturen.

## SearchProvider: nog geen keuze gemaakt

`search_provider.py` definieert de `SearchProvider`-interface
(vergelijkbaar met `EmbeddingProvider` in de scoring-service) plus een
concrete `BraveSearchProvider`. Zet `SEARCH_PROVIDER=brave` en
`SEARCH_API_KEY=...` in `.env` om die te gebruiken. Bing Search API is de
andere kandidaat maar heeft nog geen implementatie — die keuze staat nog
open (zie project-changelog/rapportage).

## Seen-items cache

`daily_digest.py` houdt per bron bij welke RSS-entry-URL's al gescoord zijn,
in een lokaal JSON-bestand (`SEEN_ITEMS_PATH`, standaard
`data/seen_items.json`). Dit voorkomt dat dezelfde feed-items elke dag
opnieuw gescoord worden. Geen externe state nodig voor Fase 1.

Per bron worden per run maximaal `MAX_NEW_ENTRIES_PER_SOURCE` (standaard 10,
0 = onbeperkt) van de **nieuwste** ongeziene items gescoord. Een nieuwe bron met
een grote feed (NCSC: honderden items, IETF: ~600) zou anders de hele run — en
dus de digest — uren blokkeren; de oudere ongeziene items worden als gezien
gemarkeerd in plaats van gescoord.

## Tests

```bash
uv run pytest
```

Dekt de pure-logica-onderdelen (zoektermen distilleren, seen-items-cache,
`SearchProvider`-fallback zonder configuratie) zonder een draaiende
scoring-service nodig te hebben. Het end-to-end-gedrag (digest versturen,
feedback-links, evaluate-all) is handmatig geverifieerd tegen een lokaal
draaiende scoring-service — zie de rapportage in de bijbehorende
changelog-entry.
