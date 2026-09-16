# Scheduler

Eigen Python-scheduler die n8n vervangt voor het orkestreren van de
scoring-service. Praat uitsluitend via HTTP met de scoring-service (nooit
rechtstreeks met de database), zodat beide componenten later onafhankelijk
van elkaar naar Azure kunnen (Container App / Function).

## Setup

```bash
uv sync
cp .env.example .env
# vul minimaal SCORING_SERVICE_URL in; SMTP_* en SEARCH_* mogen leeg blijven
# om zonder e-mail/zoek-API te testen (zie hieronder)
```

## Jobs

- **`jobs/daily_digest.py`** — haalt actieve bronnen op
  (`GET /sources?status=actief`), parst hun RSS-feed met `feedparser`, scoort
  nieuwe items via `POST /score` (met `source_id`), selecteert de top-N op
  `relevance_score` en verstuurt een HTML-digest met per item twee
  feedback-links (`GET /feedback-link?item_id=...&label=...`). Zonder
  `SMTP_HOST` wordt de digest-HTML naar de console gelogd in plaats van
  verstuurd — handig om te testen voordat SMTP is ingericht.
- **`jobs/weekly_discovery.py`** — haalt recente `"interessant"`-items op
  (`GET /items/recent-feedback?label=interessant&days=...`), distilleert
  daaruit een paar zoektermen (woordfrequentie op titels, geen NLP nodig),
  zoekt daarmee via een `SearchProvider` naar kandidaat-content, registreert
  onbekende domeinen als kandidaat-bron (`discovery_method="market_sweep"`)
  en scoort de gevonden content. Sluit af met `POST /sources/evaluate-all`.
  **Zonder geconfigureerde `SearchProvider` slaat de zoekstap netjes over**
  (met een warning-log) — de rest van de job (ophalen, distilleren,
  evaluate-all) draait gewoon door.

Beide jobs zijn los aan te roepen, zonder de scheduler-loop:

```bash
uv run python -m jobs.daily_digest
uv run python -m jobs.weekly_discovery
```

## Scheduler draaien

```bash
uv run python main.py
```

Start een `APScheduler`-`BlockingScheduler` met twee cron-triggers:
`daily_digest` dagelijks op `DIGEST_HOUR`, `weekly_discovery` wekelijks op
`DISCOVERY_DAY`/`DISCOVERY_HOUR` (zie `.env.example`).

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
