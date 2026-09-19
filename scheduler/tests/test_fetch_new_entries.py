import time
from types import SimpleNamespace

import config
import jobs.daily_digest as daily_digest


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
    monkeypatch.setattr(daily_digest.feedparser, "parse", lambda url: parsed)
    monkeypatch.setattr(config, "MAX_NEW_ENTRIES_PER_SOURCE", limit)


_SOURCE = {"id": 1, "url": "https://example.com/feed"}


def test_keeps_only_the_newest_entries_and_marks_the_older_ones_seen(monkeypatch):
    # Feed order is deliberately not chronological.
    _patch_feed(monkeypatch, [_entry(1, 1), _entry(2, 9), _entry(3, 5), _entry(4, 7)], limit=2)
    seen = _RecordingSeen()

    entries = daily_digest.fetch_new_entries(_SOURCE, seen)

    assert [e["link"] for e in entries] == ["https://example.com/2", "https://example.com/4"]
    assert sorted(seen.marked) == ["https://example.com/1", "https://example.com/3"]
    assert entries[0] == {
        "title": "item 2", "link": "https://example.com/2", "raw_content": "text 2", "source_name": "Test Feed",
    }


def test_under_the_limit_nothing_is_dropped_or_marked(monkeypatch):
    _patch_feed(monkeypatch, [_entry(1, 1), _entry(2, 2)], limit=10)
    seen = _RecordingSeen()

    entries = daily_digest.fetch_new_entries(_SOURCE, seen)

    assert len(entries) == 2
    assert seen.marked == []


def test_limit_zero_means_unlimited(monkeypatch):
    _patch_feed(monkeypatch, [_entry(n, 1) for n in range(1, 30)], limit=0)
    seen = _RecordingSeen()

    assert len(daily_digest.fetch_new_entries(_SOURCE, seen)) == 29
    assert seen.marked == []


def test_already_seen_entries_do_not_count_towards_the_limit(monkeypatch):
    _patch_feed(monkeypatch, [_entry(1, 9), _entry(2, 8), _entry(3, 7)], limit=2)
    seen = _RecordingSeen(already_seen=["https://example.com/1", "https://example.com/2"])

    entries = daily_digest.fetch_new_entries(_SOURCE, seen)

    assert [e["link"] for e in entries] == ["https://example.com/3"]
    assert seen.marked == []


def test_undated_entries_keep_feed_order_after_dated_ones(monkeypatch):
    _patch_feed(monkeypatch, [_entry(1, None), _entry(2, None), _entry(3, 3)], limit=2)
    seen = _RecordingSeen()

    entries = daily_digest.fetch_new_entries(_SOURCE, seen)

    assert [e["link"] for e in entries] == ["https://example.com/3", "https://example.com/1"]
    assert seen.marked == ["https://example.com/2"]
