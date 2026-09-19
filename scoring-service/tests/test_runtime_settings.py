import re

import pytest

from app import models, runtime_settings
from app.config import settings

# Everything that must never be editable from the GUI, whatever else changes.
# If a spec is ever added under one of these names, this test fails.
FORBIDDEN_KEYS = {
    "VOYAGE_API_KEY", "VOYAGE_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_DIM", "DEFAULT_USER_ID",
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "SEARCH_API_KEY", "SEARCH_PROVIDER",
    "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "SESSION_SECRET_KEY", "DATABASE_URL",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    "DIGEST_MAILBOX", "DIGEST_TO_EMAIL", "MAILBOX_INGEST_FOLDER", "FEEDBACK_BASE_URL",
    "SCORING_SERVICE_URL", "SCHEDULER_URL", "TRIGGER_SERVER_PORT", "BIND_ADDRESS",
    "SETTINGS_SYNC_INTERVAL_SECONDS", "SEEN_ITEMS_PATH", "DIGEST_HOUR", "DIGEST_TOP_N",
}
_SUSPICIOUS = re.compile(r"KEY|SECRET|PASSWORD|TOKEN|HASH|TENANT|CLIENT|DATABASE|MAILBOX(?!_INGEST)|EMAIL|URL|FOLDER|MODEL|DIM|USER", re.I)


# --- the registry ------------------------------------------------------------


def test_no_secret_or_routing_setting_is_editable():
    keys = {spec.key for spec in runtime_settings.SPECS}
    assert keys & FORBIDDEN_KEYS == set()
    assert [k for k in keys if _SUSPICIOUS.search(k)] == []


def test_every_forbidden_setting_is_explained_as_console_only():
    listed = " ".join(item.name for item in runtime_settings.CONSOLE_ONLY)
    for key in FORBIDDEN_KEYS - {"POSTGRES_DB", "DATABASE_URL"}:  # the two POSTGRES/DATABASE lines share entries
        assert key.split("_")[0] in listed or key in listed, key


def test_spec_keys_are_unique_and_scoring_specs_point_at_real_settings():
    keys = [spec.key for spec in runtime_settings.SPECS]
    assert len(keys) == len(set(keys))
    for spec in runtime_settings.SPECS:
        if spec.owner == "scoring":
            assert hasattr(settings, spec.attr), spec.key


def test_defaults_are_valid_for_their_own_spec():
    for spec in runtime_settings.SPECS:
        assert runtime_settings.parse(spec, runtime_settings.to_text(spec.default)) == spec.default, spec.key


# --- parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    "key, raw, expected",
    [
        ("INGEST_INTERVAL_MINUTES", " 15 ", 15),
        ("SCORE_REQUEST_DELAY_SECONDS", "21", 21.0),
        ("SCORE_REQUEST_DELAY_SECONDS", "0,5", 0.5),  # Dutch decimal comma
        ("DIGEST_DRY_RUN", "false", False),
        ("DIGEST_DRY_RUN", "aan", True),
        ("DISCOVERY_DAY", "FRI", "fri"),
    ],
)
def test_parse_accepts(key, raw, expected):
    assert runtime_settings.parse(runtime_settings.spec_for(key), raw) == expected


@pytest.mark.parametrize(
    "key, raw",
    [
        ("INGEST_INTERVAL_MINUTES", "0"),  # below minimum
        ("INGEST_INTERVAL_MINUTES", "5000"),  # above maximum
        ("INGEST_INTERVAL_MINUTES", "2.5"),  # not an integer
        ("INGEST_INTERVAL_MINUTES", "abc"),
        ("SCORE_REQUEST_DELAY_SECONDS", "-1"),
        ("DIGEST_DRY_RUN", "misschien"),
        ("DISCOVERY_DAY", "monday"),
        ("SOURCE_ACTIVATION_SCORE_THRESHOLD", "1.5"),
    ],
)
def test_parse_rejects(key, raw):
    with pytest.raises(ValueError):
        runtime_settings.parse(runtime_settings.spec_for(key), raw)


# --- saving ------------------------------------------------------------------


def test_save_sets_changes_and_clears_overrides(db_session):
    changed, errors = runtime_settings.save_overrides(
        db_session, {"SCORE_REQUEST_DELAY_SECONDS": "21", "MAX_NEW_ENTRIES_PER_SOURCE": "5"}
    )
    assert errors == {} and set(changed) == {"SCORE_REQUEST_DELAY_SECONDS", "MAX_NEW_ENTRIES_PER_SOURCE"}
    assert runtime_settings.overrides_for_scheduler(db_session) == {
        "SCORE_REQUEST_DELAY_SECONDS": 21.0,
        "MAX_NEW_ENTRIES_PER_SOURCE": 5,
    }

    changed, _ = runtime_settings.save_overrides(db_session, {"SCORE_REQUEST_DELAY_SECONDS": ""})  # follow .env again
    assert changed == ["SCORE_REQUEST_DELAY_SECONDS"]
    assert runtime_settings.overrides_for_scheduler(db_session) == {"MAX_NEW_ENTRIES_PER_SOURCE": 5}


def test_saving_the_same_values_again_changes_nothing(db_session):
    runtime_settings.save_overrides(db_session, {"INGEST_INTERVAL_MINUTES": "10"})

    changed, errors = runtime_settings.save_overrides(
        db_session, {"INGEST_INTERVAL_MINUTES": "10", "DISCOVERY_HOUR": ""}
    )

    assert changed == [] and errors == {}


