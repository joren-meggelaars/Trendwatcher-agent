from typing import Iterable

from sqlalchemy.orm import Session

from app import models

DAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
VALID_DAYS = set(DAY_ORDER)
DEFAULT_DIGEST_DAYS = "mon,tue,wed,thu,fri"


def normalize_digest_days(days: Iterable[str]) -> str:
    """Canonical comma-separated, Mon-first, de-duplicated day-of-week string (APScheduler cron
    day_of_week syntax). Raises ValueError for an unknown day or an empty selection."""
    picked = {d.strip().lower() for d in days if d.strip()}
    unknown = picked - VALID_DAYS
    if unknown:
        raise ValueError(f"Unknown day(s): {sorted(unknown)}")
    if not picked:
        raise ValueError("At least one day must be selected")
    return ",".join(day for day in DAY_ORDER if day in picked)


def get_digest_settings(db: Session) -> models.DigestSettings:
    """Get-or-create the single settings row (id=1)."""
    row = db.get(models.DigestSettings, 1)
    if row is None:
        row = models.DigestSettings(id=1, digest_days=DEFAULT_DIGEST_DAYS)
        db.add(row)
        db.commit()
        db.refresh(row)
    elif row.digest_days is None:  # a row saved before this column existed
        row.digest_days = DEFAULT_DIGEST_DAYS
        db.commit()
        db.refresh(row)
    return row


def update_digest_settings(db: Session, digest_hour: int, digest_top_n: int, digest_days: str) -> models.DigestSettings:
    row = get_digest_settings(db)
    row.digest_hour = digest_hour
    row.digest_top_n = digest_top_n
    row.digest_days = digest_days
    db.commit()
    db.refresh(row)
    return row
