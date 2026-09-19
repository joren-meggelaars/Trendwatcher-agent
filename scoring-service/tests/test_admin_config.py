import httpx

import app.admin as admin_module
from app import runtime_settings
from app.config import settings


class _Ok:
    def raise_for_status(self) -> None:
        pass


def _capture_scheduler_pings(monkeypatch, *, fail=False):
    pings = []

    def _fake_post(url, timeout):
        pings.append(url)
        if fail:
            raise httpx.ConnectError("down", request=httpx.Request("POST", url))
        return _Ok()

    monkeypatch.setattr(admin_module.httpx, "post", _fake_post)
    monkeypatch.setattr(admin_module.settings, "scheduler_url", "http://scheduler:8001")
    return pings


def test_config_page_requires_login(client):
    for method in (client.get, client.post):
        resp = method("/admin/config", follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/admin/login"


def test_config_page_lists_every_editable_setting_and_the_console_only_ones(admin_client):
    resp = admin_client.get("/admin/config")

    assert resp.status_code == 200
    for spec in runtime_settings.SPECS:
        assert spec.key in resp.text, spec.key
    assert "Alleen via de console" in resp.text
    assert "VOYAGE_API_KEY" in resp.text and "FEEDBACK_BASE_URL" in resp.text  # named, with a reason
    assert 'href="/admin/config"' in resp.text  # menu item


def test_config_page_never_shows_a_secret_value(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "sk-very-secret-voyage-key")
    monkeypatch.setattr(settings, "admin_password_hash", "$2b$12$very-secret-hash")
    monkeypatch.setattr(settings, "session_secret_key", "very-secret-session-key")
    monkeypatch.setattr(settings, "database_url", "postgresql://user:very-secret-db-pass@db/x")

    text = admin_module_render_text(admin_client)

    for secret in ("very-secret-voyage-key", "very-secret-hash", "very-secret-session-key", "very-secret-db-pass"):
        assert secret not in text


def admin_module_render_text(admin_client) -> str:
    return admin_client.get("/admin/config").text


def test_saving_an_override_shows_it_marked_and_pings_the_scheduler(admin_client, monkeypatch):
    pings = _capture_scheduler_pings(monkeypatch)

    resp = admin_client.post("/admin/config", data={"s__SCORE_REQUEST_DELAY_SECONDS": "21"})

    assert resp.status_code == 200
    assert "Opgeslagen (1 wijziging)" in resp.text
    assert "direct overgenomen" in resp.text
    assert pings == ["http://scheduler:8001/trigger/sync-settings"]
    assert "overschreven" in resp.text
    assert admin_client.get("/settings/runtime").json() == {"overrides": {"SCORE_REQUEST_DELAY_SECONDS": 21.0}}


def test_a_blank_field_goes_back_to_following_env(admin_client, monkeypatch):
    _capture_scheduler_pings(monkeypatch)
    admin_client.post("/admin/config", data={"s__INGEST_INTERVAL_MINUTES": "10"})

    resp = admin_client.post("/admin/config", data={"s__INGEST_INTERVAL_MINUTES": ""})

    assert "Opgeslagen (1 wijziging)" in resp.text
    assert admin_client.get("/settings/runtime").json() == {"overrides": {}}


def test_invalid_input_saves_nothing_and_keeps_what_was_typed(admin_client, monkeypatch):
    pings = _capture_scheduler_pings(monkeypatch)

    resp = admin_client.post(
        "/admin/config", data={"s__INGEST_INTERVAL_MINUTES": "10", "s__DISCOVERY_HOUR": "99"}
    )

    assert resp.status_code == 200
    assert "Niets opgeslagen" in resp.text and "moet tussen 0 en 23 liggen" in resp.text
    assert 'value="99"' in resp.text and 'value="10"' in resp.text  # the form is shown again as typed
    assert admin_client.get("/settings/runtime").json() == {"overrides": {}}
    assert pings == []


def test_unchanged_form_reports_no_changes_and_does_not_ping(admin_client, monkeypatch):
    pings = _capture_scheduler_pings(monkeypatch)

    resp = admin_client.post("/admin/config", data={"s__INGEST_INTERVAL_MINUTES": ""})

    assert "Geen wijzigingen" in resp.text and pings == []


def test_a_change_to_this_services_own_setting_does_not_need_the_scheduler(admin_client, monkeypatch):
    pings = _capture_scheduler_pings(monkeypatch)

    resp = admin_client.post("/admin/config", data={"s__SOURCE_ACTIVATION_SCORE_THRESHOLD": "0,7"})

    assert "Opgeslagen (1 wijziging)" in resp.text and pings == []
    assert admin_client.get("/settings/runtime").json() == {"overrides": {}}  # not a scheduler setting


def test_an_unreachable_scheduler_still_saves_and_says_when_it_takes_effect(admin_client, monkeypatch):
    _capture_scheduler_pings(monkeypatch, fail=True)

    resp = admin_client.post("/admin/config", data={"s__DIGEST_DRY_RUN": "false"})

    assert "Opgeslagen (1 wijziging)" in resp.text
    assert "niet bereikbaar" in resp.text and "binnen enkele minuten" in resp.text
    assert admin_client.get("/settings/runtime").json() == {"overrides": {"DIGEST_DRY_RUN": False}}


def test_the_form_cannot_be_used_to_set_a_setting_that_is_not_in_the_registry(admin_client, monkeypatch):
    _capture_scheduler_pings(monkeypatch)

    resp = admin_client.post(
        "/admin/config",
        data={"s__GRAPH_CLIENT_SECRET": "evil", "s__DIGEST_TO_EMAIL": "evil@example.com", "s__FEEDBACK_BASE_URL": "http://evil"},
    )

    assert "Geen wijzigingen" in resp.text
    assert admin_client.get("/settings/runtime").json() == {"overrides": {}}
