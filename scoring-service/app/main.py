from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
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
    summary = summarize(payload.raw_content)
    embedding = provider.embed(payload.raw_content)
    relevance_score = compute_relevance_score(db, embedding, user_id=settings.default_user_id)

    item = models.Item(
        user_id=settings.default_user_id,
        source=payload.source,
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
