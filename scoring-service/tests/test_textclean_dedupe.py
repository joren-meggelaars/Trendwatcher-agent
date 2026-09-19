from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import models
from app.dedupe import Deduper, canonical_url, title_key
from app.textclean import clean_text
from scripts import trim_items


# --- clean_text ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Threat actors behind the &quot;Phantom Deal&quot; campaign", 'Threat actors behind the "Phantom Deal" campaign'),
        ("Large Enterprises Targeted in Fake Merger &amp; Acquisition Scams", "Large Enterprises Targeted in Fake Merger & Acquisition Scams"),
        ("Fake Merger &amp;amp; Acquisition", "Fake Merger & Acquisition"),  # encoded twice
        ("&amp;quot;Quoted&amp;quot;", '"Quoted"'),
        ("<p>The company plans to expand.</p> <p>More text.</p>", "The company plans to expand. More text."),
        ("&lt;p&gt;Escaped tags&lt;/p&gt;", "Escaped tags"),
        ('See <a href="https://example.com/x?a=1&amp;b=2">this link</a> now', "See this link now"),
        ("Plain text stays plain, R&D too, and a < b.", "Plain text stays plain, R&D too, and a < b."),
        ("  lots \n of\t whitespace  ", "lots of whitespace"),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_wordpress_footer_is_removed():
    raw = (
        '<p>The company plans to expand into continuous cybersecurity.</p> <p>The post <a href="https://www.securityweek.com/'
        'comp-ai-raises-34-million/">Comp AI Raises $34 Million for AI-Native Compliance</a> appeared first on '
        '<a href="https://www.securityweek.com">SecurityWeek</a>.</p>'
    )

    assert clean_text(raw) == "The company plans to expand into continuous cybersecurity."


def test_a_sentence_that_merely_contains_the_words_the_post_is_left_alone():
    text = "The post office announced it appeared strong this quarter."

    assert clean_text(text) == text  # no "appeared first on"


# --- canonical_url / title_key ------------------------------------------------


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://www.example.com/a/b/", "http://example.com/a/b"),
        ("https://example.com/a?utm_source=x&utm_medium=y", "https://example.com/a"),
        ("https://example.com/a#comments", "https://example.com/a"),
        ("https://Example.COM/a?id=5&fbclid=abc", "https://example.com/a?id=5"),
    ],
)
def test_same_page_urls_share_a_canonical_form(a, b):
    assert canonical_url(a) == canonical_url(b)


def test_different_pages_do_not_share_a_canonical_form():
    assert canonical_url("https://example.com/a?id=5") != canonical_url("https://example.com/a?id=6")
    assert canonical_url("https://example.com/a") != canonical_url("https://example.com/b")


def test_title_key_ignores_case_punctuation_and_entities():
    assert title_key("Fake Merger &amp; Acquisition Scams!") == title_key("fake merger & acquisition scams")


# --- Deduper ------------------------------------------------------------------


def _item(url, title, source="https://feed.example.com", source_id=None):
    return SimpleNamespace(url=url, title=title, source=source, source_id=source_id)


def test_deduper_flags_same_url_and_same_title_from_the_same_source():
    d = Deduper()

    assert d.is_duplicate(_item("https://x.com/a", "Article A")) is False
    assert d.is_duplicate(_item("https://x.com/a/?utm_source=n", "Something else")) is True  # same page
    assert d.is_duplicate(_item("https://x.com/other", "ARTICLE a!")) is True  # same title, same source
    assert d.is_duplicate(_item("https://x.com/b", "Article B")) is False


def test_the_same_title_from_a_different_source_is_a_different_item():
    d = Deduper()

    assert d.is_duplicate(_item("https://x.com/a", "Same title", source="https://one.example.com")) is False
    assert d.is_duplicate(_item("https://y.com/a", "Same title", source="https://two.example.com")) is False


def test_an_empty_title_never_makes_two_items_duplicates():
    d = Deduper()

    assert d.is_duplicate(_item("https://x.com/a", "")) is False
    assert d.is_duplicate(_item("https://x.com/b", "")) is False


# --- through the service -----------------------------------------------------


def _score(client, title, url=None, raw="Artikel over iets.", source="https://feed.example.com/rss"):
    resp = client.post(
        "/score",
        json={"source": source, "title": title, "url": url or f"https://example.com/{uuid4().hex}", "raw_content": raw},
    )
    assert resp.status_code == 200
    return resp.json()


