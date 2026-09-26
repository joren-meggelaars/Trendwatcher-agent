from datetime import datetime, timezone

import pytest

from app import localtime, models
from app.config import settings


def test_a_stored_utc_time_is_shown_in_local_summer_and_winter_time():
    assert localtime.local_time(datetime(2026, 9, 25, 21, 39), "%H:%M") == "23:39"  # CEST, UTC+2
    assert localtime.local_time(datetime(2026, 1, 15, 21, 39), "%H:%M") == "22:39"  # CET, UTC+1


def test_the_date_can_change_because_of_the_zone():
    assert localtime.local_time(datetime(2026, 9, 25, 22, 30), "%d-%m-%Y") == "26-09-2026"


def test_timezone_aware_values_and_none_are_handled():
    assert localtime.local_time(datetime(2026, 9, 25, 21, 39, tzinfo=timezone.utc), "%H:%M") == "23:39"
    assert localtime.local_time(None) == ""


def test_the_zone_follows_the_setting_and_an_unknown_name_falls_back_to_utc(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "America/New_York")
    assert localtime.local_time(datetime(2026, 9, 25, 21, 39), "%H:%M") == "17:39"

    monkeypatch.setattr(settings, "timezone", "Not/AZone")
    assert localtime.local_time(datetime(2026, 9, 25, 21, 39), "%H:%M") == "21:39"


def test_the_items_page_shows_local_time(admin_client, db_session):
    db_session.add(
        models.Item(source="s", title="Stored at 21:39 UTC", url="https://example.com/a", raw_content="x",
                    created_at=datetime(2026, 9, 25, 21, 39))
    )
    db_session.commit()

    page = admin_client.get("/admin/items").text

    assert "2026-09-25 23:39" in page and "2026-09-25 21:39" not in page


def test_the_settings_page_says_which_zone_the_hour_is_in(admin_client):
    assert "tijd in Europe/Amsterdam" in admin_client.get("/admin/settings").text
