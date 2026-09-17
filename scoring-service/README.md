# Scoring Service — Fase 1

Lokale scoring-service voor de Security Trendwatch Agent (Fase 1 uit het PvA).
Neemt een security-artikel aan, vat het samen, scoort het op relevantie, en
leert van "interessant"/"niet interessant"-feedback via embedding-similarity.

Deze fase is bewust volledig los van n8n, Docker en Azure — die komen in
latere fases. Alles hieronder draait lokaal met `uv` en SQLite.

## Setup

```bash
uv sync
cp .env.example .env
# vul VOYAGE_API_KEY in .env in, of laat EMBEDDING_PROVIDER=fake staan
# om zonder API-key te kunnen testen
```

## Draaien

```bash
uv run uvicorn app.main:app --reload
```

De service draait dan op `http://127.0.0.1:8000`. Tabellen worden bij het
opstarten automatisch aangemaakt (`Base.metadata.create_all`, idempotent).

## Endpoints

- `POST /score` — `{source, title, url, raw_content, source_id?}` →
  `{item_id, summary, relevance_score}`. Uitgaande links in `raw_content`
  worden automatisch als kandidaat-bron geregistreerd (zie hieronder); dit
  verandert niets aan de response.
- `POST /feedback` — `{item_id, label: "interessant" | "niet_interessant"}` →
  `{status: "ok"}`, of een `404` als `item_id` niet bestaat.
- `POST /sources` — `{url, type, discovery_method?}` → nieuwe `Source` met
  `status: "kandidaat"`. `discovery_method` is optioneel en standaard
  `"manual"`; de scheduler geeft hier `"market_sweep"` mee voor bronnen die
  hij zelf via een zoekopdracht vindt. `409` als de `url` al bestaat.
- `GET /sources?status=kandidaat|actief|gedeactiveerd` — lijst bronnen,
  optioneel gefilterd op status.
- `POST /sources/{id}/evaluate` — herbeoordeelt één bron (instroom naar
  "actief" of krimp naar "gedeactiveerd", zie hieronder) en geeft de
  bijgewerkte `Source` terug. `404` als de bron niet bestaat.
- `POST /sources/evaluate-all` — draait `evaluate_source` over alle bronnen
  met status `"kandidaat"` of `"actief"` in één keer, en retourneert alleen
  de bronnen waarvan de status daadwerkelijk wijzigde:
  `[{source_id, url, old_status, new_status}, ...]`.
- `GET /feedback-link?item_id=X&label=...` — functioneel identiek aan
  `POST /feedback` (zelfde databasewijziging), maar bereikbaar via een simpele
  klikbare GET-link en retourneert een kleine HTML-bevestigingspagina in
  plaats van JSON. Bedoeld voor feedback-links in e-mails (zie `scheduler/`).
- `GET /items/recent-feedback?label=...&days=...` — `title` + `summary` van
  items die in de afgelopen `days` dagen met `label` zijn gemarkeerd. Laat de
  scheduler kernonderwerpen destilleren zonder rechtstreekse DB-toegang.

## Lokale admin-GUI (/admin/*)

Server-rendered (Jinja2, geen React/build-stap) beheerinterface, direct in
deze FastAPI-app maar volledig gescheiden van de JSON-API hierboven
(`/score`, `/feedback`, `/sources` blijven puur JSON). Uitsluitend bedoeld
voor lokaal/intern netwerkgebruik — geen internetblootstelling, geen zware
auth.

Setup (eenmalig):

```bash
uv run python -m scripts.hash_admin_password   # genereert een bcrypt-hash
# zet ADMIN_USERNAME en de hash in .env als ADMIN_PASSWORD_HASH
```

