from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import models, review, scoring
from app.config import settings

USER = settings.default_user_id
MARKET_TITLE = "Zscaler launches new AI-powered SSE platform"
NEWS_TITLE = "Critical flaw in Cisco ISE exploited in attacks"


def _item(db_session, title, *, source="https://feed-a.example.com", source_id=None, url=None, days_old=0, score=0.5):
    item = models.Item(
        user_id=USER, source=source, source_id=source_id, title=title,
        url=url or f"https://example.com/{uuid4().hex}", raw_content="x", summary="Samenvatting",
        relevance_score=score, created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
    )
    db_session.add(item)
    db_session.commit()
    return item


def _feedback(db_session, item, label):
    fb = models.Feedback(item_id=item.id, user_id=USER, label=label)
    db_session.add(fb)
    db_session.commit()
    return fb


def _titles(view):
    return [c.item.title for c in view.queue]


# --- what is offered --------------------------------------------------------------


def test_only_unanswered_recent_scored_items_are_offered(db_session):
    fresh = _item(db_session, "Fresh article")
    answered = _item(db_session, "Already thumbed")
    skipped = _item(db_session, "Already skipped")
    old = _item(db_session, "Too old", days_old=90)
    unscored = _item(db_session, "Not scored yet")
    unscored.relevance_score = None
    db_session.commit()
    _feedback(db_session, answered, "interessant")
    _feedback(db_session, skipped, review.SKIPPED)

    view = review.overview(db_session, USER, None)

    assert _titles(view) == ["Fresh article"]


def test_a_twin_of_an_answered_article_is_not_offered_again(db_session):
    answered = _item(db_session, "Same story", url="https://example.com/story")
    _item(db_session, "Same story", url="https://example.com/story?utm_source=rss")  # stored twice
    _item(db_session, "Another story")
    _feedback(db_session, answered, "niet_interessant")

    assert _titles(review.overview(db_session, USER, None)) == ["Another story"]


def test_duplicates_among_the_candidates_are_offered_once(db_session):
    _item(db_session, "Same story", url="https://example.com/story")
    _item(db_session, "Same story", url="https://example.com/story")

    assert _titles(review.overview(db_session, USER, None)) == ["Same story"]


def test_the_queue_is_spread_over_sources_newest_first(db_session):
    for i in range(4):
        _item(db_session, f"loud {i}", source="https://loud.example.com")
    _item(db_session, "quiet 0", source="https://quiet.example.com")
    _item(db_session, "other 0", source="https://other.example.com")

    titles = _titles(review.overview(db_session, USER, None, limit=10))

    # one per source in turn: not four posts from the loud one first
    assert titles[:3] == ["other 0", "quiet 0", "loud 3"]
    assert titles[3:] == ["loud 2", "loud 1", "loud 0"]


def test_category_filter_limit_and_waiting_counts(db_session):
    for i in range(3):
        _item(db_session, f"{MARKET_TITLE} {i}")
    _item(db_session, NEWS_TITLE)

    markt = review.overview(db_session, USER, "markt", limit=2)
    nieuws = review.overview(db_session, USER, "nieuws")

    assert len(markt.queue) == 2 and all(c.category == "markt" for c in markt.queue)
    assert [c.category for c in nieuws.queue] == ["nieuws"]
    assert markt.waiting == {"markt": 3, "nieuws": 1}


def test_given_counts_thumbs_per_category_and_ignores_skips(db_session):
    _feedback(db_session, _item(db_session, MARKET_TITLE), "interessant")
    _feedback(db_session, _item(db_session, MARKET_TITLE + " 2"), "niet_interessant")
    _feedback(db_session, _item(db_session, NEWS_TITLE), "interessant")
    _feedback(db_session, _item(db_session, "Skipped one"), review.SKIPPED)

    given = review.overview(db_session, USER, None).given

    assert given == {"markt": {"interessant": 1, "niet_interessant": 1}, "nieuws": {"interessant": 1, "niet_interessant": 0}}


# --- the admin page -----------------------------------------------------------------


def _feedback_rows(db_session):
    db_session.expire_all()
    return db_session.query(models.Feedback).order_by(models.Feedback.id).all()


def test_review_page_and_actions_require_login(client):
    requests = (
        client.get("/admin/review", follow_redirects=False),
        client.post("/admin/review/1", data={"action": "like"}, follow_redirects=False),
        client.post("/admin/review/undo/1", follow_redirects=False),
        client.post("/admin/review/rescore", follow_redirects=False),
    )
    for resp in requests:
        assert resp.status_code == 303 and resp.headers["location"].startswith("/admin/login")


def test_review_page_shows_cards_tabs_and_counts(admin_client, db_session):
    _item(db_session, MARKET_TITLE)
    _item(db_session, NEWS_TITLE)

    page = admin_client.get("/admin/review", params={"category": "markt"})

    assert page.status_code == 200
    assert MARKET_TITLE in page.text and NEWS_TITLE not in page.text  # markt tab
    assert "Interessant" in page.text and "Overslaan" in page.text
    assert "1 te doen" in page.text
    assert 'href="/admin/review"' in page.text  # menu item
    assert NEWS_TITLE in admin_client.get("/admin/review", params={"category": "alles"}).text


def test_an_unknown_category_falls_back_to_markt(admin_client, db_session):
    _item(db_session, MARKET_TITLE)

    assert MARKET_TITLE in admin_client.get("/admin/review", params={"category": "bogus"}).text


def test_page_shows_clean_text_for_old_rows(admin_client, db_session):
    _item(db_session, "Fake Merger &amp; Acquisition Scams &quot;Phantom&quot;")

    page = admin_client.get("/admin/review", params={"category": "alles"})

    # cleaned to "Fake Merger & Acquisition Scams "Phantom"", then escaped once for HTML
    assert "Fake Merger &amp; Acquisition Scams &#34;Phantom&#34;" in page.text
    assert "&amp;amp;" not in page.text and "&amp;quot;" not in page.text


