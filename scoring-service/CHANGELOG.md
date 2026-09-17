# Changelog

Alle noemenswaardige wijzigingen aan de scoring-service worden hier bijgehouden.
Formaat losjes gebaseerd op [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] — Lokale admin-GUI (/admin/*)

Server-rendered Jinja2-beheerinterface direct in de bestaande FastAPI-app,
achter een simpele sessie-login. `/score`, `/feedback`, `/sources` blijven
ongewijzigd en puur JSON.

### Added
- `app/auth.py`: bcrypt-wachtwoordhash (passlib) + sessie-cookie-check
  (`require_admin_session`), één vast account via `ADMIN_USERNAME` /
  `ADMIN_PASSWORD_HASH`.
- `app/admin.py`: alle `/admin/*`-routes (login/logout, bronnenoverzicht met
  handmatige status-override en toevoegformulier, itemsoverzicht met
  filters + paginering, batch-toevoegen via URL's).
- `app/templates/*.html`: server-rendered Jinja2-templates, minimale inline
  CSS, geen JS-framework.
- `scripts/hash_admin_password.py`: genereert een bcrypt-hash voor
  `ADMIN_PASSWORD_HASH`.
- `SessionMiddleware` (Starlette, ondertekend met `SESSION_SECRET_KEY`) in
  `app/main.py`. Een leeg gelaten `SESSION_SECRET_KEY` genereert een
  willekeurige sleutel per processtart (veilig by default; bestaande
  sessies overleven dan geen herstart).
- 8 nieuwe tests (`tests/test_admin.py`): redirect bij niet-ingelogd, foute
  login geeft nette 401 (geen 500), volledige login→pagina's→logout-flow,
  bronformulier zichtbaar na herladen, status-override, batch-add met
  succes+falen.

### Changed
- `app/scoring.py`: nieuwe `score_and_store()` bevat nu de gedeelde
  scoringslogica (embedding, relevance, opslag, discovery-hooks) — gebruikt
  door zowel `POST /score` als `/admin/items/batch-add`, geen dubbele
  implementatie.
- `app/discovery.py`: nieuwe `create_source()` bevat de gedeelde
  bron-aanmaaklogica — gebruikt door zowel `POST /sources` als het
  admin-bronformulier.
- `app/config.py`: `Settings` uitgebreid met `admin_username`,
  `admin_password_hash`, `session_secret_key`.

### Verified
- Alle 27 tests slagen (19 bestaand + 8 nieuw): `uv run pytest`.
- Live tegen een draaiende server: volledige cookie-login-flow (fout
  wachtwoord → 401 met nette foutmelding; correct wachtwoord → sessie-cookie
  + toegang tot alle `/admin/*`-pagina's), bronformulier zichtbaar na
  herladen, status-override zichtbaar via `GET /sources`, en batch-add met
  2 echte URL's + 1 ongeldige URL gaf exact "2 gelukt, 1 mislukt" met titel
  + score op de successen en een duidelijke reden op de mislukking.
- Tijdens het testen ontdekt en gefixt: een leeg gelaten `SESSION_SECRET_KEY=`
  in `.env` overschreef de willekeurige-sleutel-default met een lege,
  voorspelbare string — nu afgevangen met een validator.

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
