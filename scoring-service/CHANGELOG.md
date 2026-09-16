# Changelog

Alle noemenswaardige wijzigingen aan de scoring-service worden hier bijgehouden.
Formaat losjes gebaseerd op [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] — Endpoints voor de eigen Python-scheduler (n8n-vervanging)

Vijf aanpassingen zodat een nieuwe `scheduler/`-component (los onderdeel,
communiceert alleen via HTTP) de scoring-service kan aansturen zonder ooit
rechtstreeks in de database te kijken. `/score` en `/feedback` blijven
ongewijzigd werken.

### Added
- **`GET /items/recent-feedback?label=...&days=...`** — `title` + `summary`
  van items gelabeld binnen de opgegeven periode. Voor de wekelijkse
  discovery-job om zoektermen te destilleren uit recent gemarkeerde content.
- **`POST /sources/evaluate-all`** — draait `evaluate_source` over alle
  bronnen met status `kandidaat`/`actief` in één aanroep en retourneert alleen
  de daadwerkelijke statuswijzigingen (`app/discovery.py:evaluate_all_sources`).
- **`GET /feedback-link?item_id=...&label=...`** — GET-variant van
  `POST /feedback` (zelfde databasewijziging via een gedeelde
  `_record_feedback`-helper), retourneert een HTML-bevestigingspagina.
  Nodig omdat feedback-links in een e-mail alleen als klikbare GET werken.
- **`SourceCreate.discovery_method`** (optioneel, default `"manual"`) — laat
  de scheduler zijn eigen market-sweep-bronnen correct taggen als
  `"market_sweep"` in plaats van `"manual"`.
- Tests (`tests/test_scheduler_support.py`, 6 nieuw) voor alle drie de nieuwe
  endpoints, inclusief een gecombineerd instroom+krimp-scenario via één
  `evaluate-all`-aanroep.

### Verified
- Alle 19 tests slagen (13 bestaand + 6 nieuw): `uv run pytest`.
- Live tegen een draaiende server: `GET /feedback-link` geklikt via curl gaf
  dezelfde wijziging als `POST /feedback` (geverifieerd via
  `GET /items/recent-feedback`); `POST /sources/evaluate-all` rapporteerde in
  één aanroep zowel een instroom- als een krimp-statuswijziging correct.

(Zie `scheduler/README.md` voor de component die deze endpoints gebruikt.)

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
