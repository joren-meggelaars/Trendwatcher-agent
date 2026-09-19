import httpx
import pytest

import config
import runtime_settings

# Must never be applied from the admin GUI. If one of these ever ends up in
# EDITABLE, this file fails.
FORBIDDEN = {
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "SEARCH_API_KEY", "SEARCH_PROVIDER",
    "DIGEST_MAILBOX", "DIGEST_TO_EMAIL", "MAILBOX_INGEST_FOLDER", "FEEDBACK_BASE_URL",
    "SCORING_SERVICE_URL", "TRIGGER_SERVER_PORT", "SETTINGS_SYNC_INTERVAL_SECONDS", "SEEN_ITEMS_PATH",
    "DIGEST_HOUR", "DIGEST_TOP_N", "SAFE_FETCH_ALLOW_PRIVATE",
}


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch):
    """apply() sets attributes on the config module: start every test from
    "config equals what .env gave" (the conftest zeroes the scoring pause, and a
    local .env may differ) and hand every value back afterwards."""
    for key in runtime_settings.EDITABLE:
        monkeypatch.setattr(config, key, runtime_settings._BASELINE[key])
    for key in FORBIDDEN:
        if hasattr(config, key):
            monkeypatch.setattr(config, key, getattr(config, key))


def _serve(monkeypatch, overrides):
    monkeypatch.setattr(runtime_settings, "fetch_overrides", lambda: overrides)


# --- the allow-list -----------------------------------------------------------


def test_no_secret_or_routing_setting_is_in_the_allow_list():
    assert set(runtime_settings.EDITABLE) & FORBIDDEN == set()


def test_every_editable_setting_exists_in_config():
    for key in runtime_settings.EDITABLE:
        assert hasattr(config, key), key


def test_a_forbidden_key_in_the_served_overrides_is_ignored(monkeypatch):
    before = {key: getattr(config, key) for key in FORBIDDEN if hasattr(config, key)}
    different = runtime_settings._BASELINE["SCORE_REQUEST_DELAY_SECONDS"] + 1
    _serve(monkeypatch, {key: "evil" for key in FORBIDDEN} | {"SCORE_REQUEST_DELAY_SECONDS": different})

    changed = runtime_settings.apply()

    assert changed == {"SCORE_REQUEST_DELAY_SECONDS"}  # the allow-listed one is applied, the rest ignored
    assert {key: getattr(config, key) for key in before} == before


# --- applying ------------------------------------------------------------------


def test_override_is_applied_with_the_right_type(monkeypatch):
    _serve(
        monkeypatch,
        # an int for a float setting, values that certainly differ from any .env
        {"SCORE_REQUEST_DELAY_SECONDS": 37, "DIGEST_DRY_RUN": not runtime_settings._BASELINE["DIGEST_DRY_RUN"],
         "INGEST_INTERVAL_MINUTES": 7777 % 1440, "DISCOVERY_DAY": "xyz-not-checked-here"},
    )

    changed = runtime_settings.apply()

    assert config.SCORE_REQUEST_DELAY_SECONDS == 37.0 and isinstance(config.SCORE_REQUEST_DELAY_SECONDS, float)
    assert config.DIGEST_DRY_RUN is (not runtime_settings._BASELINE["DIGEST_DRY_RUN"])
    assert config.INGEST_INTERVAL_MINUTES == 7777 % 1440
    assert config.DISCOVERY_DAY == "xyz-not-checked-here"  # the scheduler trusts the service to validate values
    assert {"SCORE_REQUEST_DELAY_SECONDS", "DIGEST_DRY_RUN"} <= changed


def test_removing_an_override_goes_back_to_the_env_value(monkeypatch):
    baseline = runtime_settings._BASELINE["MAX_NEW_ENTRIES_PER_SOURCE"]
    _serve(monkeypatch, {"MAX_NEW_ENTRIES_PER_SOURCE": baseline + 5})
    assert runtime_settings.apply() == {"MAX_NEW_ENTRIES_PER_SOURCE"}
    assert config.MAX_NEW_ENTRIES_PER_SOURCE == baseline + 5

    _serve(monkeypatch, {})
    assert runtime_settings.apply() == {"MAX_NEW_ENTRIES_PER_SOURCE"}

    assert config.MAX_NEW_ENTRIES_PER_SOURCE == baseline


def test_nothing_reported_as_changed_when_nothing_changed(monkeypatch):
    _serve(monkeypatch, {})

    assert runtime_settings.apply() == set()
    assert runtime_settings.apply() == set()


@pytest.mark.parametrize(
    "key, bad",
    [
        ("INGEST_INTERVAL_MINUTES", "10"),  # a string where an int is expected
        ("INGEST_INTERVAL_MINUTES", True),  # bool is not an int here
        ("DIGEST_DRY_RUN", "false"),
        ("SCORE_REQUEST_DELAY_SECONDS", True),
        ("DISCOVERY_DAY", 3),
    ],
)
def test_a_value_of_the_wrong_type_is_ignored(monkeypatch, key, bad):
    before = getattr(config, key)
    _serve(monkeypatch, {key: bad})

    assert runtime_settings.apply() == set()
    assert getattr(config, key) == before


def test_when_the_scoring_service_is_unreachable_nothing_changes(monkeypatch):
    def down(*args, **kwargs):
        raise httpx.ConnectError("down", request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr(runtime_settings.httpx, "get", down)
    before = config.INGEST_INTERVAL_MINUTES

    assert runtime_settings.fetch_overrides() is None
    assert runtime_settings.apply() == set()
    assert config.INGEST_INTERVAL_MINUTES == before


# --- reporting the .env baseline ------------------------------------------------


def test_report_sends_only_the_allow_listed_settings_as_text(monkeypatch):
    sent = {}

    class _Ok:
        def raise_for_status(self) -> None:
            pass

    def fake_post(url, json, timeout):
        sent["url"], sent["json"] = url, json
        return _Ok()

    monkeypatch.setattr(runtime_settings.httpx, "post", fake_post)

    runtime_settings.report_baseline()

    assert sent["url"].endswith("/settings/runtime/env")
    values = sent["json"]["values"]
    assert set(values) == set(runtime_settings.EDITABLE)  # no secret can be in here
    assert all(isinstance(v, str) for v in values.values())
    assert values["DIGEST_DRY_RUN"] in ("true", "false")  # bools as lowercase text


def test_report_survives_an_unreachable_scoring_service(monkeypatch):
    def down(*args, **kwargs):
        raise httpx.ConnectError("down", request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(runtime_settings.httpx, "post", down)

    runtime_settings.report_baseline()  # must not raise


def test_baseline_reflects_env_not_the_applied_override(monkeypatch):
    key = "MAX_NEW_ENTRIES_PER_SOURCE"
    env_value = runtime_settings._BASELINE[key]
    _serve(monkeypatch, {key: env_value + 1})
    runtime_settings.apply()

    assert config.MAX_NEW_ENTRIES_PER_SOURCE == env_value + 1
    assert runtime_settings._BASELINE[key] == env_value
