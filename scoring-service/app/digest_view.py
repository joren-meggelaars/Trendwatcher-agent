"""Data for the digest pages: /admin/digest/<id> (one digest, to vote on) and
/admin/digests (the archive).

A digest page shows the digest exactly as it was mailed (the Digest and
DigestItem rows written when it was put together), with your current 👍/👎
per item, so the whole digest can be worked through on one page.
"""

from sqlalchemy.orm import Session

from app import models
from app.review import SKIPPED, THUMBS
from app.textclean import clean_text

SECTIONS = (("markt", "Marktontwikkeling"), ("nieuws", "Nieuws"))

# The GUI speaks like/dislike; the database stores the Dutch labels.
_LABEL_OF_ACTION = {"like": "interessant", "dislike": "niet_interessant"}
_ACTION_OF_LABEL = {label: action for action, label in _LABEL_OF_ACTION.items()}


def current_votes(db: Session, user_id: str, item_ids: list[int]) -> dict[int, str]:
    """{item_id: "like" | "dislike"} for the items you have a thumb on."""
    if not item_ids:
        return {}
    rows = (
        db.query(models.Feedback.item_id, models.Feedback.label)
        .filter(
            models.Feedback.user_id == user_id,
            models.Feedback.item_id.in_(item_ids),
            models.Feedback.label.in_(THUMBS),
        )
        .order_by(models.Feedback.id)  # the newest thumb wins
        .all()
    )
    return {item_id: _ACTION_OF_LABEL[label] for item_id, label in rows}


def set_vote(db: Session, user_id: str, item_id: int, action: str) -> tuple[str | None, str | None]:
    """Make `action` ("like", "dislike" or "clear") your one answer for the
    item, replacing any earlier thumb or skip. Returns (previous, new) as
    like/dislike/None, so the page can offer an exact undo."""
    rows = (
        db.query(models.Feedback)
        .filter(models.Feedback.user_id == user_id, models.Feedback.item_id == item_id)
        .order_by(models.Feedback.id)
        .all()
    )
    previous = None
    for row in rows:
        if row.label in _ACTION_OF_LABEL:
            previous = _ACTION_OF_LABEL[row.label]
        if row.label in THUMBS or row.label == SKIPPED:
            db.delete(row)

    new = action if action in _LABEL_OF_ACTION else None
    if new is not None:
        db.add(models.Feedback(item_id=item_id, user_id=user_id, label=_LABEL_OF_ACTION[new]))
    db.commit()
    return previous, new


def _card(entry: models.DigestItem, votes: dict[int, str], source_label) -> dict:
    item = entry.item
    return {
        "id": item.id,
        "title": clean_text(item.title) or item.title,
        "summary": clean_text(item.summary),
        "url": item.url,
        "source": source_label(item.source),
        "category": entry.category,
        "score": item.relevance_score,
        "vote": votes.get(item.id),
    }


def digest_page(db: Session, digest_id: int, user_id: str, source_label) -> dict | None:
    digest = db.get(models.Digest, digest_id)
    if digest is None:
        return None
    entries = list(digest.entries)
    votes = current_votes(db, user_id, [e.item_id for e in entries])

    sections = []
    for category, heading in SECTIONS:
        cards = [_card(e, votes, source_label) for e in entries if e.category == category]
        sections.append({"category": category, "heading": heading, "cards": cards})
    return {
        "digest": digest,
        "sections": sections,
        "total": len(entries),
        "voted": sum(1 for e in entries if e.item_id in votes),
    }


def archive(db: Session, user_id: str, limit: int = 60) -> list[dict]:
    digests = db.query(models.Digest).order_by(models.Digest.id.desc()).limit(limit).all()
    item_ids = [e.item_id for d in digests for e in d.entries]
    votes = current_votes(db, user_id, item_ids)
    rows = []
    for d in digests:
        entries = list(d.entries)
        rows.append(
            {
                "id": d.id,
                "created": d.created_at,
                "kind": d.kind,
                "mailed": d.mailed,
                "markt": sum(1 for e in entries if e.category == "markt"),
                "nieuws": sum(1 for e in entries if e.category == "nieuws"),
                "total": len(entries),
                "voted": sum(1 for e in entries if e.item_id in votes),
            }
        )
    return rows


def latest_digest_id(db: Session) -> int | None:
    row = db.query(models.Digest.id).order_by(models.Digest.id.desc()).first()
    return row[0] if row else None


def latest_digest_containing(db: Session, item_id: int) -> int | None:
    """The newest digest that has this item — where a 👍/👎 link from a mail
    belongs. None for items that were mailed before digests were recorded."""
    row = (
        db.query(models.DigestItem.digest_id)
        .filter(models.DigestItem.item_id == item_id)
        .order_by(models.DigestItem.digest_id.desc())
        .first()
    )
    return row[0] if row else None


def action_for(value: str) -> str | None:
    """like/dislike from either spelling (the old mail links use the Dutch labels)."""
    if value in _LABEL_OF_ACTION:
        return value
    return _ACTION_OF_LABEL.get(value)
