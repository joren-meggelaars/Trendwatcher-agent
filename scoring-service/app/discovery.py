import re
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app import models
from app.config import settings

# Matches plain http(s) URLs whether they sit inside an HTML href="..." attribute
# or as bare text — good enough to register candidate domains without needing an
# HTML parser dependency (raw_content is not fetched or rendered).
_URL_RE = re.compile(r'https?://[^\s"\'<>]+')


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

    if source.status == "kandidaat":
        recent_items = (
            db.query(models.Item)
            .filter(models.Item.source_id == source_id, models.Item.relevance_score.is_not(None))
            .order_by(models.Item.id.desc())
            .limit(settings.source_activation_window)
            .all()
        )
        high_score_count = sum(
            1
            for item in recent_items
            if item.relevance_score > settings.source_activation_score_threshold
        )
        if high_score_count >= settings.source_activation_min_high_score:
            source.status = "actief"

    elif source.status == "actief":
        recent_items = (
            db.query(models.Item)
            .filter(models.Item.source_id == source_id)
            .order_by(models.Item.id.desc())
            .limit(settings.source_deactivation_window)
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
            and source.running_avg_score < settings.source_deactivation_avg_score_threshold
        )
        if negative_count >= settings.source_deactivation_min_negative or avg_too_low:
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