def test_each_action_stores_its_label_and_removes_the_item_from_the_queue(admin_client, db_session):
    like, dislike, skip = (_item(db_session, MARKET_TITLE + f" {i}") for i in range(3))

    for item, action in ((like, "like"), (dislike, "dislike"), (skip, "skip")):
        resp = admin_client.post(
            f"/admin/review/{item.id}", data={"action": action, "category": "markt"}, follow_redirects=False
        )
        assert resp.status_code == 303 and resp.headers["location"] == "/admin/review?category=markt"

    assert [(f.item_id, f.label, f.user_id) for f in _feedback_rows(db_session)] == [
        (like.id, "interessant", USER), (dislike.id, "niet_interessant", USER), (skip.id, "overgeslagen", USER),
    ]
    page = admin_client.get("/admin/review").text
    assert "data-id=" not in page  # no card left: every answered item left the queue


def test_json_mode_for_the_fast_path_and_a_double_click_adds_one_row(admin_client, db_session):
    item = _item(db_session, MARKET_TITLE)
    headers = {"X-Requested-With": "fetch"}

    first = admin_client.post(f"/admin/review/{item.id}", data={"action": "like"}, headers=headers).json()
    second = admin_client.post(f"/admin/review/{item.id}", data={"action": "like"}, headers=headers).json()

    assert first["ok"] and first["created"] is True and first["label"] == "interessant"
    assert second["ok"] and second["created"] is False and second["feedback_id"] == first["feedback_id"]
    assert len(_feedback_rows(db_session)) == 1


def test_a_thumb_already_given_through_the_mail_link_is_not_overwritten(admin_client, client, db_session):
    item = _item(db_session, MARKET_TITLE)
    client.post("/feedback", json={"item_id": item.id, "label": "interessant"})  # a thumb already recorded

    resp = admin_client.post(
        f"/admin/review/{item.id}", data={"action": "dislike"}, headers={"X-Requested-With": "fetch"}
    ).json()

    assert resp["created"] is False and resp["label"] == "interessant"
    assert [f.label for f in _feedback_rows(db_session)] == ["interessant"]


def test_unknown_action_or_item_is_rejected_without_storing_anything(admin_client, db_session):
    item = _item(db_session, MARKET_TITLE)
    headers = {"X-Requested-With": "fetch"}

    assert admin_client.post(f"/admin/review/{item.id}", data={"action": "explode"}, headers=headers).status_code == 422
    assert admin_client.post("/admin/review/999999", data={"action": "like"}, headers=headers).status_code == 404
    assert _feedback_rows(db_session) == []


def test_undo_removes_the_answer_and_the_item_is_offered_again(admin_client, db_session):
    item = _item(db_session, MARKET_TITLE)
    headers = {"X-Requested-With": "fetch"}
    answer = admin_client.post(f"/admin/review/{item.id}", data={"action": "dislike"}, headers=headers).json()
    assert f'data-id="{item.id}"' not in admin_client.get("/admin/review").text  # answered: gone

    assert admin_client.post(f"/admin/review/undo/{answer['feedback_id']}", headers=headers).json() == {"ok": True}

    assert _feedback_rows(db_session) == []
    assert f'data-id="{item.id}"' in admin_client.get("/admin/review").text  # offered again


def test_undo_of_an_unknown_answer_is_harmless(admin_client):
    resp = admin_client.post("/admin/review/undo/999999", headers={"X-Requested-With": "fetch"})

    assert resp.status_code == 200 and resp.json() == {"ok": True}


def test_rescore_button_updates_scores_and_is_not_mistaken_for_an_item_id(admin_client, client, db_session):
    liked = client.post(
        "/score", json={"source": "t", "title": "Acme acquires Globex", "url": "https://x/1",
                        "raw_content": "Cisco router firmware vendor Acme acquires Globex network security firm"},
    ).json()
    similar = client.post(
        "/score", json={"source": "t", "title": "Initech acquires Hooli", "url": "https://x/2",
                        "raw_content": "Cisco router firmware vendor Initech acquires Hooli network security firm"},
    ).json()
    admin_client.post(
        f"/admin/review/{liked['item_id']}", data={"action": "like"}, headers={"X-Requested-With": "fetch"}
    )

    resp = admin_client.post("/admin/review/rescore", headers={"X-Requested-With": "fetch"})

    assert resp.status_code == 200 and resp.json()["updated"] >= 2
    db_session.expire_all()
    assert db_session.get(models.Item, similar["item_id"]).relevance_score > 0.6

    plain = admin_client.post("/admin/review/rescore", data={"category": "nieuws"}, follow_redirects=False)
    assert plain.status_code == 303 and plain.headers["location"].startswith("/admin/review?category=nieuws&updated=")


def test_a_thumb_from_the_review_page_changes_the_score_of_new_items(admin_client, client, db_session):
    liked = client.post(
        "/score", json={"source": "t", "title": "Acme acquires Globex", "url": "https://x/1",
                        "raw_content": "Cisco router firmware vendor Acme acquires Globex network security firm"},
    ).json()
    admin_client.post(
        f"/admin/review/{liked['item_id']}", data={"action": "like"}, headers={"X-Requested-With": "fetch"}
    )

    new = client.post(
        "/score", json={"source": "t", "title": "Initech acquires Hooli", "url": "https://x/2",
                        "raw_content": "Cisco router firmware vendor Initech acquires Hooli network security firm"},
    ).json()

    assert new["relevance_score"] > 0.6
