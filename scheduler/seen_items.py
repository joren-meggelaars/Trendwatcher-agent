"""Local dedup cache so daily_digest doesn't re-score the same RSS entry every run.

A JSON file mapping source_id -> list of seen entry URLs. Deliberately simple:
Fase 1 has no "already scored this URL" check in the scoring-service itself,
and a full dedup index there is out of scope for the scheduler's first cut.
"""

import json
from pathlib import Path

import config


class SeenItemsCache:
    def __init__(self, path: Path, data: dict[str, list[str]]) -> None:
        self._path = path
        self._data = data

    @classmethod
    def load(cls, path: Path | None = None) -> "SeenItemsCache":
        path = path or config.SEEN_ITEMS_PATH
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = {}
        return cls(path, data)

    def has_seen(self, source_id: int, url: str) -> bool:
        return url in self._data.get(str(source_id), [])

    def mark_seen(self, source_id: int, url: str) -> None:
        self._data.setdefault(str(source_id), []).append(url)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data), encoding="utf-8")
