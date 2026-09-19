"""The admin GUI as its own app: the one to expose to the internet.

app.main serves the JSON API the scheduler uses (/score, /sources, /feedback,
/settings/..., /digests, ...) next to the GUI, without any login on the API.
That must never be reachable from outside. This app contains the GUI and
nothing else — the API routes are not here at all — so it can be published
(behind a reverse proxy with HTTPS) without exposing the API: what is not in
the app cannot be reached, whatever the proxy is configured to forward.

    uvicorn app.web:app          # what docker-compose runs as the `web` service

Database and settings are the same as the API's; it does not create tables (the
API service does that at startup, and `web` starts after it is healthy).
"""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app import admin
from app.database import SessionLocal

# No /docs, /redoc or openapi.json here: nothing to advertise to the internet.
app = FastAPI(title="Trendwatch — beheer", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, **admin.session_options())
admin.register(app)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/admin/")


@app.get("/health", include_in_schema=False)
def health() -> dict:
    """The container healthcheck: a real database round-trip."""
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok"}
