from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app import admin, discovery, models, schemas
from app.config import settings
from app.database import Base, engine, get_db
from app.embeddings import EmbeddingProvider, get_embedding_provider
from app.scoring import score_and_store

app = FastAPI(title="Security Trendwatch Agent — Scoring Service")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)

# Idempotent: create_all only creates tables that don't exist yet.
Base.metadata.create_all(bind=engine)

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

    return schemas.ScoreResponse(item_id=item.id, summary=item.summary, relevance_score=item.relevance_score)


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


@app.post("/sources", response_model=schemas.SourceResponse, status_code=201)
def create_source(
    payload: schemas.SourceCreate,
    db: Session = Depends(get_db),
) -> models.Source:
    source = discovery.create_source(db, payload.url, payload.type, payload.discovery_method)
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
