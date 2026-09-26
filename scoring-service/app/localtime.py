"""Times are stored in UTC (naive datetimes in the database); people read them in
local time. Every time shown in the GUI goes through the `local_time` filter."""

import logging
from datetime import datetime, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings

log = logging.getLogger(__name__)


@lru_cache
def _load(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Onbekende tijdzone TIMEZONE=%r, tijden worden in UTC getoond.", name)
        return timezone.utc


def zone() -> tzinfo:
    return _load(settings.timezone)


def to_local(value: datetime) -> datetime:
    if value.tzinfo is None:  # the database returns naive datetimes: they are UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone())


def local_time(value: datetime | None, fmt: str = "%d-%m-%Y %H:%M") -> str:
    """Jinja filter: {{ item.created_at|local_time("%H:%M") }}"""
    return "" if value is None else to_local(value).strftime(fmt)
