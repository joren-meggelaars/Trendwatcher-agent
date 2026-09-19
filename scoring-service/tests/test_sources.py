NETWORK_TEMPLATES = [
    "Critical vulnerability discovered in Cisco router firmware allows remote code execution.",
    "Cisco router firmware vulnerability lets attackers execute remote code without authentication.",
    "Fortinet patches severe authentication bypass in FortiOS VPN appliance firmware.",
    "Juniper Networks warns of actively exploited vulnerability in Junos OS firewalls.",
    "Zero-day in Netgear routers allows remote takeover of home network devices.",
]

IRRELEVANT_TEMPLATES = [
    "Local bakery wins award for best croissant in the regional pastry competition.",
    "City council approves new bike lane along the riverside park.",
    "Weather forecast predicts sunny skies for the upcoming long weekend.",
    "Community garden project seeks volunteers for spring planting season.",
    "Local football club announces new sponsorship deal ahead of the season.",
    "Museum opens new exhibit on the history of local pottery.",
    "Farmers market extends its hours for the summer months.",
    "High school choir wins first place at the national competition.",
    "New coffee shop opens downtown with locally roasted beans.",
    "Public library announces extended weekend hours for students.",
]


def _create_source(client, url: str = "https://newblog.example.com", type_: str = "blog") -> dict:
    resp = client.post("/sources", json={"url": url, "type": type_})
    assert resp.status_code == 201
    return resp.json()


def _score(client, raw_content: str, source_id: int | None = None, title: str = "T") -> dict:
    payload = {
        "source": "test",
        "title": title,
        "url": "http://example.com",
        "raw_content": raw_content,
    }
    if source_id is not None:
        payload["source_id"] = source_id
    resp = client.post("/score", json=payload)
    assert resp.status_code == 200
    return resp.json()


def test_create_source_starts_as_kandidaat(client):
    source = _create_source(client, url="https://manual-source.example.com")
    assert source["status"] == "kandidaat"
    assert source["discovery_method"] == "manual"
    assert source["url"] == "https://manual-source.example.com"


def test_list_sources_filters_by_status(client):
    _create_source(client, url="https://a.example.com")
    _create_source(client, url="https://b.example.com")

    resp = client.get("/sources", params={"status": "kandidaat"})
    assert resp.status_code == 200
    urls = {s["url"] for s in resp.json()}
    assert {"https://a.example.com", "https://b.example.com"} <= urls
    assert all(s["status"] == "kandidaat" for s in resp.json())

    resp_active = client.get("/sources", params={"status": "actief"})
    assert resp_active.json() == []


def test_scoring_an_item_with_unknown_link_registers_candidate_source(client):
    body = _score(
        client,
        "Interesting write-up, see https://unknown-domain.example/article for details.",
    )
    # The discovery side-effect doesn't add anything to the /score response.
    assert set(body.keys()) == {"item_id", "summary", "relevance_score", "category"}

    resp = client.get("/sources", params={"status": "kandidaat"})
    urls = {s["url"] for s in resp.json()}
    assert "unknown-domain.example" in urls
    created = next(s for s in resp.json() if s["url"] == "unknown-domain.example")
    assert created["discovery_method"] == "link_following"


def test_known_domain_is_not_registered_twice(client):
    _create_source(client, url="known.example.com", type_="blog")

    _score(client, "See https://known.example.com/post for more.")

    resp = client.get("/sources")
    matches = [s for s in resp.json() if s["url"] == "known.example.com"]
    assert len(matches) == 1


def test_instroom_scenario_activates_candidate_source(client):
    """5 items from a brand-new candidate source, >=3 scoring high -> 'actief'."""
    source = _create_source(client, url="https://promising-blog.example.com")

    # Seed a liked item so later similar articles score above the 0.6 threshold.
    liked = _score(client, NETWORK_TEMPLATES[0], title="seed")
    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})

    scored = [
        _score(client, content, source_id=source["id"], title=f"n{i}")
        for i, content in enumerate(NETWORK_TEMPLATES)
    ]
    high_scores = [s for s in scored if s["relevance_score"] > 0.6]
    assert len(high_scores) >= 3, f"expected >=3 high scores, got {scored}"

    resp = client.post(f"/sources/{source['id']}/evaluate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "actief"


def test_krimp_scenario_deactivates_active_source(client):
    """An active source whose latest 10 items are mostly 'niet_interessant' -> 'gedeactiveerd'."""
    source = _create_source(client, url="https://noisy-source.example.com")

    # First get the source to "actief" via the same instroom path as the test above.
    liked = _score(client, NETWORK_TEMPLATES[0], title="seed")
    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})
    for i, content in enumerate(NETWORK_TEMPLATES):
        _score(client, content, source_id=source["id"], title=f"activate{i}")
    assert client.post(f"/sources/{source['id']}/evaluate").json()["status"] == "actief"

    # Now feed 10 irrelevant items and mark 8 of them "niet_interessant".
    scored_items = [
        _score(client, content, source_id=source["id"], title=f"noise{i}")
        for i, content in enumerate(IRRELEVANT_TEMPLATES)
    ]
    for item in scored_items[:8]:
        client.post("/feedback", json={"item_id": item["item_id"], "label": "niet_interessant"})

    resp = client.post(f"/sources/{source['id']}/evaluate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "gedeactiveerd"


def test_evaluate_unknown_source_returns_404(client):
    resp = client.post("/sources/999999/evaluate")
    assert resp.status_code == 404


def test_activation_threshold_is_configurable(client, monkeypatch):
    from app.config import settings

    source = _create_source(client, url="https://strict-threshold.example.com")
    liked = _score(client, NETWORK_TEMPLATES[0], title="seed")
    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})

    for i, content in enumerate(NETWORK_TEMPLATES):
        _score(client, content, source_id=source["id"], title=f"n{i}")

    monkeypatch.setattr(settings, "source_activation_min_high_score", 999)
    resp = client.post(f"/sources/{source['id']}/evaluate")
    assert resp.json()["status"] == "kandidaat"  # unreachable bar -> stays a candidate

    monkeypatch.setattr(settings, "source_activation_min_high_score", 3)
    resp = client.post(f"/sources/{source['id']}/evaluate")
    assert resp.json()["status"] == "actief"
