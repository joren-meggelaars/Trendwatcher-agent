import math

from sqlalchemy.orm import Session

from app import models


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
