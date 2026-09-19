import threading
import urllib.request

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
import main
import trigger_server


class _FakeScheduler:
    def __init__(self):
        self.rescheduled = {}

    def reschedule_job(self, job_id, trigger):
        self.rescheduled[job_id] = trigger


def _sync(monkeypatch, changed, digest_hour=7):
    monkeypatch.setattr(main.runtime_settings, "sync", lambda: set(changed))
    monkeypatch.setattr(main, "fetch_digest_settings", lambda: {"digest_hour": digest_hour, "digest_top_n": 5})
    monkeypatch.setattr(main, "_current_digest_hour", 7)
    scheduler = _FakeScheduler()
    main._sync_settings(scheduler)
    return scheduler


def test_a_changed_ingest_interval_reschedules_only_the_ingest_job(monkeypatch):
    monkeypatch.setattr(config, "INGEST_INTERVAL_MINUTES", 10)

    scheduler = _sync(monkeypatch, {"INGEST_INTERVAL_MINUTES"})

    assert set(scheduler.rescheduled) == {"ingest"}
    trigger = scheduler.rescheduled["ingest"]
    assert isinstance(trigger, IntervalTrigger) and trigger.interval.total_seconds() == 600


def test_a_changed_discovery_day_or_hour_reschedules_discovery(monkeypatch):
    monkeypatch.setattr(config, "DISCOVERY_DAY", "fri")
    monkeypatch.setattr(config, "DISCOVERY_HOUR", 14)

    scheduler = _sync(monkeypatch, {"DISCOVERY_HOUR"})

    trigger = scheduler.rescheduled["weekly_discovery"]
    assert isinstance(trigger, CronTrigger)
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["day_of_week"] == "fri" and fields["hour"] == "14"


def test_a_changed_mailbox_hour_reschedules_the_mailbox_job(monkeypatch):
    monkeypatch.setattr(config, "MAILBOX_INGEST_HOUR", 5)

    scheduler = _sync(monkeypatch, {"MAILBOX_INGEST_HOUR"})

    assert set(scheduler.rescheduled) == {"mailbox_ingest"}


def test_settings_that_jobs_read_when_they_run_need_no_rescheduling(monkeypatch):
    scheduler = _sync(monkeypatch, {"SCORE_REQUEST_DELAY_SECONDS", "DIGEST_DRY_RUN", "MAX_NEW_ENTRIES_PER_SOURCE"})

    assert scheduler.rescheduled == {}


def test_a_changed_digest_hour_from_the_digest_page_still_reschedules_the_digest(monkeypatch):
    scheduler = _sync(monkeypatch, set(), digest_hour=9)

    assert set(scheduler.rescheduled) == {"daily_digest"}
    assert main._current_digest_hour == 9


def test_the_trigger_server_runs_the_sync_on_request():
    import socket

    called = threading.Event()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    server = trigger_server.start(port, lambda: None, None, called.set)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/trigger/sync-settings", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202
        assert called.wait(timeout=2)
    finally:
        server.shutdown()


def test_the_sync_route_is_404_when_no_target_is_configured():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    server = trigger_server.start(port, lambda: None)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/trigger/sync-settings", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
