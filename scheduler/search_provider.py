"""Pluggable web-search interface for weekly_discovery's market-sweep step.

A local script can't "search the internet" on its own — it needs a search API
with a key (Brave Search API, Bing Search API, ...). Which one to use is not
decided yet, so the concrete provider sits behind SearchProvider (same shape
as app/embeddings.py's EmbeddingProvider in the scoring-service) and
get_search_provider() returns None until SEARCH_PROVIDER/SEARCH_API_KEY are
set in .env — callers must handle that and skip the search step, not crash.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

import config


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        raise NotImplementedError


class BraveSearchProvider(SearchProvider):
    """https://api.search.brave.com/res/v1/web/search"""

    _ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        resp = httpx.get(
            self._ENDPOINT,
            params={"q": query, "count": max_results},
            headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
            timeout=15.0,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [
            SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("description", ""))
            for r in results[:max_results]
            if r.get("url")
        ]


def get_search_provider() -> SearchProvider | None:
    if config.SEARCH_PROVIDER == "brave" and config.SEARCH_API_KEY:
        return BraveSearchProvider(api_key=config.SEARCH_API_KEY)
    return None
