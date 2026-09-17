import math

from sqlalchemy.orm import Session

from app import discovery, models
from app.embeddings import EmbeddingProvider


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def summarize(raw_content: str, max_chars: int = 280) -> str:
    """Naive truncation-based placeholder.

    No summarization model was specified for Fase 1 (only Voyage for
    embeddings), so this stays dependency-free until a later phase swaps in
    an LLM call — callers only depend on this function's signature.
    """
    text = " ".join(raw_content.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


def compute_relevance_score(db: Session, embedding: list[float], user_id: str) -> float:
    liked_items = (
        db.query(models.Item)
        .join(models.Feedback, models.Feedback.item_id == models.Item.id)
        .filter(
            models.Feedback.user_id == user_id,
            models.Feedback.label == "interessant",
            models.Item.embedding.is_not(None),
        )
        .all()
    )
    if not liked_items:
        return 0.5

    best_similarity = max(cosine_similarity(embedding, item.embedding) for item in liked_items)
    return max(0.0, min(1.0, (best_similarity + 1) / 2))


def score_and_store(
    db: Session,
    provider: EmbeddingProvider,
    *,
    source: str,
    title: str,
    url: str,
    raw_content: str,
    source_id: int | None,
    user_id: str,
) -> models.Item:
    """Shared implementation behind POST /score and the admin batch-add tool.

    Callers are responsible for any source_id validation appropriate to their
    context (e.g. POST /score returns 404 for an unknown id; batch-add never
    passes one) — this only does the scoring/storage/discovery side effects.
    """
    summary = summarize(raw_content)
    embedding = provider.embed(raw_content)
    relevance_score = compute_relevance_score(db, embedding, user_id=user_id)

    item = models.Item(
        user_id=user_id,
        source=source,
        source_id=source_id,
        title=title,
        url=url,
        raw_content=raw_content,
        summary=summary,
        embedding=embedding,
        relevance_score=relevance_score,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    if source_id is not None:
        source_ref = db.get(models.Source, source_id)
        if source_ref is not None:
            discovery.update_running_avg_score(db, source_ref, relevance_score)

    discovery.register_discovered_sources(db, raw_content)

    return item
