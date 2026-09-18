"""Daily job: read unread newsletter e-mails from DIGEST_MAILBOX via Graph
and score them, for sources that have no RSS feed at all — only a mailing
list (tl;dr sec, Risky Business News, SANS NewsBites, ...).

One "mailbox" Source is created per sender address (pseudo-URL
"mailto:sender@example.com", deduped the same way any other Source URL is),
so repeated newsletters from the same sender accumulate under one Source
instead of a new one per e-mail. Each message becomes one scored Item, using
Graph's own message webLink as the item URL and its own isRead flag as the
"already processed" marker — no separate local seen-cache needed, unlike
daily_digest's RSS entries (which have no equivalent server-side flag).

Off by default (MAILBOX_INGEST_ENABLED=false) — reading mail is a separate
Graph application permission (Mail.Read) from sending it (Mail.Send), so
this shouldn't silently start trying against a mailbox that only has send
access granted.

Runs standalone for testing:

    uv run python -m jobs.mailbox_ingest
"""

import html
import logging
import re
import time

import httpx

import config
from graph_client import get_graph_token

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return html.unescape(" ".join(_TAG_RE.sub(" ", text).split()))


def fetch_unread_messages(token: str) -> list[dict]:
    url = (
        f"https://graph.microsoft.com/v1.0/users/{config.DIGEST_MAILBOX}"
        f"/mailFolders/{config.MAILBOX_INGEST_FOLDER}/messages"
    )
    params = {
        "$filter": "isRead eq false",
        "$top": str(config.MAILBOX_INGEST_MAX_MESSAGES),
        "$select": "id,subject,from,body,webLink",
    }
    resp = httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30.0)
    resp.raise_for_status()
    return resp.json().get("value", [])


def mark_as_read(token: str, message_id: str) -> None:
    url = f"https://graph.microsoft.com/v1.0/users/{config.DIGEST_MAILBOX}/messages/{message_id}"
    resp = httpx.patch(url, json={"isRead": True}, headers={"Authorization": f"Bearer {token}"}, timeout=15.0)
    resp.raise_for_status()


def ensure_source_for_sender(client: httpx.Client, sender_email: str) -> dict:
    pseudo_url = f"mailto:{sender_email.lower()}"
    resp = client.get(f"{config.SCORING_SERVICE_URL}/sources")
    resp.raise_for_status()
    existing = next((s for s in resp.json() if s["url"] == pseudo_url), None)
    if existing is not None:
        return existing

    resp = client.post(
        f"{config.SCORING_SERVICE_URL}/sources",
        json={"url": pseudo_url, "type": "mailbox", "discovery_method": "mailbox"},
    )
    resp.raise_for_status()
    return resp.json()


def score_message(client: httpx.Client, message: dict, sender_email: str, source: dict) -> dict:
    subject = message.get("subject") or "(geen onderwerp)"
    raw_content = _strip_html(message.get("body", {}).get("content", ""))[:5000] or subject
    item_url = message.get("webLink") or f"mailto:{sender_email}"

    payload = {
        "source": sender_email,
        "title": subject,
        "url": item_url,
        "raw_content": raw_content,
        "source_id": source["id"],
    }
    resp = client.post(f"{config.SCORING_SERVICE_URL}/score", json=payload)
    resp.raise_for_status()
    return resp.json()


def run() -> None:
    if not config.MAILBOX_INGEST_ENABLED:
        logger.info("MAILBOX_INGEST_ENABLED staat uit — mailbox-ingest overgeslagen.")
        return
    if not config.DIGEST_MAILBOX:
        logger.warning("MAILBOX_INGEST_ENABLED staat aan maar DIGEST_MAILBOX is leeg — overgeslagen.")
        return

    token = get_graph_token()
    messages = fetch_unread_messages(token)
    logger.info(
        "Gevonden %d ongelezen bericht(en) in %s/%s.",
        len(messages), config.DIGEST_MAILBOX, config.MAILBOX_INGEST_FOLDER,
    )

    with httpx.Client(timeout=30.0) as client:
        for message in messages:
            sender_email = message.get("from", {}).get("emailAddress", {}).get("address")
            if not sender_email:
                logger.warning("Bericht %s heeft geen afzenderadres, overgeslagen.", message.get("id"))
                continue

            try:
                source = ensure_source_for_sender(client, sender_email)
                result = score_message(client, message, sender_email, source)
                logger.info(
                    "Gescoord: '%s' van %s -> score=%.2f",
                    message.get("subject"), sender_email, result["relevance_score"],
                )
            except httpx.HTTPError:
                logger.exception(
                    "Kon bericht %s van %s niet verwerken — blijft ongelezen voor een volgende run.",
                    message.get("id"), sender_email,
                )
                continue

            if config.SCORE_REQUEST_DELAY_SECONDS:
                time.sleep(config.SCORE_REQUEST_DELAY_SECONDS)

            try:
                mark_as_read(token, message["id"])
            except httpx.HTTPError:
                logger.exception(
                    "Kon bericht %s niet als gelezen markeren — wordt volgende run opnieuw verwerkt.",
                    message.get("id"),
                )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
