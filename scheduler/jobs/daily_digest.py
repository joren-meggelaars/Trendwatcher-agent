"""Daily job: mail the best already-scored items, as one digest with a section
for market development and one for news.

This job does not fetch feeds or score anything: scoring happens continuously
in jobs/ingest.py, so the digest goes out on time however slow (rate-limited)
the scoring is. Each digest takes the best items that were not mailed before
and marks them as mailed, so the next digest moves on to new ones.

Runs standalone for testing:

    uv run python -m jobs.daily_digest
"""

import html
import logging
from urllib.parse import urlparse

import httpx

import config
from graph_client import get_graph_token
from remote_settings import fetch_digest_settings

DIGEST_SUBJECT = "Security Trendwatch — dagelijkse digest"

# The digest has one section per category, each with its own top-N. The
# scoring-service tags every item "markt" (funding, overnames, marktcijfers,
# ...) or "nieuws" (app/classification.py) and scores each category against
# your 👍/👎 in that category only. Order here = order in the mail.
DIGEST_SECTIONS = (
    ("markt", "Marktontwikkeling"),
    ("nieuws", "Nieuws"),
)

logger = logging.getLogger(__name__)


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


def send_digest(html_body: str) -> bool:
    """Mail the digest via Graph. Returns True when it was really sent, False
    for a dry run / missing mail configuration, where it is only logged."""
    if config.DIGEST_DRY_RUN or not config.DIGEST_MAILBOX or not config.DIGEST_TO_EMAIL:
        logger.info(
            "digest_dry_run staat aan (of DIGEST_MAILBOX/DIGEST_TO_EMAIL ontbreekt) — "
            "digest wordt naar console gelogd in plaats van verstuurd via Graph.\n"
            "Onderwerp: %s\nAan: %s\n%s",
            DIGEST_SUBJECT, config.DIGEST_TO_EMAIL, html_body,
        )
        return False

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
    return True


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


def _fetch_top_by_category(top_n: int, days: int, *, undigested: bool) -> dict[str, list[dict]] | None:
    """Top-N already-scored items per DIGEST_SECTIONS category, from the
    scoring-service. None if it cannot be reached (already logged)."""
    top_by_category = {}
    for category, _heading in DIGEST_SECTIONS:
        params = {"days": days, "limit": top_n, "category": category}
        if undigested:
            params["undigested"] = True
        try:
            resp = httpx.get(f"{config.SCORING_SERVICE_URL}/items/top", params=params, timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Kon /items/top (%s) niet ophalen — geen digest verstuurd.", category)
            return None

        items = resp.json()
        for item in items:
            item["source_name"] = _source_name(item["source_url"])
        top_by_category[category] = items
    return top_by_category


def run() -> None:
    """The scheduled digest: the best items of the last DIGEST_LOOKBACK_DAYS days
    that were not mailed yet, top-N per category. After a real send they are
    marked as mailed. Nothing is marked on a dry run or when sending fails, so
    the same items are tried again next time instead of getting lost."""
    top_n = fetch_digest_settings()["digest_top_n"]
    top_by_category = _fetch_top_by_category(top_n, config.DIGEST_LOOKBACK_DAYS, undigested=True)
    if top_by_category is None:
        return

    counts = {category: len(items) for category, items in top_by_category.items()}
    if not any(counts.values()):
        logger.info(
            "Geen nieuwe gescoorde items in de laatste %d dagen — geen digest verstuurd.",
            config.DIGEST_LOOKBACK_DAYS,
        )
        return
    logger.info("Digest: %s.", ", ".join(f"{n} {category}" for category, n in counts.items()))

    if not send_digest(build_digest_html(top_by_category)):
        return  # dry run: leave the items unmarked

    item_ids = [item["item_id"] for items in top_by_category.values() for item in items]
    try:
        resp = httpx.post(
            f"{config.SCORING_SERVICE_URL}/items/mark-digested", json={"item_ids": item_ids}, timeout=30.0
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.exception(
            "Digest is verstuurd, maar de items konden niet als gemaild worden gemarkeerd — "
            "ze kunnen in de volgende digest opnieuw voorkomen."
        )


def run_now() -> None:
    """Immediate digest for the admin GUI's "verstuur nu" button: the best
    items of the last MANUAL_DIGEST_LOOKBACK_DAYS days, top-N per category.
    A preview: it does not skip items mailed before and does not mark
    anything, so it never takes items away from the scheduled digest."""
    top_n = fetch_digest_settings()["digest_top_n"]
    top_by_category = _fetch_top_by_category(top_n, config.MANUAL_DIGEST_LOOKBACK_DAYS, undigested=False)
    if top_by_category is None:
        return

    if not any(top_by_category.values()):
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
