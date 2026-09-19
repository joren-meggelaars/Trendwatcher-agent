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


def test_score_response_includes_category(client):
    market = _score(client, "Acme raises $50M Series B")
    news = _score(client, "Critical vulnerability patched in Acme VPN")

    assert market["category"] == "markt"
    assert news["category"] == "nieuws"


def test_top_items_category_filters_before_limit(client, db_session):
    # The two best-scoring items are news; without filtering-before-limit the
    # top 2 would contain no market item at all.
    news_a = _score(client, "Zero-day exploited in Acme firewall")
    news_b = _score(client, "Ransomware attack hits Acme hospital")
    market_a = _score(client, "Acme acquires Globex")
    market_b = _score(client, "Globex raises $20M Series A")

    for item_id, score in (
        (news_a["item_id"], 0.9),
        (news_b["item_id"], 0.8),
        (market_a["item_id"], 0.6),
        (market_b["item_id"], 0.7),
    ):
        db_session.get(models.Item, item_id).relevance_score = score
    db_session.commit()

    resp = client.get("/items/top", params={"limit": 2, "category": "markt"})
    assert resp.status_code == 200
    assert [i["item_id"] for i in resp.json()] == [market_b["item_id"], market_a["item_id"]]

    resp = client.get("/items/top", params={"limit": 2, "category": "nieuws"})
    assert [i["item_id"] for i in resp.json()] == [news_a["item_id"], news_b["item_id"]]

    # No category -> unchanged behaviour, everything competes.
    resp = client.get("/items/top", params={"limit": 2})
    assert [i["item_id"] for i in resp.json()] == [news_a["item_id"], news_b["item_id"]]


def test_top_items_category_fewer_matches_than_limit_returns_only_those(client):
    _score(client, "Zero-day exploited in Acme firewall")
    market = _score(client, "Acme acquires Globex")

    resp = client.get("/items/top", params={"limit": 5, "category": "markt"})
    assert [i["item_id"] for i in resp.json()] == [market["item_id"]]


def test_top_items_rejects_unknown_category(client):
    assert client.get("/items/top", params={"category": "sport"}).status_code == 422


def test_top_items_empty_database_returns_empty_list(client):
    resp = client.get("/items/top")
    assert resp.status_code == 200
    assert resp.json() == []


def test_top_items_rejects_out_of_range_parameters(client):
    assert client.get("/items/top", params={"limit": 0}).status_code == 422
    assert client.get("/items/top", params={"days": 0}).status_code == 422


def test_scoring_the_same_url_twice_returns_the_existing_item_without_embedding(client, monkeypatch):
    import app.main as main_module

    first = _score(client, "same link")

    calls = []
    real_embed = main_module.get_embedding_provider

    def _counting_provider():
        provider = real_embed()
        original = provider.embed
        provider.embed = lambda text: calls.append(text) or original(text)
        return provider

    client.app.dependency_overrides[main_module.get_embedding_provider] = _counting_provider
    try:
        second = _score(client, "same link")
    finally:
        client.app.dependency_overrides.pop(main_module.get_embedding_provider, None)

    assert second["item_id"] == first["item_id"]
    assert calls == []  # no embedding request for an already-stored URL
    assert len(client.get("/items/top", params={"limit": 50}).json()) == 1


def test_top_items_undigested_skips_items_already_mailed(client):
    first = _score(client, "first article")
    second = _score(client, "second article")

    resp = client.post("/items/mark-digested", json={"item_ids": [first["item_id"]]})
    assert resp.status_code == 200 and resp.json() == {"marked": 1}

    everything = [i["item_id"] for i in client.get("/items/top", params={"limit": 10}).json()]
    undigested = [i["item_id"] for i in client.get("/items/top", params={"limit": 10, "undigested": True}).json()]
    assert set(everything) == {first["item_id"], second["item_id"]}
    assert undigested == [second["item_id"]]


def test_top_items_undigested_combines_with_category_before_limit(client):
    mailed = _score(client, "Acme acquires Globex")
    fresh = _score(client, "Globex raises $20M Series A")
    client.post("/items/mark-digested", json={"item_ids": [mailed["item_id"]]})

    resp = client.get("/items/top", params={"limit": 1, "category": "markt", "undigested": True})

    assert [i["item_id"] for i in resp.json()] == [fresh["item_id"]]


def test_mark_digested_is_idempotent_ignores_unknown_ids_and_keeps_first_timestamp(client, db_session):
    item = _score(client, "some article")

    first = client.post("/items/mark-digested", json={"item_ids": [item["item_id"], 999999]}).json()
    stamp = db_session.get(models.Item, item["item_id"]).digested_at
    second = client.post("/items/mark-digested", json={"item_ids": [item["item_id"]]}).json()
    db_session.expire_all()

    assert first == {"marked": 1}  # the unknown id is ignored
    assert second == {"marked": 0}
    assert db_session.get(models.Item, item["item_id"]).digested_at == stamp


def test_mark_digested_rejects_empty_and_oversized_lists(client):
    assert client.post("/items/mark-digested", json={"item_ids": []}).status_code == 422
    assert client.post("/items/mark-digested", json={"item_ids": list(range(201))}).status_code == 422
