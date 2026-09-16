"""Plain-module configuration, loaded from .env via python-dotenv.

No pydantic-settings dependency here on purpose: the scheduler is meant to
run as a lean, independently deployable component (eventually an Azure
Function / Container App on its own), so it only pulls in what jobs/*.py
actually needs (feedparser, httpx, apscheduler, python-dotenv).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SCORING_SERVICE_URL = os.environ.get("SCORING_SERVICE_URL", "http://127.0.0.1:8000").rstrip("/")

# SMTP: leave SMTP_HOST empty to log the digest to the console instead of sending it.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
DIGEST_FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", SMTP_USER or "trendwatcher@localhost")
DIGEST_TO_EMAIL = os.environ.get("DIGEST_TO_EMAIL", "")

DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "7"))
DIGEST_TOP_N = int(os.environ.get("DIGEST_TOP_N", "5"))

# APScheduler day-of-week name, e.g. "mon", "tue", ... "sun".
DISCOVERY_DAY = os.environ.get("DISCOVERY_DAY", "mon")
DISCOVERY_HOUR = int(os.environ.get("DISCOVERY_HOUR", "8"))
DISCOVERY_LOOKBACK_DAYS = int(os.environ.get("DISCOVERY_LOOKBACK_DAYS", "30"))
DISCOVERY_MAX_SEARCH_TERMS = int(os.environ.get("DISCOVERY_MAX_SEARCH_TERMS", "5"))
DISCOVERY_RESULTS_PER_TERM = int(os.environ.get("DISCOVERY_RESULTS_PER_TERM", "5"))

# "" (not yet chosen) | "brave" — see search_provider.py. SEARCH_API_KEY is required
# once a provider is chosen; without it weekly_discovery skips the search step.
SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY", "")

_seen_items_path = os.environ.get("SEEN_ITEMS_PATH", "").strip()
SEEN_ITEMS_PATH = (
    Path(_seen_items_path) if _seen_items_path else Path(__file__).resolve().parent / "data" / "seen_items.json"
)
