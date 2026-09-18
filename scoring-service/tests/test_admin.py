from tests.conftest import ADMIN_TEST_PASSWORD, ADMIN_TEST_USERNAME


def test_unauthenticated_visitor_is_redirected_to_login(client):
    resp = client.get("/admin/sources", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_unauthenticated_items_and_batch_add_also_redirect(client):
    for path in ("/admin/items", "/admin/items/batch-add"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"


def test_wrong_password_shows_friendly_error_not_500(client):
    resp = client.post(
        "/admin/login",
        data={"username": ADMIN_TEST_USERNAME, "password": "totally-wrong"},
    )
    assert resp.status_code == 401
    assert "Ongeldige gebruikersnaam of wachtwoord" in resp.text

    # And still logged out afterwards.
    resp = client.get("/admin/sources", follow_redirects=False)
    assert resp.status_code == 303


def test_login_then_pages_reachable_then_logout_blocks_again(client):
    login_resp = client.post(
        "/admin/login",
        data={"username": ADMIN_TEST_USERNAME, "password": ADMIN_TEST_PASSWORD},
    )
    assert login_resp.status_code == 200  # followed redirect to /admin/sources
    assert "Bronnen" in login_resp.text

    for path in ("/admin/sources", "/admin/items", "/admin/items/batch-add"):
        resp = client.get(path)
        assert resp.status_code == 200

    logout_resp = client.post("/admin/logout", follow_redirects=False)
    assert logout_resp.status_code == 303
    assert logout_resp.headers["location"] == "/admin/login"

    resp = client.get("/admin/sources", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_sources_form_creates_a_new_source_visible_after_reload(admin_client):
    resp = admin_client.post(
        "/admin/sources",
        data={"url": "https://admin-added.example.com", "type": "blog"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = admin_client.get("/admin/sources")
    assert resp.status_code == 200
    assert "admin-added.example.com" in resp.text


def test_override_source_status_updates_existing_source(admin_client):
    create_resp = admin_client.post(
        "/sources", json={"url": "https://override-test.example.com", "type": "blog"}
    )
    source_id = create_resp.json()["id"]
    assert create_resp.json()["status"] == "kandidaat"

    resp = admin_client.post(
        f"/admin/sources/{source_id}/override-status",
        data={"new_status": "actief"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    check_resp = admin_client.get("/sources")
    updated = next(s for s in check_resp.json() if s["id"] == source_id)
    assert updated["status"] == "actief"


def test_batch_add_reports_successes_and_a_clear_failure(admin_client, monkeypatch):
    import httpx

    import app.admin as admin_module

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            pass

    def _fake_get(url, timeout, follow_redirects):
        if "unreachable" in url:
            raise httpx.ConnectError("connection failed", request=httpx.Request("GET", url))
        return _FakeResponse(f"<html><head><title>Title for {url}</title></head><body>Some article body text.</body></html>")

    monkeypatch.setattr(admin_module.httpx, "get", _fake_get)

    resp = admin_client.post(
        "/admin/items/batch-add",
        data={
            "urls": "\n".join(
                [
                    "https://example.com/article-1",
                    "https://example.com/article-2",
                    "https://unreachable.example.com/article-3",
                ]
            )
        },
    )
    assert resp.status_code == 200
    assert resp.text.count('class="status status-actief">OK<') == 2
    assert resp.text.count('class="status status-gedeactiveerd">Fout<') == 1
    assert "Kon content niet ophalen" in resp.text


def test_batch_add_flags_a_non_http_url_without_crashing(admin_client):
    resp = admin_client.post("/admin/items/batch-add", data={"urls": "not-a-url"})
    assert resp.status_code == 200
    assert "Ongeldige URL" in resp.text


def test_settings_page_shows_defaults_when_unauthenticated_redirects(client):
    resp = client.get("/admin/settings", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_settings_page_shows_current_values(admin_client):
    resp = admin_client.get("/admin/settings")
    assert resp.status_code == 200
    assert 'value="7"' in resp.text  # default digest_hour
    assert 'value="5"' in resp.text  # default digest_top_n


def test_settings_update_persists_and_clamps_out_of_range_values(admin_client):
    resp = admin_client.post(
        "/admin/settings",
        data={"digest_hour": "9", "digest_top_n": "8"},
    )
    assert resp.status_code == 200
    assert "Instellingen opgeslagen" in resp.text
    assert 'value="9"' in resp.text
    assert 'value="8"' in resp.text

    # Out-of-range values get clamped, not rejected with a 500/422.
    resp = admin_client.post(
        "/admin/settings",
        data={"digest_hour": "99", "digest_top_n": "0"},
    )
    assert resp.status_code == 200
    assert 'value="23"' in resp.text  # clamped to max
    assert 'value="1"' in resp.text  # clamped to min

    check_resp = admin_client.get("/settings/digest")
    assert check_resp.json()["digest_hour"] == 23
    assert check_resp.json()["digest_top_n"] == 1


def test_send_now_relays_to_scheduler_and_reports_success(admin_client, monkeypatch):
    import app.admin as admin_module

    captured = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

    def _fake_post(url, timeout):
        captured["url"] = url
        return _FakeResponse()

    monkeypatch.setattr(admin_module.httpx, "post", _fake_post)
    monkeypatch.setattr(admin_module.settings, "scheduler_url", "http://scheduler:8001")

    resp = admin_client.post("/admin/settings/send-now")

    assert resp.status_code == 200
    assert captured["url"] == "http://scheduler:8001/trigger/daily-digest"
    assert "Digest-run gestart" in resp.text


def test_send_now_shows_error_when_scheduler_unreachable(admin_client, monkeypatch):
    import httpx

    import app.admin as admin_module

    def _fake_post(url, timeout):
        raise httpx.ConnectError("connection failed", request=httpx.Request("POST", url))

    monkeypatch.setattr(admin_module.httpx, "post", _fake_post)

    resp = admin_client.post("/admin/settings/send-now")

    assert resp.status_code == 200
    assert "Kon de scheduler niet bereiken" in resp.text
