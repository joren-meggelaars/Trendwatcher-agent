import httpx
import pytest

import jobs.ingest as ingest


class _FakeSeen:
    def __init__(self):
        self.marked = []
        self.saves = 0

    def mark_seen(self, source_id, link) -> None:
        self.marked.append(link)

    def save(self) -> None:
        self.saves += 1


def _patch_run(monkeypatch, sources, entries_by_source, scorer):
    seen = _FakeSeen()
    monkeypatch.setattr(ingest.SeenItemsCache, "load", classmethod(lambda cls: seen))
    monkeypatch.setattr(ingest, "fetch_active_sources", lambda client: sources)
    monkeypatch.setattr(ingest, "fetch_new_entries", lambda source, _seen: entries_by_source[source["id"]](source))
    monkeypatch.setattr(ingest, "score_entry", scorer)
    return seen


def _entries(*links):
    return lambda source: [
        {"title": link, "link": link, "raw_content": "x", "source_name": "S"} for link in links
    ]


def test_run_scores_every_new_entry_marks_it_seen_and_saves_after_each_source(monkeypatch):
    scored = []
    seen = _patch_run(
        monkeypatch,
        [{"id": 1, "url": "https://a"}, {"id": 2, "url": "https://b"}],
        {1: _entries("a1", "a2"), 2: _entries("b1")},
        lambda client, entry, source: scored.append(entry["link"]) or {},
    )

    ingest.run()

    assert scored == ["a1", "a2", "b1"]
    assert seen.marked == ["a1", "a2", "b1"]
    assert seen.saves >= 2  # after each source, so an interrupted run keeps its progress


def test_run_leaves_a_failed_entry_unseen_so_it_is_retried_and_continues(monkeypatch):
    def scorer(client, entry, source):
        if entry["link"] == "a1":
            raise httpx.ConnectError("down", request=httpx.Request("POST", "http://test/score"))
        return {}

    seen = _patch_run(monkeypatch, [{"id": 1, "url": "https://a"}], {1: _entries("a1", "a2")}, scorer)

    ingest.run()

    assert seen.marked == ["a2"]


def test_run_survives_a_source_that_blows_up(monkeypatch):
    def broken(source):
        raise RuntimeError("feed exploded")

    seen = _patch_run(
        monkeypatch,
        [{"id": 1, "url": "https://bad"}, {"id": 2, "url": "https://good"}],
        {1: broken, 2: _entries("g1")},
        lambda client, entry, source: {},
    )

    ingest.run()

    assert seen.marked == ["g1"]


def test_run_does_nothing_when_the_scoring_service_is_unreachable(monkeypatch):
    def unreachable(client):
        raise httpx.ConnectError("down", request=httpx.Request("GET", "http://test/sources"))

    monkeypatch.setattr(ingest.SeenItemsCache, "load", classmethod(lambda cls: _FakeSeen()))
    monkeypatch.setattr(ingest, "fetch_active_sources", unreachable)

    ingest.run()  # must not raise


def test_run_does_nothing_without_active_sources(monkeypatch):
    seen = _patch_run(monkeypatch, [], {}, lambda *a: pytest.fail("nothing should be scored"))

    ingest.run()

    assert seen.marked == []
