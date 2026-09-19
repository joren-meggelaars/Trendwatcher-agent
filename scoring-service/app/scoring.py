import math

from sqlalchemy.orm import Session

from app import classification, discovery, models
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


def _feedback_items(db: Session, user_id: str, label: str, category: str) -> list[models.Item]:
    """Items the user gave `label` feedback on, limited to one category.

    Markt and nieuws are trained separately: a 👍 on a funding round says
    nothing about which vulnerability write-ups you care about, and vice versa.
    """
    items = (
        db.query(models.Item)
        .join(models.Feedback, models.Feedback.item_id == models.Item.id)
        .filter(
            models.Feedback.user_id == user_id,
            models.Feedback.label == label,
            models.Item.embedding.is_not(None),
        )
        .all()
    )
    return [i for i in items if classification.classify(i.title, i.summary or "") == category]


def _closeness(embedding: list[float], items: list[models.Item]) -> float:
    """0..1 closeness to the nearest of `items`; a neutral 0.5 without any."""
    if not items:
        return 0.5
    best_similarity = max(cosine_similarity(embedding, item.embedding) for item in items)
    return max(0.0, min(1.0, (best_similarity + 1) / 2))


def compute_relevance_score(db: Session, embedding: list[float], user_id: str, category: str) -> float:
    """Closeness to the nearest 👍 item of the same category, pulled down by
    closeness to the nearest 👎 item of that category.

    score = 0.5 + closeness(liked) - closeness(disliked), each side neutral
    (0.5) when there is no feedback of that kind yet. So with only 👍 this is
    exactly the closeness to the liked items, with no feedback it is 0.5, and
    an item that resembles something you rejected drops below 0.5.
    """
    liked = _closeness(embedding, _feedback_items(db, user_id, "interessant", category))
    disliked = _closeness(embedding, _feedback_items(db, user_id, "niet_interessant", category))
    return max(0.0, min(1.0, 0.5 + liked - disliked))


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

    Idempotent per (user, url): an already-stored URL returns the existing
    item untouched — no second row, no embedding call (free-tier Voyage is
    3 req/min). This keeps a lost scheduler "seen" cache (or re-adding a link
    in the admin GUI) from piling up duplicates.
    """
    existing = (
        db.query(models.Item)
        .filter(models.Item.user_id == user_id, models.Item.url == url)
        .order_by(models.Item.id)
        .first()
    )
    if existing is not None:
        return existing

    summary = summarize(raw_content)
    embedding = provider.embed(raw_content)
    relevance_score = compute_relevance_score(
        db, embedding, user_id=user_id, category=classification.classify(title, summary)
    )

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
