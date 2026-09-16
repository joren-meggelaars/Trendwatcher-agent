# Changelog

Alle noemenswaardige wijzigingen aan de scoring-service worden hier bijgehouden.
Formaat losjes gebaseerd op [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] — Dynamische bronnenlijst (Fase 1, vervolg)

Uitbreiding op de oorspronkelijke Fase 1-scope: geen vaste bronnenlijst meer,
bronnen kunnen zelf groeien (instroom) en krimpen op basis van relevantie, en
kunnen handmatig worden toegevoegd. `/score` en `/feedback` blijven ongewijzigd
werken.

### Added
- **`Source`-model** (`app/models.py`): `id`, `url` (uniek), `type`, `status`
  (`kandidaat` / `actief` / `gedeactiveerd`), `discovery_method`
  (`seed` / `link_following` / `market_sweep` / `manual`), `running_avg_score`,
  `created_at`.
- **`Item.source_id`** (nullable FK naar `Source`) zodat elk gescoord item terug
  te herleiden is naar zijn bron.
- **Link-extractie bij het scoren** (`app/discovery.py`): uitgaande links in
  `raw_content` worden herkend (regex, geen HTML-parser nodig), het domein
  wordt eruit gehaald en onbekende domeinen worden automatisch geregistreerd
  als kandidaat-bron (`discovery_method="link_following"`). Geen externe
  requests naar de gelinkte pagina's zelf.
- **`POST /sources`** — handmatig een bron toevoegen (`{url, type}`), start
  altijd als `kandidaat` met `discovery_method="manual"`. `409` bij dubbele
  `url`.
- **`GET /sources?status=...`** — bronnen opvragen, optioneel gefilterd op
  status.
- **`POST /sources/{id}/evaluate`** — herbeoordeelt één bron:
  - **Instroom**: `kandidaat` → `actief` als ≥3 van de laatste 5 items een
    `relevance_score` > 0.6 hebben.
  - **Krimp**: `actief` → `gedeactiveerd` als ≥8 van de laatste 10 items
    `niet_interessant` zijn gelabeld, of `running_avg_score` < 0.3.
  - Losstaand aan te roepen; geautomatiseerde weekplanning volgt in Fase 3
    (n8n).
- **`running_avg_score`-bijwerking**: voortschrijdend gemiddelde per bron,
  bijgewerkt bij elke score van een item met die `source_id` (geen zware
  herberekening over de hele historie).
- **Configureerbare drempels** (`app/config.py` / `.env`, geen codewijziging
  nodig): `SOURCE_ACTIVATION_WINDOW`, `SOURCE_ACTIVATION_MIN_HIGH_SCORE`,
  `SOURCE_ACTIVATION_SCORE_THRESHOLD`, `SOURCE_DEACTIVATION_WINDOW`,
  `SOURCE_DEACTIVATION_MIN_NEGATIVE`, `SOURCE_DEACTIVATION_AVG_SCORE_THRESHOLD`.
- **Tests** (`tests/test_sources.py`, 8 nieuwe): source-aanmaak, filtering,
  automatische kandidaat-registratie via links, geen dubbele registratie,
  instroom-scenario, krimp-scenario, 404 op onbekende bron, en configureerbare
  drempel via `monkeypatch`.
- **Demo-script** (`scripts/demo_sources.py`) + **testdata**
  (`data/discovery_testset.jsonl`): live end-to-end-demonstratie van
  link-discovery, instroom en krimp tegen een draaiende server. Bewust een
  los databestand van `data/testset.jsonl` gehouden, om de bestaande
  cluster-vergelijking in `scripts/demo.py` niet te verstoren.

### Changed
- `ScoreRequest` (`app/schemas.py`) heeft een optioneel `source_id`-veld
  gekregen; bestaande callers zonder dit veld blijven ongewijzigd werken.
- `README.md` uitgebreid met de nieuwe endpoints en de instroom-/krimpregels.

### Verified
- Alle 14 tests slagen (6 bestaand + 8 nieuw): `uv run pytest`.
- Live tegen een draaiende server (fake embedding provider, om Voyage's
  rate limit van 3 RPM zonder betaalmethode te omzeilen): link-discovery,
  instroom- en krimp-scenario succesvol doorlopen via `scripts/demo_sources.py`.
- `/score`-response-contract ongewijzigd bevestigd (zelfde drie velden,
  ook wanneer een link een nieuwe kandidaat-bron registreert).

## [0.1.0] — Fase 1 scoring-service

Initiële versie: FastAPI-service met SQLite, Voyage-embeddings, `/score` en
`/feedback`, cold-start neutrale score-fallback.
