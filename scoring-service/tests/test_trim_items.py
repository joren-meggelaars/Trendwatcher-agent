from uuid import uuid4

from app import models
from scripts import trim_items


def _score(client, title: str) -> int:
    resp = client.post(
        "/score",
        json={
            "source": "test",
            "title": title,
            "url": f"https://example.com/{uuid4().hex}",
            "raw_content": f"Artikel over {title}",
        },
    )
    assert resp.status_code == 200
    return resp.json()["item_id"]


def test_keeps_feedback_items_first_then_market_then_newest(client, db_session):
    old_news_with_feedback = _score(client, "Old zero-day story")
    old_market = _score(client, "Acme acquires Globex")
    news_ids = [_score(client, f"Ransomware attack number {i}") for i in range(3)]
    new_market = _score(client, "Globex raises $20M Series A")
    client.post("/feedback", json={"item_id": old_news_with_feedback, "label": "interessant"})

    keep, delete = trim_items.select_ids_to_keep(db_session, 3)

    # feedback item, then the two market items (newest first) — all news is cut,
    # even the newest news items.
    assert keep == [old_news_with_feedback, new_market, old_market]
    assert set(delete) == set(news_ids)


def test_keep_larger_than_table_keeps_everything(client, db_session):
    ids = [_score(client, f"Item {i}") for i in range(3)]

    keep, delete = trim_items.select_ids_to_keep(db_session, 50)

    assert set(keep) == set(ids)
    assert delete == []


def test_delete_items_removes_items_and_their_feedback_only(client, db_session):
    keep_id = _score(client, "Acme acquires Globex")
    drop_id = _score(client, "Ransomware attack")
    client.post("/feedback", json={"item_id": keep_id, "label": "interessant"})
    client.post("/feedback", json={"item_id": drop_id, "label": "niet_interessant"})

    trim_items.delete_items(db_session, [drop_id])

    assert db_session.get(models.Item, drop_id) is None
    assert db_session.get(models.Item, keep_id) is not None
    remaining = db_session.query(models.Feedback).all()
    assert [f.item_id for f in remaining] == [keep_id]


def test_selection_alone_never_deletes(client, db_session):
    for i in range(5):
        _score(client, f"Ransomware attack number {i}")

    trim_items.select_ids_to_keep(db_session, 2)

    assert db_session.query(models.Item).count() == 5
