"""Daily job: fetch active sources' RSS feeds, score new entries, email a digest.

Runs standalone for testing:

    uv run python -m jobs.daily_digest
"""

import html
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import httpx

import config
from graph_client import get_graph_token
from remote_settings import fetch_digest_settings
from seen_items import SeenItemsCache

DIGEST_SUBJECT = "Security Trendwatch — dagelijkse digest"

# The digest has one section per category, each with its own top-N. The
# scoring-service tags every item "markt" (funding, overnames, marktcijfers,
# ...) or "nieuws" (app/classification.py) and scores each category against
# your 👍/👎 in that category only. Order here = order in the mail.
DIGEST_SECTIONS = (
    ("markt", "Marktontwikkeling"),
    ("nieuws", "Nieuws"),
)

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
    free-tier rate — and the digest is only sent after that. So only the
    newest N are kept; the older unseen ones are marked as seen without being
    scored, so that backlog is not re-attempted every day. 0 = no limit.
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
    since retrying won't change the outcome.
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


def _safe_link(url: str, label: str) -> str:
    """Render an <a> tag, escaping text/attribute content — RSS content comes
    from third-party feeds we don't control. Falls back to plain escaped text
    if the URL isn't http(s), so a malicious feed can't smuggle in a
    javascript: link or break out of the attribute.
    """
    escaped_label = html.escape(label)
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{html.escape(url, quote=True)}">{escaped_label}</a>'
    return escaped_label


def _section_html(heading: str, items: list[dict]) -> str:
    if not items:
        return f"<h2>{html.escape(heading)}</h2><p>Geen nieuwe items in deze categorie.</p>"

    rows = []
    for item in items:
        interessant_url = (
            f"{config.FEEDBACK_BASE_URL}/feedback-link?item_id={item['item_id']}&label=interessant"
        )
        niet_url = (
            f"{config.FEEDBACK_BASE_URL}/feedback-link?item_id={item['item_id']}&label=niet_interessant"
        )
        rows.append(
            "<tr>"
            f"<td>{_safe_link(item['url'], item['title'])}</td>"
            f"<td>{_safe_link(item['source_url'], item['source_name'])}</td>"
            f"<td>{html.escape(item['summary'])}</td>"
            f"<td>{item['relevance_score']:.2f}</td>"
            f'<td><a href="{html.escape(interessant_url, quote=True)}">\U0001F44D</a> '
            f'<a href="{html.escape(niet_url, quote=True)}">\U0001F44E</a></td>'
            "</tr>"
        )
    return (
        f"<h2>{html.escape(heading)}</h2>"
        '<table border="1" cellpadding="6" cellspacing="0">'
        "<tr><th>Titel</th><th>Bron</th><th>Samenvatting</th><th>Score</th><th>Feedback</th></tr>"
        f"{''.join(rows)}"
        "</table>"
    )


def build_digest_html(items_by_category: dict[str, list[dict]]) -> str:
    """One mail, one section per DIGEST_SECTIONS entry (an empty one says so)."""
    sections = "".join(
        _section_html(heading, items_by_category.get(category, []))
        for category, heading in DIGEST_SECTIONS
    )
    return f"<html><body><h1>{DIGEST_SUBJECT}</h1>{sections}</body></html>"


