def test_get_digest_settings_returns_defaults_on_first_call(client):
    resp = client.get("/settings/digest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["digest_hour"] == 7
    assert body["digest_top_n"] == 5
    assert "updated_at" in body


def test_get_digest_settings_is_idempotent(client):
    first = client.get("/settings/digest").json()
    second = client.get("/settings/digest").json()
    assert first == second


def test_put_digest_settings_updates_and_persists(client):
    resp = client.put("/settings/digest", json={"digest_hour": 14, "digest_top_n": 10})
    assert resp.status_code == 200
    assert resp.json()["digest_hour"] == 14
    assert resp.json()["digest_top_n"] == 10

    check = client.get("/settings/digest").json()
    assert check["digest_hour"] == 14
    assert check["digest_top_n"] == 10


def test_put_digest_settings_returns_422_for_invalid_hour(client):
    resp = client.put("/settings/digest", json={"digest_hour": 24, "digest_top_n": 5})
    assert resp.status_code == 422


def test_put_digest_settings_returns_422_for_invalid_top_n(client):
    resp = client.put("/settings/digest", json={"digest_hour": 7, "digest_top_n": 0})
    assert resp.status_code == 422
