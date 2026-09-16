from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app import discovery, models, schemas
from app.config import settings
from app.database import Base, engine, get_db
from app.embeddings import EmbeddingProvider, get_embedding_provider
from app.scoring import compute_relevance_score, summarize

app = FastAPI(title="Security Trendwatch Agent — Scoring Service")

# Idempotent: create_all only creates tables that don't exist yet.
Base.metadata.create_all(bind=engine)


@app.post("/score", response_model=schemas.ScoreResponse)
def score_item(
    payload: schemas.ScoreRequest,
    db: Session = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> schemas.ScoreResponse:
    source_ref = None
    if payload.source_id is not None:
        source_ref = db.get(models.Source, payload.source_id)
        if source_ref is None:
            raise HTTPException(status_code=404, detail=f"Source {payload.source_id} not found")

    summary = summarize(payload.raw_content)
    embedding = provider.embed(payload.raw_content)
    relevance_score = compute_relevance_score(db, embedding, user_id=settings.default_user_id)

    item = models.Item(
        user_id=settings.default_user_id,
        source=payload.source,
        source_id=payload.source_id,
        title=payload.title,
        url=payload.url,
        raw_content=payload.raw_content,
        summary=summary,
        embedding=embedding,
        relevance_score=relevance_score,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    if source_ref is not None:
        discovery.update_running_avg_score(db, source_ref, relevance_score)

    discovery.register_discovered_sources(db, payload.raw_content)

    return schemas.ScoreResponse(item_id=item.id, summary=summary, relevance_score=relevance_score)


@app.post("/feedback", response_model=schemas.FeedbackResponse)
def submit_feedback(
    payload: schemas.FeedbackRequest,
    db: Session = Depends(get_db),
) -> schemas.FeedbackResponse:
    item = db.get(models.Item, payload.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {payload.item_id} not found")

    feedback = models.Feedback(
        item_id=item.id,
        user_id=settings.default_user_id,
        label=payload.label,
    )
    db.add(feedback)
    db.commit()

    return schemas.FeedbackResponse()


@app.post("/sources", response_model=schemas.SourceResponse, status_code=201)
def create_source(
    payload: schemas.SourceCreate,
    db: Session = Depends(get_db),
) -> models.Source:
    existing = db.query(models.Source).filter(models.Source.url == payload.url).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Source with url {payload.url!r} already exists")

    source = models.Source(
        url=payload.url,
        type=payload.type,
        status="kandidaat",
        discovery_method="manual",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
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
