from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app import models
from app.config import settings
from tests.conftest import ADMIN_TEST_PASSWORD, ADMIN_TEST_USERNAME

USER = settings.default_user_id
FETCH = {"X-Requested-With": "fetch"}


def _item(db_session, title, category_hint="markt"):
    item = models.Item(
        user_id=USER, source="https://feed.example.com", title=title, url=f"https://example.com/{uuid4().hex}",
        raw_content="x", summary="Een samenvatting.", relevance_score=0.6, created_at=datetime.now(timezone.utc),
    )
    db_session.add(item)
    db_session.commit()
    return item


def _digest(client, items_by_category, kind="daily"):
    resp = client.post(
        "/digests",
        json={
            "kind": kind,
            "items": [{"item_id": i.id, "category": c} for c, items in items_by_category.items() for i in items],
        },
    )
    assert resp.status_code == 201
    return resp.json()["digest_id"]


def _votes(db_session):
    db_session.expire_all()
    return [(f.item_id, f.label) for f in db_session.query(models.Feedback).order_by(models.Feedback.id).all()]


# --- recording a digest (the scheduler's side) -----------------------------------------


def test_a_digest_is_recorded_with_its_items_and_can_be_marked_mailed(client, db_session):
    a, b = _item(db_session, "Acme acquires Globex"), _item(db_session, "Zero-day in Cisco")

    digest_id = _digest(client, {"markt": [a], "nieuws": [b]})

    digest = db_session.get(models.Digest, digest_id)
    assert [(e.item_id, e.category, e.position) for e in digest.entries] == [(a.id, "markt", 0), (b.id, "nieuws", 1)]
    assert digest.mailed is False and digest.kind == "daily"

    assert client.post(f"/digests/{digest_id}/mailed").status_code == 204
    db_session.expire_all()
    assert db_session.get(models.Digest, digest_id).mailed is True
    assert client.post("/digests/999999/mailed").status_code == 404


def test_a_digest_ignores_unknown_items_and_needs_at_least_one_real_one(client, db_session):
    real = _item(db_session, "Real item")

    ok = client.post("/digests", json={"items": [{"item_id": real.id, "category": "markt"}, {"item_id": 999999, "category": "nieuws"}]})
    none = client.post("/digests", json={"items": [{"item_id": 999999, "category": "markt"}]})
    empty = client.post("/digests", json={"items": []})

    assert ok.status_code == 201
    assert none.status_code == 422 and empty.status_code == 422


# --- the digest page ----------------------------------------------------------------------


def test_digest_page_shows_the_digest_as_mailed_with_current_votes(admin_client, client, db_session):
    a, b, c = (_item(db_session, t) for t in ("Alpha item", "Beta item", "Gamma item"))
    digest_id = _digest(client, {"markt": [a, b], "nieuws": [c]})
    db_session.add(models.Feedback(item_id=a.id, user_id=USER, label="interessant"))
    db_session.commit()

    page = admin_client.get(f"/admin/digest/{digest_id}")

    assert page.status_code == 200
    for title in ("Alpha item", "Beta item", "Gamma item"):
        assert title in page.text
    assert "1 van 3 beoordeeld" in page.text
    assert page.text.count("voted-up") == 1  # only Alpha carries a thumb
    assert "Marktontwikkeling" in page.text and "Nieuws" in page.text
    assert "/static/digest.js" in page.text


def test_digest_page_only_shows_that_digests_items(admin_client, client, db_session):
    old, new = _item(db_session, "Old digest item"), _item(db_session, "New digest item")
    old_id = _digest(client, {"markt": [old]})
    _digest(client, {"markt": [new]})

    page = admin_client.get(f"/admin/digest/{old_id}").text

    assert "Old digest item" in page and "New digest item" not in page


def test_unknown_digest_is_a_friendly_404(admin_client):
    resp = admin_client.get("/admin/digest/999999")

    assert resp.status_code == 404 and "bestaat niet" in resp.text


