from datetime import datetime, timedelta, timezone

from app import models


def _score(client, title: str, source_id: int | None = None) -> dict:
    payload = {
        "source": "https://feed.example.com/rss",
        "title": title,
        "url": f"https://example.com/{title.replace(' ', '-')}",
        "raw_content": f"Artikel over {title}",
    }
    if source_id is not None:
        payload["source_id"] = source_id
    resp = client.post("/score", json=payload)
    assert resp.status_code == 200
    return resp.json()


def test_top_items_orders_by_score_then_newest_and_respects_limit(client, db_session):
    a = _score(client, "alpha")
    b = _score(client, "beta")
    c = _score(client, "gamma")

    for item_id, score in ((a["item_id"], 0.9), (b["item_id"], 0.5), (c["item_id"], 0.5)):
        db_session.get(models.Item, item_id).relevance_score = score
    db_session.commit()

    resp = client.get("/items/top", params={"days": 7, "limit": 2})
    assert resp.status_code == 200
    ids = [i["item_id"] for i in resp.json()]
    # highest score first, then the newest of the tied pair
    assert ids == [a["item_id"], c["item_id"]]


def test_top_items_excludes_items_older_than_window(client, db_session):
    old = _score(client, "old")
    fresh = _score(client, "fresh")
    db_session.get(models.Item, old["item_id"]).created_at = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.commit()

    resp = client.get("/items/top", params={"days": 7, "limit": 10})
    ids = [i["item_id"] for i in resp.json()]
    assert fresh["item_id"] in ids
    assert old["item_id"] not in ids


def test_top_items_returns_source_url_from_source_or_item_label(client):
    source = client.post("/sources", json={"url": "https://feed.example.com/rss", "type": "rss"}).json()
    with_source = _score(client, "with source", source_id=source["id"])
    without_source = _score(client, "without source")

    by_id = {i["item_id"]: i for i in client.get("/items/top", params={"limit": 10}).json()}
    assert by_id[with_source["item_id"]]["source_url"] == "https://feed.example.com/rss"
    # no Source row -> falls back to the free-text label the item was scored with
    assert by_id[without_source["item_id"]]["source_url"] == "https://feed.example.com/rss"
    assert by_id[with_source["item_id"]]["title"] == "with source"


def test_top_items_empty_database_returns_empty_list(client):
    resp = client.get("/items/top")
    assert resp.status_code == 200
    assert resp.json() == []


def test_top_items_rejects_out_of_range_parameters(client):
    assert client.get("/items/top", params={"limit": 0}).status_code == 422
    assert client.get("/items/top", params={"days": 0}).status_code == 422
