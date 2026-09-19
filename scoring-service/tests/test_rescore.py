from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import models, scoring
from app.config import settings

USER = settings.default_user_id

MARKET = ("Acme acquires Globex", "Cisco router firmware vendor Acme acquires Globex network security firm")
MARKET_SIMILAR = ("Initech acquires Hooli", "Cisco router firmware vendor Initech acquires Hooli network security firm")
MARKET_UNRELATED = ("Umbrella acquires Wonka", "Chocolate factory tour tickets and candy recipes for children")


def _score(client, article, source_id=None):
    title, content = article
    payload = {"source": "test", "title": title, "url": f"https://example.com/{uuid4().hex}", "raw_content": content}
    if source_id is not None:
        payload["source_id"] = source_id
    resp = client.post("/score", json=payload)
    assert resp.status_code == 200
    return resp.json()


def _score_of(db_session, item_id):
    db_session.expire_all()
    return db_session.get(models.Item, item_id).relevance_score


def test_a_thumb_moves_items_that_were_already_stored(client, db_session):
    liked = _score(client, MARKET)
    similar = _score(client, MARKET_SIMILAR)
    unrelated = _score(client, MARKET_UNRELATED)
    assert _score_of(db_session, similar["item_id"]) == 0.5  # scored before any feedback

    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})
    updated = scoring.rescore_recent(db_session, USER)

    assert updated >= 2  # the liked item itself and the similar one
    assert _score_of(db_session, similar["item_id"]) > 0.6
    assert _score_of(db_session, unrelated["item_id"]) < _score_of(db_session, similar["item_id"])


def test_a_thumbs_down_pulls_similar_stored_items_down(client, db_session):
    disliked = _score(client, MARKET)
    similar = _score(client, MARKET_SIMILAR)

    client.post("/feedback", json={"item_id": disliked["item_id"], "label": "niet_interessant"})
    scoring.rescore_recent(db_session, USER)

    assert _score_of(db_session, similar["item_id"]) < 0.4


def test_nothing_happens_without_any_thumbs(client, db_session):
    _score(client, MARKET)

    assert scoring.rescore_recent(db_session, USER) == 0


def test_a_skip_is_not_a_thumb(client, db_session):
    item = _score(client, MARKET)
    db_session.add(models.Feedback(item_id=item["item_id"], user_id=USER, label="overgeslagen"))
    db_session.commit()

    assert scoring.rescore_recent(db_session, USER) == 0


def test_unchanged_thumbs_are_not_recomputed_unless_forced(client, db_session):
    liked = _score(client, MARKET)
    similar = _score(client, MARKET_SIMILAR)
    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})
    scoring.rescore_recent(db_session, USER)

    db_session.get(models.Item, similar["item_id"]).relevance_score = 0.99  # something changed the score behind our back
    db_session.commit()

    assert scoring.rescore_recent(db_session, USER) == 0  # same thumbs: skipped
    assert _score_of(db_session, similar["item_id"]) == 0.99
    assert scoring.rescore_recent(db_session, USER, force=True) >= 1  # forced: recomputed
    assert _score_of(db_session, similar["item_id"]) < 0.99


def test_new_thumbs_are_picked_up_again(client, db_session):
    first = _score(client, MARKET)
    client.post("/feedback", json={"item_id": first["item_id"], "label": "interessant"})
    scoring.rescore_recent(db_session, USER)
    other = _score(client, MARKET_UNRELATED)
    before = _score_of(db_session, other["item_id"])

    client.post("/feedback", json={"item_id": other["item_id"], "label": "interessant"})

    assert scoring.rescore_recent(db_session, USER) >= 1
    assert _score_of(db_session, other["item_id"]) > before  # it is now its own reference point


def test_items_older_than_the_window_are_left_alone(client, db_session):
    liked = _score(client, MARKET)
    old = _score(client, MARKET_SIMILAR)
    db_session.get(models.Item, old["item_id"]).created_at = datetime.now(timezone.utc) - timedelta(days=90)
    db_session.commit()

    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})
    scoring.rescore_recent(db_session, USER, days=30)

    assert _score_of(db_session, old["item_id"]) == 0.5


def test_a_sources_running_average_follows_the_rescored_items(client, db_session):
    source = client.post("/sources", json={"url": "https://avg.example.com", "type": "rss"}).json()
    liked = _score(client, MARKET, source_id=source["id"])
    similar = _score(client, MARKET_SIMILAR, source_id=source["id"])
    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})

    scoring.rescore_recent(db_session, USER)

    db_session.expire_all()
    scores = [_score_of(db_session, liked["item_id"]), _score_of(db_session, similar["item_id"])]
    assert abs(db_session.get(models.Source, source["id"]).running_avg_score - sum(scores) / 2) < 1e-9


def test_rescore_endpoint(client, db_session):
    liked = _score(client, MARKET)
    _score(client, MARKET_SIMILAR)
    assert client.post("/items/rescore").json() == {"updated": 0}  # no thumbs yet

    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})
    assert client.post("/items/rescore").json()["updated"] >= 2

    assert client.post("/items/rescore", params={"days": 0}).status_code == 422
