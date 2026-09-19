import math
import operator
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app import classification, discovery, models
from app.embeddings import EmbeddingProvider
from app.textclean import clean_text


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
    text = clean_text(raw_content)  # plain text: no tags, entities decoded, no WordPress footer
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
    return [i for i in items if item_category(i) == category]


def item_category(item: models.Item) -> str:
    """markt/nieuws of a stored item, from its (cleaned) title and summary."""
    return classification.classify(clean_text(item.title), clean_text(item.summary))


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


# --- rescoring: let already-stored items follow new feedback ---------------------


def _unit(embedding: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(x * x for x in embedding))
    return [x / norm for x in embedding] if norm else None


def _closeness_unit(unit: list[float], references: list[list[float]]) -> float:
    """Same as _closeness, on vectors that are already unit length (a plain
    dot product is then the cosine): what makes rescoring hundreds of items
    against dozens of thumbs fast enough without extra dependencies."""
    if not references:
        return 0.5
    best = max(sum(map(operator.mul, unit, ref)) for ref in references)
    return max(0.0, min(1.0, (best + 1) / 2))


# What the last rescore saw, so an unchanged set of thumbs is not recomputed
# (the scheduler asks after every ingest run). Per process; a restart just
# recomputes once.
_last_rescore_signature: tuple | None = None


def rescore_recent(db: Session, user_id: str, days: int = 30, *, force: bool = False) -> int:
    """Recompute the relevance score of items stored in the last `days` days
    against the *current* 👍/👎, so feedback given today also moves items that
    are already stored (a score is otherwise fixed at the moment it was
    computed). Uses the stored embeddings: no Voyage requests.

    Returns how many items got a different score. Nothing to do without any
    feedback, or when the thumbs did not change since the last call.
    """
    global _last_rescore_signature

    signature = (
        db.query(func.count(models.Feedback.id), func.max(models.Feedback.id))
        .filter(models.Feedback.user_id == user_id, models.Feedback.label.in_(("interessant", "niet_interessant")))
        .one()
    )
    signature = (tuple(signature), days)
    if not force and signature == _last_rescore_signature:
        return 0
    if not signature[0][0]:
        return 0

    references: dict[tuple[str, str], list[list[float]]] = {}
    for category in ("markt", "nieuws"):
        for label in ("interessant", "niet_interessant"):
            references[(category, label)] = [
                unit
                for item in _feedback_items(db, user_id, label, category)
                if (unit := _unit(item.embedding)) is not None
            ]

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items = (
        db.query(models.Item)
        .filter(
            models.Item.user_id == user_id,
            models.Item.embedding.is_not(None),
            models.Item.created_at >= cutoff,
        )
        .all()
    )
    updated = 0
    touched_sources: set[int] = set()
    for item in items:
        unit = _unit(item.embedding)
        if unit is None:
            continue
        category = item_category(item)
        liked = _closeness_unit(unit, references[(category, "interessant")])
        disliked = _closeness_unit(unit, references[(category, "niet_interessant")])
        score = max(0.0, min(1.0, 0.5 + liked - disliked))
        if item.relevance_score is None or abs(item.relevance_score - score) > 1e-9:
            item.relevance_score = score
            updated += 1
            if item.source_id is not None:
                touched_sources.add(item.source_id)
    db.commit()

    # A source's running average is what the "krimp" rule looks at: keep it in
    # line with the rescored items.
    for source_id in touched_sources:
        source = db.get(models.Source, source_id)
        if source is not None:
            source.running_avg_score = (
                db.query(func.avg(models.Item.relevance_score))
                .filter(models.Item.source_id == source_id, models.Item.relevance_score.is_not(None))
                .scalar()
            )
    db.commit()

    _last_rescore_signature = signature
    return updated


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

    Idempotent per (user, url) and per (user, source, title): an article that
    is already stored returns the existing item untouched — no second row, no
    embedding call (free-tier Voyage is 3 req/min). This keeps a lost
    scheduler "seen" cache, a feed that republishes an article under another
    URL, or re-adding a link in the admin GUI from piling up duplicates.
    """
    title = clean_text(title) or title  # feed titles arrive with HTML entities
    existing = (
        db.query(models.Item)
        .filter(
            models.Item.user_id == user_id,
            or_(models.Item.url == url, and_(models.Item.source == source, models.Item.title == title)),
        )
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
