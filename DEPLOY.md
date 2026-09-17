# Deployment op de Ubuntu 24.04 Docker-VM

Alle drie de componenten (Postgres, scoring-service + admin-GUI, scheduler)
draaien als containers via `docker compose`. Docker/Docker Compose worden
door jou op de VM geïnstalleerd — dit document begint bij een schone VM met
een werkende `docker`/`docker compose`-installatie.

## 1. Clonen en configureren

```bash
git clone <repo-url> trendwatcher-agent
cd trendwatcher-agent

cp .env.example .env
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

## 4. Starten

```bash
docker compose up -d
```

Start alle drie de containers. `scoring-service` wacht op Postgres'
healthcheck (niet alleen op "container bestaat") voordat hij zelf opstart;
`scheduler` wacht op de gezondheidscheck van `scoring-service`.

## 5. Controleren dat alles draait

```bash
docker compose ps
```

Alle drie de services moeten `running`/`healthy` tonen (postgres en
scoring-service hebben een healthcheck; scheduler heeft er geen — een
lopende `Up`-status daar is voldoende, want het proces is een simpele
achtergrond-loop zonder eigen HTTP-endpoint).

```bash
docker compose logs -f scoring-service
docker compose logs -f scheduler
```

Snelle rooktest vanaf de VM zelf:

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}

curl -s -X POST http://localhost:8000/score \
  -H "Content-Type: application/json" \
  -d '{"source":"test","title":"T","url":"https://example.com","raw_content":"Test artikel over een kwetsbaarheid."}'
```

De admin-GUI is bereikbaar op `http://<BIND_ADDRESS>:8000/admin/login` vanaf
elk toestel binnen hetzelfde netwerk — niet van buitenaf (daar staat de
bestaande reverse proxy voor, die hier niet is meegenomen).

## 6. Geautomatiseerde tests draaien tegen de Postgres-container

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

## 7. Data-persistentie verifiëren

```bash
# maak een bron aan
curl -s -X POST http://localhost:8000/sources \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/feed","type":"rss"}'

docker compose restart

curl -s http://localhost:8000/sources
# de zojuist aangemaakte bron moet er nog steeds staan
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