def test_latest_digest_redirects_to_the_newest_or_the_archive(admin_client, client, db_session):
    assert admin_client.get("/admin/digest/latest", follow_redirects=False).headers["location"] == "/admin/digests"
    first = _digest(client, {"markt": [_item(db_session, "One")]})
    second = _digest(client, {"markt": [_item(db_session, "Two")]})

    resp = admin_client.get("/admin/digest/latest", follow_redirects=False)

    assert resp.headers["location"] == f"/admin/digest/{second}" and second > first


def test_archive_lists_digests_with_composition_and_progress(admin_client, client, db_session):
    a, b = _item(db_session, "Alpha item"), _item(db_session, "Beta item")
    digest_id = _digest(client, {"markt": [a], "nieuws": [b]}, kind="preview")
    client.post(f"/digests/{digest_id}/mailed")
    db_session.add(models.Feedback(item_id=a.id, user_id=USER, label="niet_interessant"))
    db_session.commit()

    page = admin_client.get("/admin/digests").text

    assert f'href="/admin/digest/{digest_id}"' in page
    assert "1 markt" in page and "1 nieuws" in page
    assert "voorbeeld" in page and "1 van 2" in page


def test_empty_archive_says_so(admin_client):
    assert "Nog geen digests" in admin_client.get("/admin/digests").text


# --- voting from the page --------------------------------------------------------------------


def test_vote_sets_replaces_and_clears_one_answer_and_reports_the_previous_one(admin_client, db_session):
    item = _item(db_session, "Vote item")

    like = admin_client.post(f"/admin/vote/{item.id}", data={"action": "like"}, headers=FETCH).json()
    switch = admin_client.post(f"/admin/vote/{item.id}", data={"action": "dislike"}, headers=FETCH).json()
    clear = admin_client.post(f"/admin/vote/{item.id}", data={"action": "clear"}, headers=FETCH).json()

    assert like == {"ok": True, "vote": "like", "previous": None}
    assert switch == {"ok": True, "vote": "dislike", "previous": "like"}  # replaced, not added
    assert clear == {"ok": True, "vote": None, "previous": "dislike"}
    assert _votes(db_session) == []


def test_vote_keeps_exactly_one_row_and_replaces_an_earlier_skip(admin_client, db_session):
    item = _item(db_session, "Vote item")
    db_session.add(models.Feedback(item_id=item.id, user_id=USER, label="overgeslagen"))
    db_session.commit()

    admin_client.post(f"/admin/vote/{item.id}", data={"action": "like"}, headers=FETCH)
    admin_client.post(f"/admin/vote/{item.id}", data={"action": "like"}, headers=FETCH)  # a repeat is harmless

    assert _votes(db_session) == [(item.id, "interessant")]


def test_undo_from_the_page_restores_the_previous_state_exactly(admin_client, db_session):
    item = _item(db_session, "Vote item")
    admin_client.post(f"/admin/vote/{item.id}", data={"action": "like"}, headers=FETCH)
    switched = admin_client.post(f"/admin/vote/{item.id}", data={"action": "dislike"}, headers=FETCH).json()

    admin_client.post(f"/admin/vote/{item.id}", data={"action": switched["previous"]}, headers=FETCH)  # what the toast's undo does

    assert _votes(db_session) == [(item.id, "interessant")]


def test_vote_rejects_unknown_action_and_item(admin_client, db_session):
    item = _item(db_session, "Vote item")

    assert admin_client.post(f"/admin/vote/{item.id}", data={"action": "explode"}, headers=FETCH).status_code == 422
    assert admin_client.post("/admin/vote/999999", data={"action": "like"}, headers=FETCH).status_code == 404
    assert _votes(db_session) == []


def test_vote_needs_a_login_and_a_page_script_can_tell(client, db_session):
    item = _item(db_session, "Vote item")

    fetch = client.post(f"/admin/vote/{item.id}", data={"action": "like"}, headers=FETCH)
    plain = client.post(f"/admin/vote/{item.id}", data={"action": "like"}, follow_redirects=False)

    assert fetch.status_code == 401 and fetch.json() == {"ok": False, "login": True}
    assert plain.status_code == 303
    assert _votes(db_session) == []