def test_one_invalid_value_saves_nothing_at_all(db_session):
    changed, errors = runtime_settings.save_overrides(
        db_session, {"INGEST_INTERVAL_MINUTES": "10", "DISCOVERY_HOUR": "99"}
    )

    assert changed == [] and set(errors) == {"DISCOVERY_HOUR"}
    assert runtime_settings.overrides_for_scheduler(db_session) == {}


def test_a_rule_that_can_never_be_met_is_rejected(db_session):
    # env: window 5, min high scores 3 -> asking for 6 of 5 is a mistake
    _changed, errors = runtime_settings.save_overrides(db_session, {"SOURCE_ACTIVATION_MIN_HIGH_SCORE": "6"})
    assert "SOURCE_ACTIVATION_MIN_HIGH_SCORE" in errors

    # ... but fine when the window is raised in the same save
    changed, errors = runtime_settings.save_overrides(
        db_session, {"SOURCE_ACTIVATION_MIN_HIGH_SCORE": "6", "SOURCE_ACTIVATION_WINDOW": "10"}
    )
    assert errors == {} and len(changed) == 2


def test_only_the_scheduler_settings_are_served_to_the_scheduler(db_session):
    runtime_settings.save_overrides(
        db_session, {"SOURCE_ACTIVATION_WINDOW": "7", "INGEST_INTERVAL_MINUTES": "10"}
    )

    assert runtime_settings.overrides_for_scheduler(db_session) == {"INGEST_INTERVAL_MINUTES": 10}


# --- baseline reported by the scheduler -------------------------------------


def test_env_baseline_is_stored_for_scheduler_keys_only(db_session):
    stored = runtime_settings.store_env_baseline(
        db_session,
        {
            "SCORE_REQUEST_DELAY_SECONDS": "21.0",
            "SOURCE_ACTIVATION_WINDOW": "99",  # belongs to this service: ignored
            "GRAPH_CLIENT_SECRET": "should-never-be-stored",  # not in the registry: ignored
            "INGEST_INTERVAL_MINUTES": "not-a-number",  # unparsable: ignored
        },
    )

    assert stored == 1
    assert {row.key for row in db_session.query(models.RuntimeSetting).all()} == {"SCORE_REQUEST_DELAY_SECONDS"}


def test_describe_all_shows_reported_env_and_own_env_and_the_override(db_session):
    runtime_settings.store_env_baseline(db_session, {"SCORE_REQUEST_DELAY_SECONDS": "21.0"})
    runtime_settings.save_overrides(db_session, {"SCORE_REQUEST_DELAY_SECONDS": "0", "INGEST_INTERVAL_MINUTES": "10"})

    rows = {row.spec.key: row for row in runtime_settings.describe_all(db_session)}

    delay = rows["SCORE_REQUEST_DELAY_SECONDS"]
    assert (delay.env, delay.override, delay.effective) == (21.0, 0.0, 0.0)
    ingest = rows["INGEST_INTERVAL_MINUTES"]  # scheduler never reported it -> env unknown, spec default shown
    assert (ingest.env, ingest.override, ingest.effective) == (None, 10, 10)
    own = rows["SOURCE_ACTIVATION_WINDOW"]
    assert own.env == settings.source_activation_window and own.override is None
    assert own.effective == settings.source_activation_window


# --- used by this service ----------------------------------------------------


def test_override_changes_the_source_activation_rule_and_clearing_restores_env(client, db_session):
    source = client.post("/sources", json={"url": "https://tune.example.com", "type": "blog"}).json()
    liked = client.post(
        "/score",
        json={"source": "t", "title": "seed", "url": "https://x/seed", "raw_content": "Cisco router firmware vulnerability"},
    ).json()
    client.post("/feedback", json={"item_id": liked["item_id"], "label": "interessant"})
    for i in range(5):
        client.post(
            "/score",
            json={
                "source": "t", "title": f"n{i}", "url": f"https://x/{i}", "source_id": source["id"],
                "raw_content": "Cisco router firmware vulnerability allows remote code execution",
            },
        )

    runtime_settings.save_overrides(db_session, {"SOURCE_ACTIVATION_SCORE_THRESHOLD": "1"})  # nothing scores above 1
    assert client.post(f"/sources/{source['id']}/evaluate").json()["status"] == "kandidaat"

    runtime_settings.save_overrides(db_session, {"SOURCE_ACTIVATION_SCORE_THRESHOLD": ""})  # back to .env (0.6)
    assert client.post(f"/sources/{source['id']}/evaluate").json()["status"] == "actief"


# --- JSON API for the scheduler ----------------------------------------------


def test_api_serves_typed_overrides_for_the_scheduler(client, db_session):
    assert client.get("/settings/runtime").json() == {"overrides": {}}

    runtime_settings.save_overrides(
        db_session, {"DIGEST_DRY_RUN": "false", "INGEST_INTERVAL_MINUTES": "10", "SCORE_REQUEST_DELAY_SECONDS": "0,5"}
    )

    assert client.get("/settings/runtime").json() == {
        "overrides": {"DIGEST_DRY_RUN": False, "INGEST_INTERVAL_MINUTES": 10, "SCORE_REQUEST_DELAY_SECONDS": 0.5}
    }


def test_api_env_report_cannot_set_an_override(client, db_session):
    resp = client.post(
        "/settings/runtime/env",
        json={"values": {"SCORE_REQUEST_DELAY_SECONDS": "21.0", "GRAPH_CLIENT_SECRET": "nope"}},
    )

    assert resp.status_code == 200 and resp.json() == {"stored": 1}
    assert client.get("/settings/runtime").json() == {"overrides": {}}  # a report never changes behaviour
    keys = {row.key for row in db_session.query(models.RuntimeSetting).all()}
    assert "GRAPH_CLIENT_SECRET" not in keys
