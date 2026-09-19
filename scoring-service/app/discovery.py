import re
from typing import get_args
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app import models, runtime_settings, schemas

# Matches plain http(s) URLs whether they sit inside an HTML href="..." attribute
# or as bare text — good enough to register candidate domains without needing an
# HTML parser dependency (raw_content is not fetched or rendered).
_URL_RE = re.compile(r'https?://[^\s"\'<>]+')


_STATUSES = get_args(schemas.SourceStatus)
_CATEGORIES = get_args(schemas.SourceCategory)


def create_source(
    db: Session,
    url: str,
    type_: str,
    discovery_method: str = "manual",
    *,
    category: str | None = None,
    notes: str | None = None,
    status: str = "kandidaat",
) -> models.Source | None:
    """Shared implementation behind POST /sources, the admin sources form and
    the seed script.

    Returns None if a Source with this url already exists — callers decide
    how to surface that (409 for the JSON API, a no-op redirect for the GUI).
    `status` defaults to "kandidaat" (the normal instroom path); only the seed
    script passes anything else, for sources whose feed it has verified itself.
    """
    if status not in _STATUSES:
        raise ValueError(f"Unknown source status {status!r}")
    if category is not None and category not in _CATEGORIES:
        raise ValueError(f"Unknown source category {category!r}")

    existing = db.query(models.Source).filter(models.Source.url == url).first()
    if existing is not None:
        return None

    source = models.Source(
        url=url,
        type=type_,
        status=status,
        discovery_method=discovery_method,
        category=category,
        notes=notes,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def upsert_seed_source(
    db: Session,
    url: str,
    type_: str,
    *,
    category: str | None = None,
    status: str = "kandidaat",
    notes: str | None = None,
    discovery_method: str = "seed",
) -> tuple[models.Source, str]:
    """Idempotent seed step, keyed on the feed url. Returns (source, outcome):

    - "created":      the url was new; created with the given status/category/notes.
    - "category_set": the url exists without a category and one was given; only
                      that empty category is filled in.
    - "unchanged":    the url exists; nothing was touched.

    An existing source's status, type and notes are never changed — whatever
    an admin or the instroom/krimp logic decided since seeding wins.
    """
    source = create_source(
        db, url, type_, discovery_method, category=category, notes=notes, status=status
    )
    if source is not None:
        return source, "created"

    existing = db.query(models.Source).filter(models.Source.url == url).one()
    if existing.category is None and category is not None:
        existing.category = category
        db.commit()
        db.refresh(existing)
        return existing, "category_set"
    return existing, "unchanged"


def extract_links(raw_content: str) -> list[str]:
    return _URL_RE.findall(raw_content)


def extract_domain(url: str) -> str | None:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


def register_discovered_sources(db: Session, raw_content: str) -> None:
    """Register every not-yet-known outbound domain as a candidate Source.

    No request is made to the linked pages themselves — registering the
    domain as a "kandidaat" is enough for Fase 1.
    """
    domains = {extract_domain(link) for link in extract_links(raw_content)}
    domains.discard(None)
    if not domains:
        return

    existing = {
        row[0]
        for row in db.query(models.Source.url).filter(models.Source.url.in_(domains)).all()
    }
    for domain in domains - existing:
        db.add(
            models.Source(
                url=domain,
                type="unknown",
                status="kandidaat",
                discovery_method="link_following",
            )
        )
    db.commit()


def update_running_avg_score(db: Session, source: models.Source, new_score: float) -> None:
    """Incrementally update running_avg_score without re-averaging history."""
    count = (
        db.query(models.Item)
        .filter(models.Item.source_id == source.id, models.Item.relevance_score.is_not(None))
        .count()
    )
    if source.running_avg_score is None or count <= 1:
        source.running_avg_score = new_score
    else:
        source.running_avg_score += (new_score - source.running_avg_score) / count
    db.commit()
    db.refresh(source)


def evaluate_source(db: Session, source_id: int) -> models.Source | None:
    source = db.get(models.Source, source_id)
    if source is None:
        return None

    # Thresholds: a value set in /admin/config wins over .env (see app/runtime_settings.py).
    if source.status == "kandidaat":
        recent_items = (
            db.query(models.Item)
            .filter(models.Item.source_id == source_id, models.Item.relevance_score.is_not(None))
            .order_by(models.Item.id.desc())
            .limit(runtime_settings.get(db, "SOURCE_ACTIVATION_WINDOW"))
            .all()
        )
        score_threshold = runtime_settings.get(db, "SOURCE_ACTIVATION_SCORE_THRESHOLD")
        high_score_count = sum(1 for item in recent_items if item.relevance_score > score_threshold)
        if high_score_count >= runtime_settings.get(db, "SOURCE_ACTIVATION_MIN_HIGH_SCORE"):
            source.status = "actief"

    elif source.status == "actief":
        recent_items = (
            db.query(models.Item)
            .filter(models.Item.source_id == source_id)
            .order_by(models.Item.id.desc())
            .limit(runtime_settings.get(db, "SOURCE_DEACTIVATION_WINDOW"))
            .all()
        )
        item_ids = [item.id for item in recent_items]
        negative_count = 0
        if item_ids:
            negative_count = (
                db.query(models.Feedback.item_id)
                .filter(
                    models.Feedback.item_id.in_(item_ids),
                    models.Feedback.label == "niet_interessant",
                )
                .distinct()
                .count()
            )

        avg_too_low = (
            source.running_avg_score is not None
            and source.running_avg_score < runtime_settings.get(db, "SOURCE_DEACTIVATION_AVG_SCORE_THRESHOLD")
        )
        if negative_count >= runtime_settings.get(db, "SOURCE_DEACTIVATION_MIN_NEGATIVE") or avg_too_low:
            source.status = "gedeactiveerd"

    db.commit()
    db.refresh(source)
    return source


def evaluate_all_sources(db: Session) -> list[dict]:
    """Run evaluate_source over every non-final source, return only the changes."""
    sources = (
        db.query(models.Source)
        .filter(models.Source.status.in_(["kandidaat", "actief"]))
        .all()
    )
    changes = []
    for source in sources:
        old_status = source.status
        updated = evaluate_source(db, source.id)
        if updated is not None and updated.status != old_status:
            changes.append(
                {
                    "source_id": updated.id,
                    "url": updated.url,
                    "old_status": old_status,
                    "new_status": updated.status,
                }
            )
    return changes
