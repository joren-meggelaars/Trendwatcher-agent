"""Weekly job: distill search terms from recently-liked items, market-sweep for
new candidate sources, then re-evaluate every source's instroom/krimp status.

Runs standalone for testing:

    uv run python -m jobs.weekly_discovery
"""

import logging
import re
from collections import Counter
from urllib.parse import urlparse

import httpx

import config
import rate_limit
from search_provider import SearchResult, get_search_provider

logger = logging.getLogger(__name__)

# Small hand-picked NL/EN stopword list — no NLP dependency needed for Fase 1's
# "belangrijkste woorden uit de titels" approach.
_STOPWORDS = {
    "de", "het", "een", "in", "op", "voor", "van", "en", "te", "met", "is",
    "die", "dat", "aan", "bij", "naar", "om", "over", "als", "of", "maar",
    "wordt", "worden", "kunnen", "moet", "hun", "zijn", "hebben", "heeft",
    "dit", "deze", "niet", "ook", "nu", "meer", "nieuwe", "the", "a", "an",
    "of", "to", "for", "on", "and", "are", "new", "how", "after", "into",
}
_WORD_RE = re.compile(r"[a-zA-Z0-9\-]+")
_TAG_RE = re.compile(r"<[^>]+>")


def fetch_recent_liked_items(client: httpx.Client) -> list[dict]:
    resp = client.get(
        f"{config.SCORING_SERVICE_URL}/items/recent-feedback",
        params={"label": "interessant", "days": config.DISCOVERY_LOOKBACK_DAYS},
    )
    resp.raise_for_status()
    return resp.json()


def extract_search_terms(items: list[dict], top_n: int) -> list[str]:
    counter: Counter[str] = Counter()
    for item in items:
        for word in _WORD_RE.findall(item.get("title", "").lower()):
            if len(word) < 4 or word in _STOPWORDS:
                continue
            counter[word] += 1
    return [word for word, _ in counter.most_common(top_n)]


def _strip_html(html: str) -> str:
    return " ".join(_TAG_RE.sub(" ", html).split())


def ensure_source_for_domain(client: httpx.Client, domain: str) -> dict:
    resp = client.get(f"{config.SCORING_SERVICE_URL}/sources")
    resp.raise_for_status()
    existing = next((s for s in resp.json() if s["url"] == domain), None)
    if existing is not None:
        return existing

    resp = client.post(
        f"{config.SCORING_SERVICE_URL}/sources",
        json={"url": domain, "type": "unknown", "discovery_method": "market_sweep"},
    )
    resp.raise_for_status()
    return resp.json()


def score_search_result(client: httpx.Client, result: SearchResult, source: dict) -> dict:
    try:
        page = httpx.get(result.url, timeout=15.0, follow_redirects=True)
        page.raise_for_status()
        raw_content = _strip_html(page.text)[:5000] or result.snippet or result.title
    except httpx.HTTPError:
        logger.warning("Kon pagina niet ophalen voor %s, val terug op snippet/titel", result.url)
        raw_content = result.snippet or result.title

    payload = {
        "source": source["url"],
        "title": result.title or result.url,
        "url": result.url,
        "raw_content": raw_content,
        "source_id": source["id"],
    }
    rate_limit.wait_for_scoring_slot()
    resp = client.post(f"{config.SCORING_SERVICE_URL}/score", json=payload)
    resp.raise_for_status()
    return resp.json()


def run_market_sweep(client: httpx.Client, search_terms: list[str]) -> None:
    provider = get_search_provider()
    if provider is None:
        logger.warning(
            "Geen SearchProvider geconfigureerd (SEARCH_PROVIDER/SEARCH_API_KEY ontbreekt in .env) "
            "— zoekstap overgeslagen."
        )
        return

    for term in search_terms:
        try:
            results = provider.search(term, max_results=config.DISCOVERY_RESULTS_PER_TERM)
        except Exception:
            logger.exception("Zoekopdracht mislukt voor term %r", term)
            continue

        for result in results:
            domain = urlparse(result.url).netloc.lower().removeprefix("www.")
            if not domain:
                continue
            try:
                source = ensure_source_for_domain(client, domain)
                score_search_result(client, result, source)
            except httpx.HTTPError:
                logger.exception("Kon kandidaat-resultaat niet verwerken: %s", result.url)


def run() -> None:
    with httpx.Client(timeout=30.0) as client:
        liked_items = fetch_recent_liked_items(client)
        search_terms = extract_search_terms(liked_items, top_n=config.DISCOVERY_MAX_SEARCH_TERMS)
        logger.info(
            "Gedistilleerd uit %d 'interessant'-items van de laatste %d dagen: zoektermen %s",
            len(liked_items), config.DISCOVERY_LOOKBACK_DAYS, search_terms,
        )

        run_market_sweep(client, search_terms)

        resp = client.post(f"{config.SCORING_SERVICE_URL}/sources/evaluate-all")
        resp.raise_for_status()
        changes = resp.json()
        if changes:
            logger.info("Bronstatus gewijzigd door evaluate-all: %s", changes)
        else:
            logger.info("Geen bronstatus gewijzigd door evaluate-all.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