- `GET/POST /admin/login`, `POST /admin/logout` — sessie-cookie-auth (via
  Starlette's `SessionMiddleware`, ondertekend met `SESSION_SECRET_KEY`).
  Niet ingelogd → elke andere `/admin/*`-route stuurt door naar
  `/admin/login`.
- `GET/POST /admin/sources` — bronnentabel (status, gem. score, aantal
  items) met filter op status, een formulier om een nieuwe bron toe te
  voegen, en per rij een handmatige status-override.
- `GET /admin/items` — itemsoverzicht met filters (bron, minimale score,
  laatste 7/30 dagen) en paginering (50 per pagina).
- `GET/POST /admin/items/batch-add` — plak meerdere URL's (één per regel);
  elke URL wordt opgehaald, van HTML ontdaan en gescoord via dezelfde
  `score_and_store()`-functie die ook achter `POST /score` zit. Toont per
  URL succes (titel + score) of falen (reden).

## Bronbeheer: instroom en krimp

Er is geen vaste bronnenlijst. Bronnen ontstaan op drie manieren:
handmatig via `POST /sources` (`discovery_method: "manual"`), of automatisch
doordat een gescoord item linkt naar een nog onbekend domein
(`discovery_method: "link_following"`, alleen het domein wordt geregistreerd,
er wordt geen externe request gedaan). Elke nieuwe bron start als
`"kandidaat"`.

`POST /sources/{id}/evaluate` (los aan te roepen, bijvoorbeeld vanuit een
testscript; automatische aanroep vanuit een schema komt in Fase 3) past twee
regels toe, met drempels in `app/config.py` / `.env` (geen codewijziging
nodig om te kalibreren):

- **Instroom** (`kandidaat` → `actief`): minstens
  `SOURCE_ACTIVATION_MIN_HIGH_SCORE` (standaard 3) van de laatste
  `SOURCE_ACTIVATION_WINDOW` (standaard 5) items van die bron scoren boven
  `SOURCE_ACTIVATION_SCORE_THRESHOLD` (standaard 0.6).
- **Krimp** (`actief` → `gedeactiveerd`): minstens
  `SOURCE_DEACTIVATION_MIN_NEGATIVE` (standaard 8) van de laatste
  `SOURCE_DEACTIVATION_WINDOW` (standaard 10) items zijn als
  `"niet_interessant"` gelabeld, of `running_avg_score` zakt onder
  `SOURCE_DEACTIVATION_AVG_SCORE_THRESHOLD` (standaard 0.3).

`running_avg_score` op een `Source` wordt bijgewerkt als voortschrijdend
gemiddelde telkens wanneer een item met die `source_id` gescoord wordt.

## Embeddings: Voyage AI of offline "fake"

`EMBEDDING_PROVIDER` in `.env` bepaalt welke implementatie van
`EmbeddingProvider` (`app/embeddings.py`) gebruikt wordt:

- `voyage` (standaard) — echte embeddings via de Voyage AI API
  (`voyage-3`, `input_type="document"`). Vereist `VOYAGE_API_KEY`.
- `fake` — deterministische, dependency-vrije stand-in (feature hashing op
  woorden). Geen API-key nodig; genoeg om de scoring/feedback-loop lokaal te
  demonstreren voordat je een Voyage-key hebt.

Unit tests (`tests/`) zetten dit altijd op `fake` via `conftest.py`, zodat de
testsuite nooit een netwerkcall of API-key nodig heeft.

## Tests

```bash
uv run pytest
```

## Demo: aantonen dat feedback de scoring beïnvloedt

```bash
# in een terminal: de service draaiend houden
uv run uvicorn app.main:app --reload

# in een andere terminal
uv run python scripts/demo.py
```

Dit script stuurt alle 12 artikelen uit `data/testset.jsonl` (drie clusters:
netwerkapparatuur, cloud-security, phishing) naar `/score`, markeert twee
netwerkapparatuur-items als "interessant" via `/feedback`, scoort daarna
opnieuw en toont aan dat overige netwerkapparatuur-items nu hoger scoren dan
items uit de andere clusters.

Werkt met zowel `EMBEDDING_PROVIDER=fake` (geen API-key nodig) als
`EMBEDDING_PROVIDER=voyage` (na het invullen van `VOYAGE_API_KEY`).

## Database: SQLite nu, PostgreSQL + pgvector later

De connectiestring (`DATABASE_URL`) bepaalt de database, niet de code:

```bash
# lokaal, standaard
DATABASE_URL=sqlite:///./scoring.db

# later, zonder wijzigingen in models.py/scoring.py/main.py
DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname
```

De embedding-kolom (`app/models.py`, `EmbeddingVector`) kijkt zelf naar de
SQLAlchemy-dialect: op SQLite wordt de vector als JSON-tekst opgeslagen, op
PostgreSQL schakelt hij automatisch over op pgvector's native `Vector`-type.
Voor die laatste stap moet je op dat moment wel `psycopg` en `pgvector` (het
Python-package) toevoegen aan de dependencies — dat is bewust nu nog niet
gedaan omdat Fase 1 op SQLite draait.

## Buiten scope van Fase 1

n8n-orkestratie, Docker-containerisatie, Azure-deployment, multi-user-auth
(er is nu een vaste `DEFAULT_USER_ID=joren`), en een echte LLM-samenvatting
(`summarize()` in `app/scoring.py` is nu een simpele truncatie-placeholder).
