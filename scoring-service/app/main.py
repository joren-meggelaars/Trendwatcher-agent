from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, load_only
from starlette.middleware.sessions import SessionMiddleware

from app import admin, classification, digest_settings, discovery, migrations, models, runtime_settings, schemas
from app.config import settings
from app.database import Base, engine, get_db
from app.embeddings import EmbeddingProvider, get_embedding_provider
from app.scoring import score_and_store

app = FastAPI(title="Security Trendwatch Agent — Scoring Service")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)

# Idempotent: create_all only creates tables that don't exist yet, and
# add_missing_columns then adds columns introduced after a table was created.
Base.metadata.create_all(bind=engine)
migrations.add_missing_columns(engine)

admin.register(app)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Used by the Docker healthcheck — a real DB round-trip, not just "process is up"."""
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/score", response_model=schemas.ScoreResponse)
def score_item(
    payload: schemas.ScoreRequest,
    db: Session = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> schemas.ScoreResponse:
    if payload.source_id is not None and db.get(models.Source, payload.source_id) is None:
        raise HTTPException(status_code=404, detail=f"Source {payload.source_id} not found")

    item = score_and_store(
        db,
        provider,
        source=payload.source,
        title=payload.title,
        url=payload.url,
        raw_content=payload.raw_content,
        source_id=payload.source_id,
        user_id=settings.default_user_id,
    )

    return schemas.ScoreResponse(
        item_id=item.id,
        summary=item.summary,
        relevance_score=item.relevance_score,
        category=classification.classify(item.title, item.summary or ""),
    )


def _record_feedback(db: Session, item_id: int, label: str) -> models.Item | None:
    item = db.get(models.Item, item_id)
    if item is None:
        return None

    feedback = models.Feedback(
        item_id=item.id,
        user_id=settings.default_user_id,
        label=label,
    )
    db.add(feedback)
    db.commit()
    return item


@app.post("/feedback", response_model=schemas.FeedbackResponse)
def submit_feedback(
    payload: schemas.FeedbackRequest,
    db: Session = Depends(get_db),
) -> schemas.FeedbackResponse:
    item = _record_feedback(db, payload.item_id, payload.label)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {payload.item_id} not found")

    return schemas.FeedbackResponse()


@app.get("/feedback-link", response_class=HTMLResponse)
def feedback_link(
    item_id: int,
    label: schemas.FeedbackLabel,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """GET variant of /feedback so a link in an email can trigger feedback with one click."""
    item = _record_feedback(db, item_id, label)
    if item is None:
        return HTMLResponse(
            f"<html><body><h1>Item {item_id} niet gevonden.</h1></body></html>",
            status_code=404,
        )

    return HTMLResponse(
        "<html><body><h1>Bedankt voor je feedback!</h1>"
        f"<p>Item {item.id} gemarkeerd als “{label}”.</p></body></html>"
    )


@app.get("/items/recent-feedback", response_model=list[schemas.RecentFeedbackItem])
def recent_feedback_items(
    label: schemas.FeedbackLabel,
    days: int,
    db: Session = Depends(get_db),
) -> list[models.Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return (
        db.query(models.Item)
        .join(models.Feedback, models.Feedback.item_id == models.Item.id)
        .filter(models.Feedback.label == label, models.Feedback.created_at >= cutoff)
        .order_by(models.Item.id.desc())
        .all()
    )


@app.get("/items/top", response_model=list[schemas.TopItem])
def top_items(
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(5, ge=1, le=50),
    category: schemas.ItemCategory | None = Query(
        None, description="Only items of this category; omit for all."
    ),
    undigested: bool = Query(
        False, description="Only items that have not been mailed in a scheduled digest yet."
    ),
    db: Session = Depends(get_db),
) -> list[schemas.TopItem]:
    """Best already-scored items from the last `days` days, highest score first
    (newest first on ties). Used by the scheduler's digests, which mail what the
    continuously running ingest job has already scored instead of scoring
    feed entries themselves.

    With `category` the split is applied here (see app/classification.py),
    before `limit`, so the top N is filled with items of that category only.
    With `undigested` items already mailed (see POST /items/mark-digested) are left out."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = db.query(models.Item).filter(
        models.Item.relevance_score.is_not(None), models.Item.created_at >= cutoff
    )
    if undigested:
        query = query.filter(models.Item.digested_at.is_(None))
    query = query.order_by(models.Item.relevance_score.desc(), models.Item.id.desc())
    if category is None:
        items = query.limit(limit).all()
    else:
        # Category isn't stored, so classify in Python. Skip the heavy columns
        # (raw_content, embedding) for the candidates we only inspect.
        candidates = query.options(
            load_only(
                models.Item.id,
                models.Item.title,
                models.Item.url,
                models.Item.summary,
                models.Item.relevance_score,
                models.Item.source,
                models.Item.source_id,
            )
        ).all()
        items = [
            item
            for item in candidates
            if classification.classify(item.title, item.summary or "") == category
        ][:limit]
    return [
        schemas.TopItem(
            item_id=item.id,
            title=item.title,
            url=item.url,
            summary=item.summary or "",
            relevance_score=item.relevance_score,
            source_url=item.source_ref.url if item.source_ref else item.source,
        )
        for item in items
    ]