def send_digest(html_body: str) -> None:
    if config.DIGEST_DRY_RUN or not config.DIGEST_MAILBOX or not config.DIGEST_TO_EMAIL:
        logger.info(
            "digest_dry_run staat aan (of DIGEST_MAILBOX/DIGEST_TO_EMAIL ontbreekt) — "
            "digest wordt naar console gelogd in plaats van verstuurd via Graph.\n"
            "Onderwerp: %s\nAan: %s\n%s",
            DIGEST_SUBJECT, config.DIGEST_TO_EMAIL, html_body,
        )
        return

    token = get_graph_token()
    payload = {
        "message": {
            "subject": DIGEST_SUBJECT,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": config.DIGEST_TO_EMAIL}}],
        },
        "saveToSentItems": "false",
    }
    resp = httpx.post(
        f"https://graph.microsoft.com/v1.0/users/{config.DIGEST_MAILBOX}/sendMail",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    logger.info("Digest verzonden via Graph naar %s", config.DIGEST_TO_EMAIL)


def run() -> None:
    seen = SeenItemsCache.load()

    with httpx.Client(timeout=30.0) as client:
        sources = fetch_active_sources(client)
        if not sources:
            logger.info("Geen actieve bronnen gevonden — geen digest te versturen.")
            return

        scored_items = []
        for source in sources:
            for entry in fetch_new_entries(source, seen):
                try:
                    scored_items.append(score_entry(client, entry, source))
                except httpx.HTTPError:
                    logger.exception(
                        "Kon item definitief niet scoren (na retries): %s "
                        "(len(raw_content)=%d, tijdstip=%s)",
                        entry["link"], len(entry["raw_content"]), datetime.now(timezone.utc).isoformat(),
                    )
                    continue
                finally:
                    if config.SCORE_REQUEST_DELAY_SECONDS:
                        time.sleep(config.SCORE_REQUEST_DELAY_SECONDS)
                seen.mark_seen(source["id"], entry["link"])

        seen.save()

        if not scored_items:
            logger.info("Geen nieuwe items gevonden in de actieve bronnen.")
            return

        digest_top_n = fetch_digest_settings()["digest_top_n"]
        top_by_category = {}
        for category, _heading in DIGEST_SECTIONS:
            in_category = [i for i in scored_items if i.get("category") == category]
            top_by_category[category] = sorted(
                in_category, key=lambda i: i["relevance_score"], reverse=True
            )[:digest_top_n]
            logger.info("%d van %d nieuwe items in categorie %s.", len(in_category), len(scored_items), category)

        send_digest(build_digest_html(top_by_category))


def _source_name(source_url: str) -> str:
    """Readable label for a source when only its URL is known (no feed title
    is stored in the database): host without "www.", or the sender address
    for mailbox sources ("mailto:sender@example.com")."""
    if source_url.lower().startswith("mailto:"):
        return source_url[len("mailto:"):]
    host = urlparse(source_url).netloc.lower()
    if host.startswith("www."):
        host = host[len("www."):]
    return host or source_url


def run_now() -> None:
    """Immediate digest: mails the best already-scored items from the last
    MANUAL_DIGEST_LOOKBACK_DAYS days without fetching feeds or scoring
    anything, so it doesn't wait on the (rate-limited) scoring step. Used by
    the admin GUI's "verstuur nu" button; the scheduled run() is unchanged.
    Same layout as the daily digest: top-N per category."""
    digest_top_n = fetch_digest_settings()["digest_top_n"]
    top_by_category = {}
    for category, _heading in DIGEST_SECTIONS:
        try:
            resp = httpx.get(
                f"{config.SCORING_SERVICE_URL}/items/top",
                params={
                    "days": config.MANUAL_DIGEST_LOOKBACK_DAYS,
                    "limit": digest_top_n,
                    "category": category,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Kon /items/top (%s) niet ophalen — geen digest verstuurd.", category)
            return

        items = resp.json()
        for item in items:
            item["source_name"] = _source_name(item["source_url"])
        top_by_category[category] = items

    total = sum(len(items) for items in top_by_category.values())
    if total == 0:
        logger.info(
            "Geen gescoorde items in de laatste %d dagen — geen digest verstuurd.",
            config.MANUAL_DIGEST_LOOKBACK_DAYS,
        )
        return

    logger.info(
        "Handmatige digest: %s uit de laatste %d dagen.",
        ", ".join(f"{len(items)} {category}" for category, items in top_by_category.items()),
        config.MANUAL_DIGEST_LOOKBACK_DAYS,
    )
    send_digest(build_digest_html(top_by_category))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
