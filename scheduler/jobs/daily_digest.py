"""Daily job: fetch active sources' RSS feeds, score new entries, email a digest.

Runs standalone for testing:

    uv run python -m jobs.daily_digest
"""

import logging
import smtplib
from email.message import EmailMessage

import feedparser
import httpx

import config
from seen_items import SeenItemsCache

logger = logging.getLogger(__name__)


def fetch_active_sources(client: httpx.Client) -> list[dict]:
    resp = client.get(f"{config.SCORING_SERVICE_URL}/sources", params={"status": "actief"})
    resp.raise_for_status()
    return resp.json()


def fetch_new_entries(source: dict, seen: SeenItemsCache) -> list[dict]:
    parsed = feedparser.parse(source["url"])
    new_entries = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        if not link or seen.has_seen(source["id"], link):
            continue
        new_entries.append(
            {
                "title": entry.get("title") or "(geen titel)",
                "link": link,
                "raw_content": entry.get("summary") or entry.get("title") or "",
            }
        )
    return new_entries


def score_entry(client: httpx.Client, entry: dict, source: dict) -> dict:
    payload = {
        "source": source["url"],
        "title": entry["title"],
        "url": entry["link"],
        "raw_content": entry["raw_content"],
        "source_id": source["id"],
    }
    resp = client.post(f"{config.SCORING_SERVICE_URL}/score", json=payload)
    resp.raise_for_status()
    result = resp.json()
    result["title"] = entry["title"]
    result["url"] = entry["link"]
    return result


def build_digest_html(top_items: list[dict]) -> str:
    rows = []
    for item in top_items:
        interessant_url = (
            f"{config.SCORING_SERVICE_URL}/feedback-link?item_id={item['item_id']}&label=interessant"
        )
        niet_url = (
            f"{config.SCORING_SERVICE_URL}/feedback-link?item_id={item['item_id']}&label=niet_interessant"
        )
        rows.append(
            "<tr>"
            f"<td>{item['title']}</td>"
            f"<td>{item['summary']}</td>"
            f"<td>{item['relevance_score']:.2f}</td>"
            f'<td><a href="{interessant_url}">Interessant</a> | '
            f'<a href="{niet_url}">Niet interessant</a></td>'
            "</tr>"
        )
    return (
        "<html><body>"
        "<h1>Security Trendwatch — dagelijkse digest</h1>"
        '<table border="1" cellpadding="6" cellspacing="0">'
        "<tr><th>Titel</th><th>Samenvatting</th><th>Score</th><th>Feedback</th></tr>"
        f"{''.join(rows)}"
        "</table></body></html>"
    )


def send_digest(html: str) -> None:
    if not config.SMTP_HOST or not config.DIGEST_TO_EMAIL:
        logger.info(
            "SMTP_HOST/DIGEST_TO_EMAIL niet geconfigureerd — digest wordt naar console gelogd:\n%s",
            html,
        )
        return

    message = EmailMessage()
    message["Subject"] = "Security Trendwatch — dagelijkse digest"
    message["From"] = config.DIGEST_FROM_EMAIL
    message["To"] = config.DIGEST_TO_EMAIL
    message.set_content("Deze e-mail bevat HTML; open in een HTML-mailclient om de digest te zien.")
    message.add_alternative(html, subtype="html")

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.starttls()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(message)
    logger.info("Digest verzonden naar %s", config.DIGEST_TO_EMAIL)


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
                    logger.exception("Kon item niet scoren: %s", entry["link"])
                    continue
                seen.mark_seen(source["id"], entry["link"])

        seen.save()

        if not scored_items:
            logger.info("Geen nieuwe items gevonden in de actieve bronnen.")
            return

        top_items = sorted(scored_items, key=lambda i: i["relevance_score"], reverse=True)[: config.DIGEST_TOP_N]
        send_digest(build_digest_html(top_items))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
