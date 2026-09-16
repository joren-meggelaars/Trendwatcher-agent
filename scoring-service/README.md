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

- `POST /score` — `{source, title, url, raw_content}` →
  `{item_id, summary, relevance_score}`
- `POST /feedback` — `{item_id, label: "interessant" | "niet_interessant"}` →
  `{status: "ok"}`, of een `404` als `item_id` niet bestaat.

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
