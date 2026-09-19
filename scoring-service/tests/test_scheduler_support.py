"""Endpoints added specifically so the scheduler never needs direct DB access:
GET /items/recent-feedback, POST /sources/evaluate-all.
"""

from uuid import uuid4


def _score(client, raw_content: str = "Some security article content.", source_id: int | None = None) -> dict:
    payload = {"source": "test", "title": f"T {uuid4().hex[:8]}", "url": f"http://example.com/{uuid4().hex}", "raw_content": raw_content}
    if source_id is not None:
        payload["source_id"] = source_id
    resp = client.post("/score", json=payload)
    assert resp.status_code == 200
    return resp.json()


def test_old_feedback_link_only_redirects_to_the_login_and_records_nothing(client, db_session):
    """Mails sent before the digest pages existed still carry /feedback-link URLs.
    They must not change anything by themselves (a mail scanner may fetch them):
    they send the visitor through the login to /admin/vote-link."""
    from app import models

    scored = _score(client)

    resp = client.get(
        "/feedback-link", params={"item_id": scored["item_id"], "label": "interessant"}, follow_redirects=False
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/vote-link?item_id={scored['item_id']}&label=interessant"
    assert db_session.query(models.Feedback).count() == 0


def test_old_feedback_link_rejects_an_unknown_label(client):
    resp = client.get("/feedback-link", params={"item_id": 1, "label": "misschien"}, follow_redirects=False)

    assert resp.status_code == 422


def test_recent_feedback_items_filters_by_label_and_window(client):
    liked = _score(client, "Liked article content.")
    disliked = _score(client, "Disliked article content.")
    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})
    client.post("/feedback", json={"item_id": disliked["item_id"], "label": "niet_interessant"})

    resp = client.get("/items/recent-feedback", params={"label": "interessant", "days": 30})
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()]
    assert len(titles) == 1 and titles[0].startswith("T ")  # only the liked one, not the disliked
    assert all("summary" in item for item in resp.json())

    resp_old_window = client.get("/items/recent-feedback", params={"label": "interessant", "days": 0})
    assert resp_old_window.json() == []


def test_evaluate_all_activates_and_reports_instroom_source(client):
    source = client.post(
        "/sources", json={"url": "https://evaluate-all-source.example.com", "type": "blog"}
    ).json()

    liked = _score(client, "Router firmware vulnerability allows remote code execution.")
    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})

    contents = [
        "Router firmware vulnerability allows remote code execution on network devices.",
        "Critical firmware bug lets attackers execute remote code on routers.",
        "Firmware vulnerability enables remote code execution without authentication.",
    ]
    for content in contents:
        _score(client, content, source_id=source["id"])

    resp = client.post("/sources/evaluate-all")
    assert resp.status_code == 200
    changes = resp.json()
    match = next((c for c in changes if c["source_id"] == source["id"]), None)
    assert match is not None, f"expected a status change for the new source, got {changes}"
    assert match["old_status"] == "kandidaat"
    assert match["new_status"] == "actief"
    assert match["url"] == "https://evaluate-all-source.example.com"


def test_evaluate_all_skips_sources_that_do_not_change(client):
    client.post("/sources", json={"url": "https://untouched-source.example.com", "type": "blog"})

    resp = client.post("/sources/evaluate-all")
    assert resp.status_code == 200
    urls = [c["url"] for c in resp.json()]
    assert "https://untouched-source.example.com" not in urls