@app.get("/settings/runtime", response_model=schemas.RuntimeOverridesResponse)
def get_runtime_overrides(db: Session = Depends(get_db)) -> schemas.RuntimeOverridesResponse:
    """What the scheduler polls: the values an admin overrode in /admin/config
    for the scheduler's settings. Only registry keys, never secrets."""
    return schemas.RuntimeOverridesResponse(overrides=runtime_settings.overrides_for_scheduler(db))


@app.post("/settings/runtime/env", response_model=schemas.RuntimeEnvReportResponse)
def report_runtime_env(
    payload: schemas.RuntimeEnvReport,
    db: Session = Depends(get_db),
) -> schemas.RuntimeEnvReportResponse:
    """The scheduler reports its .env values, for display only. This can never
    change what the scheduler does — overrides are only set in the admin GUI."""
    return schemas.RuntimeEnvReportResponse(stored=runtime_settings.store_env_baseline(db, payload.values))


@app.post("/items/mark-digested", response_model=schemas.MarkDigestedResponse)
def mark_digested(
    payload: schemas.MarkDigestedRequest,
    db: Session = Depends(get_db),
) -> schemas.MarkDigestedResponse:
    """Record that these items were mailed in the daily digest. Idempotent: an
    item that is already marked keeps its first timestamp, unknown ids are
    ignored; `marked` is how many items were newly marked."""
    marked = (
        db.query(models.Item)
        .filter(models.Item.id.in_(payload.item_ids), models.Item.digested_at.is_(None))
        .update({"digested_at": datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.commit()
    return schemas.MarkDigestedResponse(marked=marked)


@app.post("/sources", response_model=schemas.SourceResponse, status_code=201)
def create_source(
    payload: schemas.SourceCreate,
    db: Session = Depends(get_db),
) -> models.Source:
    source = discovery.create_source(
        db, payload.url, payload.type, payload.discovery_method, category=payload.category
    )
    if source is None:
        raise HTTPException(status_code=409, detail=f"Source with url {payload.url!r} already exists")
    return source


@app.get("/sources", response_model=list[schemas.SourceResponse])
def list_sources(
    status: schemas.SourceStatus | None = None,
    db: Session = Depends(get_db),
) -> list[models.Source]:
    query = db.query(models.Source)
    if status is not None:
        query = query.filter(models.Source.status == status)
    return query.order_by(models.Source.id).all()


@app.post("/sources/{source_id}/evaluate", response_model=schemas.SourceResponse)
def evaluate_source(source_id: int, db: Session = Depends(get_db)) -> models.Source:
    source = discovery.evaluate_source(db, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
    return source


@app.post("/sources/evaluate-all", response_model=list[schemas.SourceStatusChange])
def evaluate_all_sources(db: Session = Depends(get_db)) -> list[dict]:
    return discovery.evaluate_all_sources(db)


@app.get("/settings/digest", response_model=schemas.DigestSettingsResponse)
def get_digest_settings_endpoint(db: Session = Depends(get_db)) -> models.DigestSettings:
    return digest_settings.get_digest_settings(db)


@app.put("/settings/digest", response_model=schemas.DigestSettingsResponse)
def update_digest_settings_endpoint(
    payload: schemas.DigestSettingsUpdate,
    db: Session = Depends(get_db),
) -> models.DigestSettings:
    return digest_settings.update_digest_settings(db, payload.digest_hour, payload.digest_top_n)