def test_a_vote_moves_the_score_of_similar_items_like_any_other_thumb(admin_client, client):
    liked = client.post(
        "/score", json={"source": "t", "title": "Acme acquires Globex", "url": "https://x/1",
                        "raw_content": "Cisco router firmware vendor Acme acquires Globex network security firm"},
    ).json()
    admin_client.post(f"/admin/vote/{liked['item_id']}", data={"action": "like"}, headers=FETCH)

    similar = client.post(
        "/score", json={"source": "t", "title": "Initech acquires Hooli", "url": "https://x/2",
                        "raw_content": "Cisco router firmware vendor Initech acquires Hooli network security firm"},
    ).json()

    assert similar["relevance_score"] > 0.6


# --- the link in the mail ------------------------------------------------------------------------


def test_vote_link_goes_to_the_newest_digest_with_the_item_and_the_vote(admin_client, client, db_session):
    item = _item(db_session, "Mailed item")
    first = _digest(client, {"markt": [item]})
    second = _digest(client, {"markt": [item]})

    resp = admin_client.get("/admin/vote-link", params={"item_id": item.id, "label": "interessant"}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/digest/{second}?vote={item.id}:like" and second > first


def test_vote_link_for_an_item_from_before_digests_were_recorded_uses_the_review_page(admin_client, db_session):
    item = _item(db_session, "Old mail item")

    resp = admin_client.get("/admin/vote-link", params={"item_id": item.id, "label": "niet_interessant"}, follow_redirects=False)

    assert resp.headers["location"] == f"/admin/review?category=alles&vote={item.id}:dislike"


def test_vote_link_with_an_unknown_label_just_opens_the_archive(admin_client):
    resp = admin_client.get("/admin/vote-link", params={"item_id": 1, "label": "boem"}, follow_redirects=False)

    assert resp.headers["location"] == "/admin/digests"


def test_clicking_a_link_in_the_mail_logs_in_and_lands_on_the_digest_with_the_vote(client, db_session):
    """The whole path of the new flow, from a mail-link click to the page that records the vote."""
    item = _item(db_session, "Mailed item")
    digest_id = _digest(client, {"markt": [item]})
    target = f"/admin/digest/{digest_id}?vote={item.id}:like"

    # 1. not logged in: the digest URL sends you to the login, remembering where you were going
    first = client.get(target, follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"].startswith("/admin/login?next=")
    login_url = first.headers["location"]
    assert parse_qs(urlsplit(login_url).query)["next"] == [target]

    # 2. the login page carries it along; logging in leads straight back
    assert 'name="next"' in client.get(login_url).text
    done = client.post(
        "/admin/login",
        data={"username": ADMIN_TEST_USERNAME, "password": ADMIN_TEST_PASSWORD, "next": target, "remember": "1"},
        follow_redirects=False,
    )
    assert done.status_code == 303 and done.headers["location"] == target

    # 3. the page opens; nothing was recorded by opening it (the page's script does that with a POST)
    assert client.get(target).status_code == 200
    assert _votes(db_session) == []


def test_an_old_style_mail_link_also_ends_up_on_the_digest_after_login(client, db_session):
    item = _item(db_session, "Mailed item")
    digest_id = _digest(client, {"markt": [item]})

    hop1 = client.get("/feedback-link", params={"item_id": item.id, "label": "interessant"}, follow_redirects=False)
    hop2 = client.get(hop1.headers["location"], follow_redirects=False)
    assert hop2.headers["location"].startswith("/admin/login?next=")
    login = client.post(
        "/admin/login",
        data={"username": ADMIN_TEST_USERNAME, "password": ADMIN_TEST_PASSWORD, "next": parse_qs(urlsplit(hop2.headers["location"]).query)["next"][0]},
        follow_redirects=False,
    )
    hop3 = client.get(login.headers["location"], follow_redirects=False)

    assert hop3.headers["location"] == f"/admin/digest/{digest_id}?vote={item.id}:like"
    assert _votes(db_session) == []  # still nothing recorded by any GET
