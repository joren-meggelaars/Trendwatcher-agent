"""APScheduler entrypoint: starts daily_digest, weekly_discovery and
mailbox_ingest on their configured schedules. Each job is also runnable
standalone for testing, e.g.:

    uv run python -m jobs.daily_digest
    uv run python -m jobs.weekly_discovery
    uv run python -m jobs.mailbox_ingest
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import trigger_server
from jobs import daily_digest, mailbox_ingest, weekly_discovery
from remote_settings import fetch_digest_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_current_digest_hour = config.DIGEST_HOUR


def _sync_digest_schedule(scheduler: BlockingScheduler) -> None:
    """Polled every SETTINGS_SYNC_INTERVAL_SECONDS: picks up an admin-GUI
    change to the digest hour without needing a container restart. digest_top_n
    doesn't need this — daily_digest.run() fetches it fresh on every run."""
    global _current_digest_hour
    new_hour = fetch_digest_settings()["digest_hour"]
    if new_hour != _current_digest_hour:
        logger.info("Digest-uur gewijzigd: %d -> %d, herplannen.", _current_digest_hour, new_hour)
        scheduler.reschedule_job("daily_digest", trigger=CronTrigger(hour=new_hour, minute=0))
        _current_digest_hour = new_hour


def main() -> None:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        daily_digest.run,
        CronTrigger(hour=config.DIGEST_HOUR, minute=0),
        id="daily_digest",
        name="Dagelijkse digest",
    )
    scheduler.add_job(
        weekly_discovery.run,
        CronTrigger(day_of_week=config.DISCOVERY_DAY, hour=config.DISCOVERY_HOUR, minute=0),
        id="weekly_discovery",
        name="Wekelijkse discovery-sweep",
    )
    scheduler.add_job(
        mailbox_ingest.run,
        CronTrigger(hour=config.MAILBOX_INGEST_HOUR, minute=0),
        id="mailbox_ingest",
        name="Mailbox-ingest (nieuwsbrieven)",
    )
    scheduler.add_job(
        _sync_digest_schedule,
        "interval",
        seconds=config.SETTINGS_SYNC_INTERVAL_SECONDS,
        args=[scheduler],
        id="sync_digest_schedule",
        name="Digest-uur synchroniseren vanuit admin-GUI",
    )

    # Keep a reference so the server (and its background thread) isn't
    # garbage-collected once main() returns control to scheduler.start().
    _trigger_server_handle = trigger_server.start(config.TRIGGER_SERVER_PORT, daily_digest.run)  # noqa: F841

    logger.info(
        "Scheduler gestart: daily_digest dagelijks om %02d:00, weekly_discovery op %s om %02d:00, "
        "mailbox_ingest dagelijks om %02d:00 (enabled=%s), trigger-server op poort %d",
        config.DIGEST_HOUR, config.DISCOVERY_DAY, config.DISCOVERY_HOUR,
        config.MAILBOX_INGEST_HOUR, config.MAILBOX_INGEST_ENABLED, config.TRIGGER_SERVER_PORT,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
