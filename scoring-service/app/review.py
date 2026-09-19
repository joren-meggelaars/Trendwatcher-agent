"""What to offer for review on /admin/review: articles to give a 👍/👎 on,
independent of any digest, so the scoring gets initial input quickly.

An item is offered when it is stored, scored, recent (REVIEW_LOOKBACK_DAYS),
has no feedback from you yet (a skip counts as an answer), and is not a
duplicate of something already handled or offered. The queue is spread over
sources — one item per source in turn, newest first — so a first round of
thumbs covers many feeds and topics instead of ten posts from the loudest one.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, load_only

from app import dedupe, models, scoring
from app.textclean import clean_text

REVIEW_LOOKBACK_DAYS = 30
SKIPPED = "overgeslagen"  # stored like feedback, but never used for scoring
THUMBS = ("interessant", "niet_interessant")

_LIGHT = load_only(
    models.Item.id,
    models.Item.title,
    models.Item.url,
    models.Item.summary,
    models.Item.relevance_score,
    models.Item.source,
    models.Item.source_id,
    models.Item.created_at,
)


@dataclass(frozen=True)
class Candidate:
    item: models.Item
    category: str


@dataclass(frozen=True)
class Overview:
    queue: list[Candidate]  # what to show now, for the requested category
    waiting: dict[str, int]  # items still to review per category
    given: dict[str, dict[str, int]]  # thumbs given so far per category


def _candidates(db: Session, user_id: str) -> list[Candidate]:
    """All reviewable items, spread over sources (see module docstring)."""
    handled_ids = {
        row[0] for row in db.query(models.Feedback.item_id).filter(models.Feedback.user_id == user_id).distinct().all()
    }

    # Twins of what was already answered must not come back as "new" articles.
    deduper = dedupe.Deduper()
    if handled_ids:
        for item in db.query(models.Item).options(_LIGHT).filter(models.Item.id.in_(handled_ids)).all():
            deduper.is_duplicate(item)

    cutoff = datetime.now(timezone.utc) - timedelta(days=REVIEW_LOOKBACK_DAYS)
    stored = (
        db.query(models.Item)
        .options(_LIGHT)
        .filter(
            models.Item.user_id == user_id,
            models.Item.relevance_score.is_not(None),
            models.Item.created_at >= cutoff,
        )
        .order_by(models.Item.id.desc())
        .all()
    )

    by_source: dict[object, list[Candidate]] = defaultdict(list)
    for item in stored:  # newest first
        if item.id in handled_ids or deduper.is_duplicate(item):
            continue
        by_source[item.source_id or item.source].append(Candidate(item, scoring.item_category(item)))

    # Round-robin: the newest of every source, then the second newest, ...
    # Sources with the newest item first.
    groups = sorted(by_source.values(), key=lambda group: -group[0].item.id)
    ordered: list[Candidate] = []
    for depth in range(max((len(g) for g in groups), default=0)):
        ordered.extend(group[depth] for group in groups if depth < len(group))
    return ordered


def overview(db: Session, user_id: str, category: str | None, limit: int = 10) -> Overview:
    """`category` is "markt", "nieuws" or None for both."""
    candidates = _candidates(db, user_id)
    waiting = Counter(c.category for c in candidates)
    shown = [c for c in candidates if category is None or c.category == category][:limit]

    given: dict[str, dict[str, int]] = {cat: {label: 0 for label in THUMBS} for cat in ("markt", "nieuws")}
    rows = (
        db.query(models.Item, models.Feedback.label)
        .join(models.Feedback, models.Feedback.item_id == models.Item.id)
        .options(_LIGHT)
        .filter(models.Feedback.user_id == user_id, models.Feedback.label.in_(THUMBS))
        .all()
    )
    seen: set[tuple[int, str]] = set()
    for item, label in rows:
        if (item.id, label) in seen:
            continue  # the same item thumbed twice counts once
        seen.add((item.id, label))
        given[scoring.item_category(item)][label] += 1

    return Overview(shown, {"markt": waiting["markt"], "nieuws": waiting["nieuws"]}, given)


def card_text(item: models.Item) -> tuple[str, str]:
    """Plain title and summary for display (older rows still hold HTML)."""
    return clean_text(item.title) or item.title, clean_text(item.summary)
