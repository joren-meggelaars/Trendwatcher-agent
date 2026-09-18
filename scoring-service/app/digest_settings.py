from sqlalchemy.orm import Session

from app import models


def get_digest_settings(db: Session) -> models.DigestSettings:
    """Get-or-create the single settings row (id=1)."""
    row = db.get(models.DigestSettings, 1)
    if row is None:
        row = models.DigestSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_digest_settings(db: Session, digest_hour: int, digest_top_n: int) -> models.DigestSettings:
    row = get_digest_settings(db)
    row.digest_hour = digest_hour
    row.digest_top_n = digest_top_n
    db.commit()
    db.refresh(row)
    return row
