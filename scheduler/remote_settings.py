"""Fetches digest settings (hour, top-N) from the scoring-service's
GET /settings/digest — the admin GUI writes there, this reads it, so an
admin-GUI change takes effect without editing .env or restarting anything.

Falls back to the static config.py defaults if the scoring-service can't be
reached (e.g. during startup ordering, or a transient network blip) — a
missing settings fetch should never crash a job.
"""

import logging

import httpx

import config

logger = logging.getLogger(__name__)


def fetch_digest_settings() -> dict:
    try:
        resp = httpx.get(f"{config.SCORING_SERVICE_URL}/settings/digest", timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.warning(
            "Kon /settings/digest niet ophalen, val terug op DIGEST_HOUR=%d / DIGEST_TOP_N=%d uit config.",
            config.DIGEST_HOUR, config.DIGEST_TOP_N,
        )
        return {"digest_hour": config.DIGEST_HOUR, "digest_top_n": config.DIGEST_TOP_N}
