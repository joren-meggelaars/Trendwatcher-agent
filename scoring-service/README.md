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
  `{item_id, summary, relevance_score, category}`. Uitgaande links in
  `raw_content` worden automatisch als kandidaat-bron geregistreerd (zie
  hieronder); dit verandert niets aan de response. Een al opgeslagen `url`
  geeft het bestaande item terug (geen duplicaat, geen nieuwe embedding).
  `category` is `"markt"` of `"nieuws"`. Markt is wat nieuw is of verandert in
  de securitymarkt: innovaties, nieuwe producten en diensten, nieuwe
  ontwikkelingen (trends, nieuwe spelers, nieuwe protocollen en standaarden) én
  de zakelijke kant (overnames, funding, marktcijfers, analistenrapporten).
  Nieuws is de rest: incidenten, kwetsbaarheden, patches, advisories,
  threat research. Bepaald met trefwoorden op titel + samenvatting
  (`app/classification.py`), met een contextcontrole: "launches" of "releases"
  in een aanvals- of patchverhaal ("hackers launch new campaign", "Apple
  releases new version to fix a zero-day") telt niet als productnieuws.
  Titel en samenvatting worden als platte tekst opgeslagen (`app/textclean.py`:
  HTML-tags weg, ook dubbel gecodeerde entiteiten als `&amp;quot;` gedecodeerd,
  WordPress' "The post … appeared first on …"-regel weggehaald). Hetzelfde
  artikel wordt niet twee keer bewaard: zelfde URL (ook met tracking-parameters)
  of dezelfde titel van dezelfde bron (`app/dedupe.py`).
- `GET /items/top?days=7&limit=5&category=markt|nieuws` — beste al gescoorde
  items, hoogste score eerst; met `category` wordt eerst gefilterd en pas
  daarna gelimiteerd. Een artikel dat toch dubbel in de database staat komt er
  één keer uit, en tekst komt schoon terug (ook voor oudere items).

### Hoe de score tot stand komt

Er wordt geen model getraind: de embeddings komen van een vooraf getraind
model (Voyage) en veranderen niet. "Leren" is hier dat je feedback de
vergelijkingsverzameling bepaalt. Voor een nieuw item, binnen zijn eigen
categorie (markt en nieuws leren los van elkaar):

`score = 0,5 + nabijheid tot het dichtstbijzijnde 👍-item − nabijheid tot het
dichtstbijzijnde 👎-item`

waarbij elke kant neutraal (0,5) is zolang er nog geen feedback van die soort
is, en nabijheid de cosine-similarity is, herschaald naar 0–1. Zonder
feedback is elke score dus 0,5.

Een score wordt berekend bij het scoren, en daarna **opnieuw** zodra je
feedback verandert, zodat een duimpje ook al opgeslagen items verplaatst:
`POST /items/rescore?days=30` rekent de items van de laatste 30 dagen opnieuw
uit met de opgeslagen embeddings (dus zonder Voyage-aanroepen; ook
`running_avg_score` van de bronnen wordt bijgewerkt). De scheduler roept dit aan
na elke ingest-run, de beoordeel-pagina bij een lege lijst en met de knop
"Scores bijwerken". Het doet niets zolang de duimpjes niet veranderd zijn.
"Overgeslagen" items (beoordeel-pagina) tellen niet mee.
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
- `GET /feedback-link?item_id=X&label=...` — de 👍/👎-link uit mails van vóór de
  digest-pagina's. Legt **niets** meer vast (een mailscanner of link-preview kan
  zo'n GET ophalen): stuurt door naar `/admin/vote-link`, dus via de login naar
  de digest waar het item in zat. Zit ook in de publieke `web`-app.
- `POST /digests` `{kind, items: [{item_id, category}]}` en
  `POST /digests/{id}/mailed` — de scheduler legt vast welke items een digest
  bevat (vóór het mailen), zodat de knoppen in de mail naar precies die digest
  wijzen, en markeert hem als verstuurd.
- `GET /items/recent-feedback?label=...&days=...` — `title` + `summary` van
  items die in de afgelopen `days` dagen met `label` zijn gemarkeerd. Laat de
  scheduler kernonderwerpen destilleren zonder rechtstreekse DB-toegang.

## Admin-GUI (/admin/*)

Server-rendered (Jinja2, geen React/build-stap) beheerinterface met een eigen
ontwerp (`app/static/app.css`: licht/donker naar je systeem, zijbalk op een
scherm, menu op een telefoon; geen externe bibliotheken). Alle scripts staan in
`app/static/*.js` en de pagina's hebben geen inline code, zodat een strikte
Content-Security-Policy kan.

**Twee apps, één codebase.** `app.main` is de interne JSON-API
(`/score`, `/feedback`, `/sources`, ... zonder login; die bevat de GUI ook, voor
lokaal ontwikkelen). `app.web` is **alleen de GUI**: zonder enige API-route en
zonder `/docs`. In docker-compose draait `web` als eigen container en is dat het
enige dat naar buiten wordt gepubliceerd; wat niet in de app zit, kan niet worden
bereikt, ook niet bij een verkeerd ingestelde reverse proxy. Zie DEPLOY.md voor
HTTPS en toegang van buitenaf.

**Beveiliging** (`app/security.py`, `app/auth.py`): bcrypt-wachtwoord; 5
verkeerde pogingen per adres blokkeren dat adres 15 minuten; sessies met een
verloopdatum (12 uur, of 30 dagen met "onthoud mij"; elke login start een nieuwe
sessie); posts naar `/admin/*` van een andere site worden geweigerd (Origin- of
Referer-controle, aanvullend op SameSite=Lax); Content-Security-Policy, geen
framing, geen caching van admin-pagina's; `?next=` na de login mag alleen een pad
in `/admin` zijn. `SESSION_COOKIE_SECURE` en `PUBLIC_BASE_URL` (`.env`) horen bij
HTTPS.

- `GET /admin/digests` en `GET /admin/digest/<id>` — het archief van digests en
  één digest zoals hij is gemaild (secties markt en nieuws), met je huidige
  👍/👎. Op die pagina kun je alles beoordelen zonder weg te navigeren: klik op een
  knop om te stemmen, nogmaals om het antwoord te wissen, of ongedaan te maken uit
  de melding. `POST /admin/vote/{item_id}` (`like`/`dislike`/`clear`) zet je ene
  antwoord voor dat item en geeft het vorige terug. **De knoppen in de mail**
  openen `/admin/digest/<id>?vote=<item>:<like|dislike>`: niet ingelogd, dan eerst
  de login (en daarna terug), en de pagina legt die stem zelf vast met een POST,
  niet via de link. `/admin/digest/latest` gaat naar de nieuwste.

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
- `GET /admin/review` — **Beoordelen**: opgeslagen artikelen één voor één
  aangeboden voor een 👍/👎 (of overslaan), los van de digests, om snel initiële
  feedback te geven. Tabbladen Markt / Nieuws / Alles met per categorie hoeveel
  er nog te doen zijn en hoeveel duimpjes je al gaf. Aangeboden worden items
  van de laatste 30 dagen zonder antwoord van jou, zonder dubbelen (ook geen
  tweeling van iets wat je al beoordeelde), verspreid over bronnen (van elke
  bron om de beurt de nieuwste) zodat een eerste ronde breed dekt. Zonder
  JavaScript gewone formulieren; met JavaScript verdwijnt een kaart zonder
  herladen en werken de toetsen `y`/`→` (👍), `n`/`←` (👎), `s`/`↓` (overslaan)
  en `u` (ongedaan maken). Een antwoord is één rij feedback (`POST
  /admin/review/{item_id}`; overslaan = label `overgeslagen`, telt niet mee
  voor de scores); is de lijst leeg, dan worden de scores bijgewerkt.
- `GET/POST /admin/config` — **Configuratie**: de instellingen die normaal in
  `.env` staan, bewerkbaar zonder herstart (scorepauze, ingest-interval, dry-run,
  discovery, mailbox-ingest, drempels voor bronnen-instroom/krimp, ...). Een
  ingevulde waarde *overschrijft* `.env` (opgeslagen in de tabel
  `runtime_settings`); een leeg veld volgt weer `.env`. Alles wordt gevalideerd
  (type en bereik) en is alles-of-niets. De scheduler krijgt direct bericht en
  plant zijn jobs zo nodig opnieuw in. **Geheimen en alles wat bepaalt waar mail
  heen gaat blijven alleen via `.env` op de VM te wijzigen** (API-sleutels,
  Graph-gegevens, admin-wachtwoord, `DIGEST_MAILBOX`/`DIGEST_TO_EMAIL`,
  `FEEDBACK_BASE_URL`, embedding-model, netwerk-URL's, ...): die staan niet in
  het register (`app/runtime_settings.py`), worden nooit opgeslagen of
  getoond, en de pagina noemt ze onderaan alleen bij naam met de reden.
  Voor de scheduler zijn er twee JSON-endpoints: `GET /settings/runtime`
  (de overrides) en `POST /settings/runtime/env` (de scheduler meldt zijn
  `.env`-waarden, alleen om te tonen wat echt actief is).

## Bronbeheer: instroom en krimp

Er is geen vaste bronnenlijst. Bronnen ontstaan op drie manieren:
handmatig via `POST /sources` (`discovery_method: "manual"`), of automatisch
doordat een gescoord item linkt naar een nog onbekend domein
(`discovery_method: "link_following"`, alleen het domein wordt geregistreerd,
er wordt geen externe request gedaan). Elke nieuwe bron start als
`"kandidaat"`.

`POST /sources/{id}/evaluate` (los aan te roepen, bijvoorbeeld vanuit een
testscript; automatische aanroep vanuit een schema komt in Fase 3) past twee
regels toe, met drempels in `app/config.py` / `.env`, die je ook in de admin-GUI
kunt overschrijven (`/admin/config`; geen codewijziging of herstart nodig om te
kalibreren):

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
