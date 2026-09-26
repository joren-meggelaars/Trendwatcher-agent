"""Plain-module configuration, loaded from .env via python-dotenv.

No pydantic-settings dependency here on purpose: the scheduler is meant to
run as a lean, independently deployable component (eventually an Azure
Function / Container App on its own), so it only pulls in what jobs/*.py
actually needs (feedparser, httpx, apscheduler, python-dotenv).
"""

import logging
import os
from datetime import timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()

SCORING_SERVICE_URL = os.environ.get("SCORING_SERVICE_URL", "http://127.0.0.1:8000").rstrip("/")

# The zone the schedule is read in: "digest at 7" means 07:00 here, not 07:00 UTC (the
# containers run on UTC). The admin GUI shows times in the same zone (its TIMEZONE).
TIMEZONE = os.environ.get("TIMEZONE", "").strip() or "Europe/Amsterdam"


def zone() -> tzinfo:
    """TIMEZONE as a tzinfo; an unknown name falls back to UTC (with a warning) instead of crashing."""
    try:
        return ZoneInfo(TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        logging.getLogger(__name__).warning("Onbekende tijdzone TIMEZONE=%r, UTC wordt gebruikt.", TIMEZONE)
        return timezone.utc

# Base URL used for the 👍/👎 links *inside the digest email*. Must be
# reachable from the reader's browser, so it can't be the internal Docker
# hostname SCORING_SERVICE_URL points at (http://scoring-service:8000 only
# resolves inside the compose network). Empty -> fall back to
# SCORING_SERVICE_URL, which is fine when running everything locally.
FEEDBACK_BASE_URL = (os.environ.get("FEEDBACK_BASE_URL", "").strip() or SCORING_SERVICE_URL).rstrip("/")

# Microsoft Graph (client-credentials / app-only flow) — mail goes through
# Graph's sendMail, not SMTP/IMAP. The app registration needs application
# permission Mail.Send (with admin consent) on GRAPH_TENANT_ID.
GRAPH_TENANT_ID = os.environ.get("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.environ.get("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = os.environ.get("GRAPH_CLIENT_SECRET", "")

# Mailbox the Graph app sends as (POST /users/{DIGEST_MAILBOX}/sendMail).
DIGEST_MAILBOX = os.environ.get("DIGEST_MAILBOX", "")
DIGEST_TO_EMAIL = os.environ.get("DIGEST_TO_EMAIL", "")

# True (default): log the composed digest to the console instead of actually
# calling Graph's sendMail — safe default before the app registration exists.
DIGEST_DRY_RUN = os.environ.get("DIGEST_DRY_RUN", "true").strip().lower() not in ("false", "0", "no")

DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "7"))
DIGEST_TOP_N = int(os.environ.get("DIGEST_TOP_N", "5"))
# APScheduler day-of-week list, e.g. "mon,tue,wed,thu,fri" (comma-separated, no ranges — kept in sync
# with app/digest_settings.py's canonical format on the scoring-service side).
DIGEST_DAYS = os.environ.get("DIGEST_DAYS", "mon,tue,wed,thu,fri")

# Minimum spacing between POST /score calls, in seconds, shared by all jobs
# that score (see rate_limit.py). Default 0 (no delay); 21 for Voyage's
# free-tier limit of 3 req/min when EMBEDDING_PROVIDER=voyage on the
# scoring-service — not needed once a payment method is on file.
SCORE_REQUEST_DELAY_SECONDS = float(os.environ.get("SCORE_REQUEST_DELAY_SECONDS", "0"))

# jobs/ingest.py (fetch feeds + score) runs continuously in the background at
# this interval; the daily digest only mails what is already scored. The first
# ingest runs one interval after the scheduler starts, not at startup.
INGEST_INTERVAL_MINUTES = int(os.environ.get("INGEST_INTERVAL_MINUTES", "30"))

# Per source and per ingest run, score at most this many of the newest unseen
# feed entries (0 = no limit). Protects against a newly added source with a big
# feed (NCSC: hundreds, IETF: ~600 entries) hogging the scoring for hours;
# older unseen entries are marked seen, not scored.
MAX_NEW_ENTRIES_PER_SOURCE = int(os.environ.get("MAX_NEW_ENTRIES_PER_SOURCE", "10"))

# The scheduled digest picks the best not-yet-mailed items scored within this
# many days (a few days, so a failed digest run does not lose its items).
DIGEST_LOOKBACK_DAYS = int(os.environ.get("DIGEST_LOOKBACK_DAYS", "3"))

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

# --- mailbox_ingest ---
# Reads unread mail from DIGEST_MAILBOX (same mailbox the digest is sent
# from) via Graph and scores each message, for newsletter-only sources that
# have no RSS feed at all (tl;dr sec, Risky Business News, SANS NewsBites, ...).
# Off by default: sending mail (Mail.Send) and reading it (Mail.Read) are
# separate Graph application permissions, so enabling this requires granting
# Mail.Read admin consent too, not just having DIGEST_MAILBOX set.
MAILBOX_INGEST_ENABLED = os.environ.get("MAILBOX_INGEST_ENABLED", "false").strip().lower() not in (
    "false", "0", "no",
)
MAILBOX_INGEST_FOLDER = os.environ.get("MAILBOX_INGEST_FOLDER", "inbox")
MAILBOX_INGEST_MAX_MESSAGES = int(os.environ.get("MAILBOX_INGEST_MAX_MESSAGES", "25"))
MAILBOX_INGEST_HOUR = int(os.environ.get("MAILBOX_INGEST_HOUR", "6"))

# Switches off the "only public addresses" check for everything the scheduler
# downloads (safe_fetch.py). Local testing only; console-only, never on the VM.
SAFE_FETCH_ALLOW_PRIVATE = os.environ.get("SAFE_FETCH_ALLOW_PRIVATE", "false").strip().lower() in ("1", "true", "yes")

# How far back (days) the admin GUI's "verstuur nu" digest looks for the best
# already-scored items (see daily_digest.run_now). It doesn't score anything.
MANUAL_DIGEST_LOOKBACK_DAYS = int(os.environ.get("MANUAL_DIGEST_LOOKBACK_DAYS", "7"))

# --- internal trigger server ---
# Lets the admin GUI (scoring-service) POST /trigger/daily-digest for the
# "verstuur nu" button. Only bound for reachability over the shared Docker
# network — never published to the host in docker-compose.yml.
TRIGGER_SERVER_PORT = int(os.environ.get("TRIGGER_SERVER_PORT", "8001"))

# How often (seconds) main.py re-checks GET /settings/digest on the
# scoring-service for a changed DIGEST_HOUR and reschedules the APScheduler
# job accordingly, so an admin-GUI change takes effect without a restart.
SETTINGS_SYNC_INTERVAL_SECONDS = int(os.environ.get("SETTINGS_SYNC_INTERVAL_SECONDS", "300"))

_seen_items_path = os.environ.get("SEEN_ITEMS_PATH", "").strip()
SEEN_ITEMS_PATH = (
    Path(_seen_items_path) if _seen_items_path else Path(__file__).resolve().parent / "data" / "seen_items.json"
)
