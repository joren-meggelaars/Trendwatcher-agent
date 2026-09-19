from collections import Counter
from typing import get_args
from urllib.parse import urlparse

from app import discovery, models, schemas
from scripts import seed_sources
from scripts.seed_sources import SEED_SOURCES, SeedSource

_CATEGORIES = set(get_args(schemas.SourceCategory))
_STATUSES = set(get_args(schemas.SourceStatus))


# --- the list itself --------------------------------------------------------


def test_seed_urls_are_unique():
    duplicates = [url for url, n in Counter(s.url for s in SEED_SOURCES).items() if n > 1]
    assert duplicates == []


def test_every_entry_has_a_known_status_and_category():
    for entry in SEED_SOURCES:
        assert entry.status in _STATUSES, entry.url
        assert entry.category is None or entry.category in _CATEGORIES, entry.url


def test_only_the_original_reading_list_may_lack_a_category():
    # Newly added sources always carry a category; the first 20 keep theirs
    # empty unless the url was named explicitly.
    for entry in SEED_SOURCES[20:]:
        assert entry.category is not None, entry.url


def test_every_disabled_source_says_why():
    disabled = [s for s in SEED_SOURCES if s.status == "gedeactiveerd"]
    assert disabled, "expected sources without a working feed to be seeded as disabled"
    for entry in disabled:
        assert entry.notes and len(entry.notes) > 30, entry.url


def test_feed_urls_are_http_and_never_the_discontinued_cisa_feeds():
    for entry in SEED_SOURCES:
        assert urlparse(entry.url).scheme in ("http", "https"), entry.url
        assert "cisa.gov" not in entry.url, entry.url


def test_original_sources_are_seeded_as_kandidaat_like_before():
    for entry in SEED_SOURCES[:20]:
        assert entry.status == "kandidaat", entry.url


# --- upsert behaviour -------------------------------------------------------


def test_seed_creates_everything_with_the_given_status_category_and_notes(db_session):
    report = seed_sources.seed(db_session)

    assert len(report.created) == len(SEED_SOURCES)
    assert report.category_set == [] and report.unchanged == []
    by_url = {s.url: s for s in db_session.query(models.Source).all()}
    assert len(by_url) == len(SEED_SOURCES)
    for entry in SEED_SOURCES:
        source = by_url[entry.url]
        assert (source.status, source.category, source.notes) == (entry.status, entry.category, entry.notes)
        assert source.discovery_method == "seed"


def test_seed_twice_is_a_no_op(db_session):
    seed_sources.seed(db_session)

    second = seed_sources.seed(db_session)

    assert second.created == [] and second.category_set == []
    assert len(second.unchanged) == len(SEED_SOURCES)
    assert db_session.query(models.Source).count() == len(SEED_SOURCES)


def test_seed_never_changes_status_type_or_notes_of_an_existing_source(db_session):
    url = "https://www.darkreading.com/rss.xml"
    existing = discovery.create_source(db_session, url, "custom-type", "manual", notes="mijn eigen notitie")
    existing.status = "actief"  # e.g. activated by hand in the admin GUI
    db_session.commit()

    report = seed_sources.seed(db_session, [SeedSource(url, "rss", "news", "gedeactiveerd", "andere notitie")])

    refreshed = db_session.query(models.Source).filter_by(url=url).one()
    assert (refreshed.status, refreshed.type, refreshed.notes) == ("actief", "custom-type", "mijn eigen notitie")
    assert refreshed.discovery_method == "manual"
    # The one thing it may do: fill the empty category.
    assert refreshed.category == "news"
    assert [e.url for e in report.category_set] == [url]


def test_seed_never_overwrites_an_existing_category(db_session):
    url = "https://example.com/feed"
    discovery.create_source(db_session, url, "rss", category="vendor_product")

    report = seed_sources.seed(db_session, [SeedSource(url, "rss", "news")])

    assert db_session.query(models.Source).filter_by(url=url).one().category == "vendor_product"
    assert [e.url for e in report.unchanged] == [url]


def test_disabled_sources_are_not_picked_up_by_the_scheduler_but_active_ones_are(client, db_session):
    seed_sources.seed(db_session)

    active_urls = {s["url"] for s in client.get("/sources", params={"status": "actief"}).json()}

    assert {e.url for e in SEED_SOURCES if e.status == "actief"} == active_urls
    disabled = client.get("/sources", params={"status": "gedeactiveerd"}).json()
    assert all(s["notes"] for s in disabled)


# --- shared create_source path ---------------------------------------------


def test_create_source_rejects_unknown_category_and_status(db_session):
    import pytest

    with pytest.raises(ValueError, match="category"):
        discovery.create_source(db_session, "https://a.example.com", "rss", category="sport")
    with pytest.raises(ValueError, match="status"):
        discovery.create_source(db_session, "https://b.example.com", "rss", status="enabled")


def test_api_accepts_and_returns_category(client):
    resp = client.post(
        "/sources", json={"url": "https://c.example.com/feed", "type": "rss", "category": "market_ma"}
    )
    assert resp.status_code == 201
    assert resp.json()["category"] == "market_ma"
    assert resp.json()["status"] == "kandidaat"  # the API never creates active sources
    assert resp.json()["notes"] is None

    assert client.post(
        "/sources", json={"url": "https://d.example.com/feed", "type": "rss", "category": "sport"}
    ).status_code == 422


def test_admin_sources_page_shows_category_and_notes(admin_client, db_session):
    discovery.create_source(
        db_session, "https://e.example.com/feed", "rss", category="threat_research",
        notes="Waarom dit een notitie heeft", status="gedeactiveerd",
    )

    resp = admin_client.get("/admin/sources")

    assert resp.status_code == 200
    assert "threat_research" in resp.text
    assert "Waarom dit een notitie heeft" in resp.text
