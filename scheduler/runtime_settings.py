"""Applies the settings an admin changed in the GUI (/admin/config) to the
running scheduler, without a restart.

.env stays the baseline: the values config.py read at startup are remembered
here. A value overridden in the GUI is fetched from the scoring-service over
HTTP (the scheduler never touches the database) and set on the `config`
module — every job reads `config.X` at the moment it needs it, so the change is
live. An override that is removed goes back to the .env value.

EDITABLE is a deliberate allow-list. It is the scheduler's half of a
two-key safeguard: the scoring-service also only stores and serves the keys
in its own registry (scoring-service/app/runtime_settings.py). Secrets and
anything that decides where mail goes, what is read or how services find each
other are in neither list, so they can only be changed in .env on the VM. Do
not add such a setting here.
"""

import logging

import httpx

import config

logger = logging.getLogger(__name__)

EDITABLE = (
    "SCORE_REQUEST_DELAY_SECONDS",
    "INGEST_INTERVAL_MINUTES",
    "MAX_NEW_ENTRIES_PER_SOURCE",
    "DIGEST_DRY_RUN",
    "DIGEST_LOOKBACK_DAYS",
    "MANUAL_DIGEST_LOOKBACK_DAYS",
    "DISCOVERY_DAY",
    "DISCOVERY_HOUR",
    "DISCOVERY_LOOKBACK_DAYS",
    "DISCOVERY_MAX_SEARCH_TERMS",
    "DISCOVERY_RESULTS_PER_TERM",
    "MAILBOX_INGEST_ENABLED",
    "MAILBOX_INGEST_MAX_MESSAGES",
    "MAILBOX_INGEST_HOUR",
)

# What .env gave at startup, and the type each setting has.
_BASELINE = {key: getattr(config, key) for key in EDITABLE}


def _as_text(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _coerce(key: str, value):
    """The override as the same type as the .env value, or ValueError."""
    baseline = _BASELINE[key]
    if isinstance(baseline, bool):
        if isinstance(value, bool):
            return value
    elif isinstance(baseline, int):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif isinstance(baseline, float):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif isinstance(baseline, str):
        if isinstance(value, str):
            return value
    raise ValueError(f"unexpected value {value!r} for {key}")


def report_baseline() -> None:
    """Tell the scoring-service which .env values this scheduler runs with, so
    the admin GUI can show what is really in effect. Display only."""
    try:
        resp = httpx.post(
            f"{config.SCORING_SERVICE_URL}/settings/runtime/env",
            json={"values": {key: _as_text(value) for key, value in _BASELINE.items()}},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Kon de .env-waarden niet melden aan de scoring-service.", exc_info=True)


def fetch_overrides() -> dict | None:
    """The GUI overrides, or None when the scoring-service can't be reached
    (then nothing is changed: the current values stay)."""
    try:
        resp = httpx.get(f"{config.SCORING_SERVICE_URL}/settings/runtime", timeout=10.0)
        resp.raise_for_status()
        return resp.json()["overrides"]
    except (httpx.HTTPError, KeyError, ValueError):
        logger.warning("Kon de instellingen uit de admin-GUI niet ophalen; houd de huidige aan.", exc_info=True)
        return None


def apply() -> set[str]:
    """Bring `config` in line with the GUI overrides. Returns the keys whose
    value changed (so main.py can reschedule the jobs that depend on them)."""
    overrides = fetch_overrides()
    if overrides is None:
        return set()

    changed = set()
    for key in EDITABLE:  # keys outside the allow-list in `overrides` are ignored
        target = _BASELINE[key]
        if key in overrides:
            try:
                target = _coerce(key, overrides[key])
            except ValueError:
                logger.warning("Ongeldige waarde uit de admin-GUI genegeerd voor %s: %r", key, overrides[key])
        if getattr(config, key) != target:
            setattr(config, key, target)
            changed.add(key)

    if changed:
        logger.info(
            "Instellingen uit de admin-GUI toegepast: %s",
            {key: getattr(config, key) for key in sorted(changed)},
        )
    return changed


def sync() -> set[str]:
    report_baseline()
    return apply()
