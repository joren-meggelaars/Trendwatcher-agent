"""Recognising the same article stored more than once.

Duplicates come from before the URL check existed (a lost scheduler cache made
every entry get scored again), and from feeds that republish an article under a
slightly different URL (tracking parameters, trailing slash) or list it twice.
Two items are the same article when they have the same canonical URL, or the
same title from the same source.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from app.textclean import clean_text

# Query parameters that only track where a click came from.
_TRACKING_PARAM = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|cmpid)$", re.IGNORECASE)


def canonical_url(url: str) -> str:
    """The URL without what makes two links to one page differ: scheme, "www.",
    fragment, trailing slash, tracking parameters, host case."""
    parts = urlsplit((url or "").strip())
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAM.match(k))
    )
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return f"{host}{path}" + (f"?{query}" if query else "")


def title_key(title: str) -> str:
    """The title reduced to lowercase letters and digits, after cleaning."""
    return re.sub(r"[^a-z0-9]+", "", clean_text(title).lower())


def keys(item) -> tuple[str, tuple | None]:
    """(canonical url, (source, title key)) of an item; the second is None for
    an empty title, which says nothing about identity."""
    tkey = title_key(item.title)
    return canonical_url(item.url), ((item.source_id or item.source, tkey) if tkey else None)


class Deduper:
    """Walk items best-first; `is_duplicate` is True for one that repeats an
    item already passed through, and registers the ones that don't."""

    def __init__(self) -> None:
        self._urls: set[str] = set()
        self._titles: set[tuple] = set()

    def is_duplicate(self, item) -> bool:
        url_key, title = keys(item)
        if url_key in self._urls or (title is not None and title in self._titles):
            return True
        self._urls.add(url_key)
        if title is not None:
            self._titles.add(title)
        return False
