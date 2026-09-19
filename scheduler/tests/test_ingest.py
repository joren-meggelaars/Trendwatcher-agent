import time
from types import SimpleNamespace

import httpx
import pytest

import config
import jobs.ingest as ingest


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.request = httpx.Request("POST", "http://test/score")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code} error", request=self.request, response=self)

    def json(self) -> dict:
        return self._json_data


class _FakeClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0

    def post(self, url, json):
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


_ENTRY = {
    "title": "T",
    "link": "https://example.com/a",
    "raw_content": "content",
    "source_name": "Test Source",
}
_SOURCE = {"url": "https://source.example.com", "id": 1}


def test_score_entry_succeeds_first_try(monkeypatch):
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    client = _FakeClient([_FakeResponse(200, {"item_id": 1, "summary": "s", "relevance_score": 0.7})])

    result = ingest.score_entry(client, _ENTRY, _SOURCE)

    assert result["item_id"] == 1
    assert client.calls == 1


def test_score_entry_retries_on_500_then_succeeds(monkeypatch):
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    client = _FakeClient(
        [_FakeResponse(500), _FakeResponse(200, {"item_id": 2, "summary": "s", "relevance_score": 0.6})]
    )

    result = ingest.score_entry(client, _ENTRY, _SOURCE)

    assert result["item_id"] == 2
    assert client.calls == 2


def test_score_entry_retries_on_connection_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    request = httpx.Request("POST", "http://test/score")
    client = _FakeClient(
        [
            httpx.ReadError("connection forcibly closed by the remote host", request=request),
            _FakeResponse(200, {"item_id": 3, "summary": "s", "relevance_score": 0.5}),
        ]
    )

    result = ingest.score_entry(client, _ENTRY, _SOURCE)

    assert result["item_id"] == 3
    assert client.calls == 2


def test_score_entry_gives_up_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    client = _FakeClient([_FakeResponse(500), _FakeResponse(500), _FakeResponse(500)])

    with pytest.raises(httpx.HTTPStatusError):
        ingest.score_entry(client, _ENTRY, _SOURCE)

    assert client.calls == 3  # 1 initial attempt + 2 retries, matching _SCORE_RETRY_DELAYS


def test_score_entry_does_not_retry_on_4xx(monkeypatch):
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    client = _FakeClient([_FakeResponse(404)])

    with pytest.raises(httpx.HTTPStatusError):
        ingest.score_entry(client, _ENTRY, _SOURCE)

    assert client.calls == 1


class _RecordingSeen:
    def __init__(self, already_seen=()):
        self._seen = {("1", url) for url in already_seen}
        self.marked = []

    def has_seen(self, source_id, url) -> bool:
        return (str(source_id), url) in self._seen

    def mark_seen(self, source_id, url) -> None:
        self.marked.append(url)


def _entry(n: int, day: int | None):
    entry = {"title": f"item {n}", "link": f"https://example.com/{n}", "summary": f"text {n}"}
    if day is not None:
        entry["published_parsed"] = time.struct_time((2026, 9, day, 12, 0, 0, 0, 0, 0))
    return entry


def _patch_feed(monkeypatch, entries, limit):
    parsed = SimpleNamespace(feed={"title": "Test Feed"}, entries=entries)
    monkeypatch.setattr(ingest.feedparser, "parse", lambda url: parsed)
    monkeypatch.setattr(config, "MAX_NEW_ENTRIES_PER_SOURCE", limit)


_SOURCE = {"id": 1, "url": "https://example.com/feed"}


def test_keeps_only_the_newest_entries_and_marks_the_older_ones_seen(monkeypatch):
    # Feed order is deliberately not chronological.
    _patch_feed(monkeypatch, [_entry(1, 1), _entry(2, 9), _entry(3, 5), _entry(4, 7)], limit=2)
    seen = _RecordingSeen()

    entries = ingest.fetch_new_entries(_SOURCE, seen)

    assert [e["link"] for e in entries] == ["https://example.com/2", "https://example.com/4"]
    assert sorted(seen.marked) == ["https://example.com/1", "https://example.com/3"]
    assert entries[0] == {
        "title": "item 2", "link": "https://example.com/2", "raw_content": "text 2", "source_name": "Test Feed",
    }


def test_under_the_limit_nothing_is_dropped_or_marked(monkeypatch):
    _patch_feed(monkeypatch, [_entry(1, 1), _entry(2, 2)], limit=10)
    seen = _RecordingSeen()

    entries = ingest.fetch_new_entries(_SOURCE, seen)

    assert len(entries) == 2
    assert seen.marked == []


def test_limit_zero_means_unlimited(monkeypatch):
    _patch_feed(monkeypatch, [_entry(n, 1) for n in range(1, 30)], limit=0)
    seen = _RecordingSeen()

    assert len(ingest.fetch_new_entries(_SOURCE, seen)) == 29
    assert seen.marked == []


def test_already_seen_entries_do_not_count_towards_the_limit(monkeypatch):
    _patch_feed(monkeypatch, [_entry(1, 9), _entry(2, 8), _entry(3, 7)], limit=2)
    seen = _RecordingSeen(already_seen=["https://example.com/1", "https://example.com/2"])

    entries = ingest.fetch_new_entries(_SOURCE, seen)

    assert [e["link"] for e in entries] == ["https://example.com/3"]
    assert seen.marked == []


def test_undated_entries_keep_feed_order_after_dated_ones(monkeypatch):
    _patch_feed(monkeypatch, [_entry(1, None), _entry(2, None), _entry(3, 3)], limit=2)
    seen = _RecordingSeen()

    entries = ingest.fetch_new_entries(_SOURCE, seen)

    assert [e["link"] for e in entries] == ["https://example.com/3", "https://example.com/1"]
    assert seen.marked == ["https://example.com/2"]
