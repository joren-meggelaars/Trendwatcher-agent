"""APScheduler entrypoint: starts ingest (continuously), daily_digest,
weekly_discovery and mailbox_ingest on their configured schedules. Each job is
also runnable standalone for testing, e.g.:

    uv run python -m jobs.ingest
    uv run python -m jobs.daily_digest
    uv run python -m jobs.weekly_discovery
    uv run python -m jobs.mailbox_ingest
"""

import logging
import threading

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
import runtime_settings
import trigger_server
from jobs import daily_digest, ingest, mailbox_ingest, weekly_discovery
from remote_settings import fetch_digest_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_current_digest_hour = config.DIGEST_HOUR
_current_digest_days = config.DIGEST_DAYS
_sync_lock = threading.Lock()  # the sync tick and a "save" from the admin GUI can coincide

# When each job runs, built from `config` at the moment it is needed — so
# startup and a later settings change (see _sync_settings) always agree.
# daily_digest is not here: its hour and days come from the digest settings page.
_TRIGGERS = {
    "ingest": lambda: IntervalTrigger(minutes=config.INGEST_INTERVAL_MINUTES),
    "weekly_discovery": lambda: CronTrigger(
        day_of_week=config.DISCOVERY_DAY, hour=config.DISCOVERY_HOUR, minute=0
    ),
    "mailbox_ingest": lambda: CronTrigger(hour=config.MAILBOX_INGEST_HOUR, minute=0),
}
# Which settings a job's schedule depends on: when one changes, it is rescheduled.
_SCHEDULE_SETTINGS = {
    "ingest": {"INGEST_INTERVAL_MINUTES"},
    "weekly_discovery": {"DISCOVERY_DAY", "DISCOVERY_HOUR"},
    "mailbox_ingest": {"MAILBOX_INGEST_HOUR"},
}


def _sync_settings(scheduler: BlockingScheduler) -> None:
    """Polled every SETTINGS_SYNC_INTERVAL_SECONDS, and run right away when the
    admin GUI saves: picks up changes made there without a container restart.

    - Settings from /admin/config are applied to `config` (runtime_settings.py);
      jobs whose schedule depends on one are rescheduled. Everything else is
      read by the jobs at the moment they run, so it is live as soon as it is set.
    - The digest hour and days (/admin/settings) are handled here too.
      digest_top_n needs nothing: the digest fetches it fresh on every run.
    """
    global _current_digest_hour, _current_digest_days
    with _sync_lock:
        changed = runtime_settings.sync()
        for job_id, keys in _SCHEDULE_SETTINGS.items():
            if changed & keys:
                logger.info("Planning van %s gewijzigd (%s), herplannen.", job_id, ", ".join(sorted(changed & keys)))
                scheduler.reschedule_job(job_id, trigger=_TRIGGERS[job_id]())

        digest_settings = fetch_digest_settings()
        new_hour, new_days = digest_settings["digest_hour"], digest_settings["digest_days"]
        if new_hour != _current_digest_hour or new_days != _current_digest_days:
            logger.info(
                "Digest-planning gewijzigd: %s om %02d:00 -> %s om %02d:00, herplannen.",
                _current_digest_days, _current_digest_hour, new_days, new_hour,
            )
            scheduler.reschedule_job("daily_digest", trigger=CronTrigger(day_of_week=new_days, hour=new_hour, minute=0))
            _current_digest_hour, _current_digest_days = new_hour, new_days


# A cron job that starts late (the VM was paused for a backup, the container was
# busy or restarting) still runs if it is at most this late; APScheduler's default
# is 1 second, after which the run is silently skipped ("was missed by") and, for
# the digest, no mail arrives that day. Several late runs collapse into one
# (coalesce), and the digest only mails what was not mailed yet, so a late run
# never sends anything twice.
_CRON_MISFIRE_GRACE_SECONDS = 6 * 3600


def _add_jobs(scheduler: BlockingScheduler) -> None:
    # Scoring is the slow, rate-limited part, so it runs all day in the
    # background; the daily digest below only mails what is already scored.
    # max_instances=1 + coalesce: a run that outlasts the interval (a big
    # backlog on the free Voyage tier) makes the next tick(s) skip, not pile up.
    scheduler.add_job(
        ingest.run,
        _TRIGGERS["ingest"](),
        id="ingest",
        name="Feeds ophalen en scoren",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        daily_digest.run,
        CronTrigger(day_of_week=config.DIGEST_DAYS, hour=config.DIGEST_HOUR, minute=0),
        id="daily_digest",
        name="Dagelijkse digest",
        misfire_grace_time=_CRON_MISFIRE_GRACE_SECONDS,
        coalesce=True,
    )
    scheduler.add_job(
        weekly_discovery.run,
        _TRIGGERS["weekly_discovery"](),
        id="weekly_discovery",
        name="Wekelijkse discovery-sweep",
        misfire_grace_time=_CRON_MISFIRE_GRACE_SECONDS,
        coalesce=True,
    )
    scheduler.add_job(
        mailbox_ingest.run,
        _TRIGGERS["mailbox_ingest"](),
        id="mailbox_ingest",
        name="Mailbox-ingest (nieuwsbrieven)",
        misfire_grace_time=_CRON_MISFIRE_GRACE_SECONDS,
        coalesce=True,
    )
    scheduler.add_job(
        _sync_settings,
        "interval",
        seconds=config.SETTINGS_SYNC_INTERVAL_SECONDS,
        args=[scheduler],
        id="sync_settings",
        name="Instellingen synchroniseren vanuit admin-GUI",
    )


def main() -> None:
    # Apply what was set in the admin GUI before the jobs are scheduled, so a
    # changed interval/hour is used from the very first schedule. The
    # scoring-service is up (compose waits for its healthcheck); if it is not,
    # the .env values are used until the first sync tick.
    runtime_settings.sync()

    scheduler = BlockingScheduler()
    _add_jobs(scheduler)

    # Keep a reference so the server (and its background thread) isn't
    # garbage-collected once main() returns control to scheduler.start().
    _trigger_server_handle = trigger_server.start(  # noqa: F841
        config.TRIGGER_SERVER_PORT,
        daily_digest.run,
        daily_digest.run_now,
        lambda: _sync_settings(scheduler),
    )

    logger.info(
        "Scheduler gestart: ingest elke %d min (eerste run na dat interval), daily_digest op %s om %02d:00, "
        "weekly_discovery op %s om %02d:00, mailbox_ingest dagelijks om %02d:00 (enabled=%s), "
        "trigger-server op poort %d",
        config.INGEST_INTERVAL_MINUTES, config.DIGEST_DAYS, config.DIGEST_HOUR, config.DISCOVERY_DAY, config.DISCOVERY_HOUR,
        config.MAILBOX_INGEST_HOUR, config.MAILBOX_INGEST_ENABLED, config.TRIGGER_SERVER_PORT,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
