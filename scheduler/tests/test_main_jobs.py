from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
import main
from jobs import daily_digest, ingest


def _scheduler(monkeypatch, minutes=15):
    monkeypatch.setattr(config, "INGEST_INTERVAL_MINUTES", minutes)
    scheduler = BackgroundScheduler()  # not started: jobs just wait in the pending list
    main._add_jobs(scheduler)
    return scheduler


def test_all_jobs_are_registered(monkeypatch):
    scheduler = _scheduler(monkeypatch)

    ids = {job.id for job in scheduler.get_jobs()}

    assert ids == {"ingest", "daily_digest", "weekly_discovery", "mailbox_ingest", "sync_settings"}


def test_ingest_runs_continuously_and_never_overlaps_itself(monkeypatch):
    job = _scheduler(monkeypatch, minutes=15).get_job("ingest")

    assert job.func is ingest.run
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval.total_seconds() == 15 * 60
    assert job.max_instances == 1
    assert job.coalesce is True


def test_daily_digest_job_only_mails(monkeypatch):
    job = _scheduler(monkeypatch).get_job("daily_digest")

    assert job.func is daily_digest.run
    assert not hasattr(daily_digest, "score_entry") and not hasattr(daily_digest, "fetch_new_entries")


def test_daily_digest_job_defaults_to_weekdays_only(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_DAYS", "mon,tue,wed,thu,fri")
    job = _scheduler(monkeypatch).get_job("daily_digest")

    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day_of_week"] == "mon,tue,wed,thu,fri"


def test_cron_jobs_still_run_when_they_start_hours_late_instead_of_being_skipped(monkeypatch):
    """Regression: the digest was skipped because the scheduler woke 37 minutes after 07:00
    (APScheduler's default grace is 1 second), so no mail arrived that day."""
    scheduler = _scheduler(monkeypatch)

    for job_id in ("daily_digest", "weekly_discovery", "mailbox_ingest"):
        job = scheduler.get_job(job_id)
        assert job.misfire_grace_time >= 3600, job_id
        assert job.coalesce is True, job_id


def test_a_rescheduled_digest_keeps_its_grace_time(monkeypatch):
    from apscheduler.triggers.cron import CronTrigger

    scheduler = _scheduler(monkeypatch)
    scheduler.reschedule_job("daily_digest", trigger=CronTrigger(day_of_week="mon", hour=9, minute=0))

    assert scheduler.get_job("daily_digest").misfire_grace_time == main._CRON_MISFIRE_GRACE_SECONDS
