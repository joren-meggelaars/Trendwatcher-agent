def _score(client, raw_content: str = "Some security article content."):
    resp = client.post(
        "/score",
        json={"source": "test", "title": "T", "url": "http://example.com", "raw_content": raw_content},
    )
    assert resp.status_code == 200
    return resp.json()


def test_feedback_for_unknown_item_returns_404_not_an_unhandled_exception(client):
    resp = client.post("/feedback", json={"item_id": 999999, "label": "interessant"})
    assert resp.status_code == 404
    assert "999999" in resp.json()["detail"]


def test_feedback_accepts_valid_item_and_label(client):
    scored = _score(client)
    resp = client.post("/feedback", json={"item_id": scored["item_id"], "label": "interessant"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_feedback_rejects_invalid_label(client):
    scored = _score(client)
    resp = client.post("/feedback", json={"item_id": scored["item_id"], "label": "leuk"})
    assert resp.status_code == 422