def test_scoring_stores_clean_titles_and_summaries(client):
    item = _score(
        client,
        "Fake Merger &amp; Acquisition Scams",
        raw="<p>Threat actors behind the &quot;Phantom Deal&quot; campaign.</p> <p>The post X appeared first on Y.</p>",
    )

    assert item["summary"] == 'Threat actors behind the "Phantom Deal" campaign.'
    top = client.get("/items/top", params={"limit": 5}).json()
    assert top[0]["title"] == "Fake Merger & Acquisition Scams"


def test_the_same_title_from_the_same_source_with_a_new_url_is_not_stored_twice(client):
    first = _score(client, "Palo Alto acquires Globex", url="https://example.com/one")
    second = _score(client, "Palo Alto acquires Globex", url="https://example.com/two?utm_source=rss")

    assert second["item_id"] == first["item_id"]


def test_top_items_shows_a_stored_duplicate_once_and_cleans_old_rows(client, db_session):
    # Rows as they were stored before cleaning and the duplicate check existed.
    for i in range(2):
        db_session.add(
            models.Item(
                user_id="joren", source="https://www.darkreading.com/rss.xml",
                title="Large Enterprises Targeted in Fake Merger &amp; Acquisition Scams",
                url="https://www.darkreading.com/cyber-risk/fake-merger-scams",
                raw_content="x", summary="Actors behind the &quot;Phantom Deal&quot; campaign",
                relevance_score=0.5,
            )
        )
    db_session.commit()

    top = client.get("/items/top", params={"limit": 5, "category": "markt"}).json()

    assert len(top) == 1
    assert top[0]["title"] == "Large Enterprises Targeted in Fake Merger & Acquisition Scams"
    assert top[0]["summary"] == 'Actors behind the "Phantom Deal" campaign'


def test_dedupe_happens_before_the_limit(client, db_session):
    for i, title in enumerate(["Same story", "Same story", "Other story"]):
        db_session.add(
            models.Item(
                user_id="joren", source="https://feed.example.com", title=title,
                url=f"https://example.com/{i}", raw_content="x", summary="s",
                relevance_score=0.9 - i * 0.1,
            )
        )
    db_session.commit()

    top = client.get("/items/top", params={"limit": 2}).json()

    assert [i["title"] for i in top] == ["Same story", "Other story"]  # not "Same story" twice


def test_marking_an_item_digested_also_marks_its_stored_twin(client, db_session):
    ids = []
    for i in range(2):
        item = models.Item(
            user_id="joren", source="https://feed.example.com", title="Twin story",
            url="https://example.com/twin", raw_content="x", summary="s", relevance_score=0.5,
        )
        db_session.add(item)
        db_session.flush()
        ids.append(item.id)
    unrelated = models.Item(
        user_id="joren", source="https://feed.example.com", title="Unrelated",
        url="https://example.com/unrelated", raw_content="x", summary="s", relevance_score=0.4,
    )
    db_session.add(unrelated)
    db_session.commit()

    resp = client.post("/items/mark-digested", json={"item_ids": [ids[0]]})

    assert resp.json() == {"marked": 2}  # the mailed one and its twin
    db_session.expire_all()
    assert db_session.get(models.Item, ids[1]).digested_at is not None
    assert db_session.get(models.Item, unrelated.id).digested_at is None
    assert [i["title"] for i in client.get("/items/top", params={"undigested": True}).json()] == ["Unrelated"]


def test_recent_feedback_titles_are_plain_text(client):
    item = _score(client, "R&amp;D funding &amp; M&amp;A")
    client.post("/feedback", json={"item_id": item["item_id"], "label": "interessant"})

    titles = [i["title"] for i in client.get("/items/recent-feedback", params={"label": "interessant", "days": 30}).json()]

    assert titles == ["R&D funding & M&A"]


# --- trim_items collapses duplicates ---------------------------------------------


def test_trim_removes_stored_twins_even_when_everything_would_fit(db_session):
    for i in range(3):
        db_session.add(
            models.Item(
                user_id="joren", source="https://feed.example.com", title="Twin story",
                url="https://example.com/twin", raw_content="x", summary="s", relevance_score=0.5,
            )
        )
    db_session.add(
        models.Item(
            user_id="joren", source="https://feed.example.com", title="Other",
            url="https://example.com/other", raw_content="x", summary="s", relevance_score=0.5,
        )
    )
    db_session.commit()

    keep, delete = trim_items.select_ids_to_keep(db_session, 50)

    assert len(keep) == 2 and len(delete) == 2
    kept_titles = {db_session.get(models.Item, i).title for i in keep}
    assert kept_titles == {"Twin story", "Other"}
