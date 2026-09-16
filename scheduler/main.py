"""APScheduler entrypoint: starts daily_digest and weekly_discovery on their
configured schedules. Each job is also runnable standalone for testing, e.g.:

    uv run python -m jobs.daily_digest
    uv run python -m jobs.weekly_discovery
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from jobs import daily_digest, weekly_discovery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


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
    logger.info(
        "Scheduler gestart: daily_digest dagelijks om %02d:00, weekly_discovery op %s om %02d:00",
        config.DIGEST_HOUR, config.DISCOVERY_DAY, config.DISCOVERY_HOUR,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
