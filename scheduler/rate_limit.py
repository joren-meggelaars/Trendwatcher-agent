"""Process-wide pacing of POST /score calls.

Every job that scores (ingest, mailbox_ingest, weekly_discovery) ends up on the
same Voyage API key via the scoring-service, and APScheduler runs jobs in
parallel threads — so a pause inside each job is not enough: two jobs would
each be "within the limit" and together exceed it. This keeps a minimum
spacing of SCORE_REQUEST_DELAY_SECONDS between the *starts* of scoring calls
across all jobs in the process (0 = no pacing, the default; 21 for Voyage's
free tier of 3 requests per minute).

Call wait_for_scoring_slot() right before every POST /score.
"""

import threading
import time

import config

_lock = threading.Lock()
_last_call = 0.0  # time.monotonic() of the last slot handed out


def wait_for_scoring_slot() -> None:
    delay = config.SCORE_REQUEST_DELAY_SECONDS
    if not delay:
        return

    global _last_call
    # Sleeping while holding the lock is the point: concurrent callers queue up
    # and are released one per `delay` seconds.
    with _lock:
        remaining = _last_call + delay - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        _last_call = time.monotonic()


def reset() -> None:
    """Forget the last call (for tests)."""
    global _last_call
    _last_call = 0.0
