"""Continuous job: fetch active sources' RSS feeds and score new entries.

Runs every INGEST_INTERVAL_MINUTES (see main.py), so the slow, rate-limited
part — scoring — happens in the background all day. daily_digest then only
mails what is already scored and never waits on Voyage.

Runs standalone for testing:

    uv run python -m jobs.ingest
"""

import logging
import time
from datetime import datetime, timezone

import feedparser
import httpx

import config
import rate_limit
from seen_items import SeenItemsCache

# score_entry retries a failed POST /score up to twice, waiting these many
# seconds before each retry, before giving up on the item.
_SCORE_RETRY_DELAYS = (1, 3)

logger = logging.getLogger(__name__)


def fetch_active_sources(client: httpx.Client) -> list[dict]:
    resp = client.get(f"{config.SCORING_SERVICE_URL}/sources", params={"status": "actief"})
    resp.raise_for_status()
    return resp.json()


def fetch_new_entries(source: dict, seen: SeenItemsCache) -> list[dict]:
    """Unseen entries of one feed, at most MAX_NEW_ENTRIES_PER_SOURCE of them.

    A source that is new to the seen-cache would otherwise have its whole feed
    (hundreds of entries for some) scored in one run — hours at the Voyage
    free-tier rate. So only the newest N are kept; the older unseen ones are
    marked as seen without being scored, so that backlog is not re-attempted
    every run. 0 = no limit.
    """
    parsed = feedparser.parse(source["url"])
    source_name = parsed.feed.get("title") or source["url"]
    new_entries = []  # (published, entry)
    for entry in parsed.entries:
        link = entry.get("link", "")
        if not link or seen.has_seen(source["id"], link):
            continue
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        new_entries.append(
            (
                tuple(published) if published else (),
                {
                    "title": entry.get("title") or "(geen titel)",
                    "link": link,
                    "raw_content": entry.get("summary") or entry.get("title") or "",
                    "source_name": source_name,
                },
            )
        )

    limit = config.MAX_NEW_ENTRIES_PER_SOURCE
    if limit and len(new_entries) > limit:
        # Newest first; entries without a date keep their feed order, after the dated ones.
        new_entries.sort(key=lambda pair: pair[0], reverse=True)
        for _published, skipped in new_entries[limit:]:
            seen.mark_seen(source["id"], skipped["link"])
        logger.info(
            "%s: %d ongeziene items, alleen de nieuwste %d worden gescoord (MAX_NEW_ENTRIES_PER_SOURCE); "
            "de %d oudere zijn als gezien gemarkeerd.",
            source["url"], len(new_entries), limit, len(new_entries) - limit,
        )
        new_entries = new_entries[:limit]
    return [entry for _published, entry in new_entries]


def score_entry(client: httpx.Client, entry: dict, source: dict) -> dict:
    """POST /score for one RSS entry, retrying transient failures.

    Retries up to len(_SCORE_RETRY_DELAYS) times (with backoff) on a
    connection-level error or a 5xx response — the kind of failure that can
    come from a hiccup in the connection rather than a genuine problem with
    this item. A 4xx response (e.g. an unknown source_id) is not retried,
    since retrying won't change the outcome. Every attempt waits for its slot
    in the shared pacing (rate_limit.py).
    """
    payload = {
        "source": source["url"],
        "title": entry["title"],
        "url": entry["link"],
        "raw_content": entry["raw_content"],
        "source_id": source["id"],
    }

    attempts = len(_SCORE_RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        try:
            rate_limit.wait_for_scoring_slot()
            resp = client.post(f"{config.SCORING_SERVICE_URL}/score", json=payload)
            resp.raise_for_status()
            result = resp.json()
            result["title"] = entry["title"]
            result["url"] = entry["link"]
            result["source_name"] = entry["source_name"]
            result["source_url"] = source["url"]
            return result
        except httpx.HTTPError as exc:
            is_last_attempt = attempt == attempts
            is_client_error = (
                isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500
            )
            logger.warning(
                "POST /score mislukt (poging %d/%d, %s) voor %s "
                "(len(raw_content)=%d, tijdstip=%s)",
                attempt, attempts, exc, entry["link"],
                len(entry["raw_content"]), datetime.now(timezone.utc).isoformat(),
            )
            if is_client_error or is_last_attempt:
                raise
            time.sleep(_SCORE_RETRY_DELAYS[attempt - 1])

    raise AssertionError("unreachable")  # loop always returns or raises


def rescore(client: httpx.Client) -> int | None:
    """Ask the scoring-service to recompute stored items' scores against the
    current 👍/👎 (from the stored embeddings: no Voyage requests, and a no-op
    while the thumbs are unchanged). Runs after every ingest, so a thumb given
    in the mail or on the review page reaches items scored before it. Returns
    how many items changed, or None when it could not be done — that never
    fails the ingest."""
    try:
        resp = client.post(f"{config.SCORING_SERVICE_URL}/items/rescore", timeout=300.0)
        resp.raise_for_status()
        return resp.json()["updated"]
    except (httpx.HTTPError, KeyError, ValueError):
        logger.warning("Kon de scores niet opnieuw laten berekenen; volgende ingest-run probeert het weer.", exc_info=True)
        return None


def run() -> None:
    seen = SeenItemsCache.load()
    scored = failed = 0

    try:
        with httpx.Client(timeout=30.0) as client:
            try:
                sources = fetch_active_sources(client)
            except httpx.HTTPError:
                logger.exception("Kon de actieve bronnen niet ophalen — ingest overgeslagen.")
                return
            if not sources:
                logger.info("Geen actieve bronnen gevonden — niets te ingesten.")
                return

            for source in sources:
                try:
                    for entry in fetch_new_entries(source, seen):
                        try:
                            score_entry(client, entry, source)
                        except httpx.HTTPError:
                            failed += 1
                            logger.exception(
                                "Kon item definitief niet scoren (na retries): %s "
                                "(len(raw_content)=%d, tijdstip=%s)",
                                entry["link"], len(entry["raw_content"]), datetime.now(timezone.utc).isoformat(),
                            )
                            continue
                        scored += 1
                        seen.mark_seen(source["id"], entry["link"])
                except Exception:  # noqa: BLE001 — one broken feed must not stop the other sources
                    logger.exception("Bron overgeslagen door een fout: %s", source["url"])
                # After every source, not only at the end: a long run that is
                # interrupted (deploy, restart) keeps the progress it made.
                seen.save()

            rescored = rescore(client)
    finally:
        seen.save()

    logger.info(
        "Ingest klaar: %d item(s) gescoord, %d mislukt%s.",
        scored, failed, "" if rescored is None else f", {rescored} score(s) bijgewerkt door feedback",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
