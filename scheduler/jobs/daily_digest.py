"""Daily job: mail the best already-scored items, as one digest with a section
for market development and one for news.

This job does not fetch feeds or score anything: scoring happens continuously
in jobs/ingest.py, so the digest goes out on time however slow (rate-limited)
the scoring is. Each digest takes the best items that were not mailed before
and marks them as mailed, so the next digest moves on to new ones.

Every digest is first recorded in the scoring-service (which items it holds),
so the 👍/👎 buttons in the mail can open that exact digest in the web GUI, log
you in if needed and record the vote there — one page to work through, no
stream of separate tabs.

Runs standalone for testing:

    uv run python -m jobs.daily_digest
"""

import html
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

import config
from graph_client import get_graph_token
from remote_settings import fetch_digest_settings

DIGEST_SUBJECT = "Security Trendwatch — dagelijkse digest"

# The digest has one section per category, each with its own top-N. The
# scoring-service tags every item "markt" (innovation, new products and
# services, new developments, plus acquisitions/funding/market figures) or
# "nieuws" (incidents, vulnerabilities, patches; app/classification.py) and
# scores each category against your 👍/👎 in that category only. Order here =
# order in the mail.
DIGEST_SECTIONS = (
    ("markt", "Marktontwikkeling"),
    ("nieuws", "Nieuws"),
)

logger = logging.getLogger(__name__)

# --- the mail's look ---------------------------------------------------------------------------
#
# Mail clients (Outlook desktop above all) ignore most modern CSS, so this is
# built from tables with inline styles and solid colours: it looks the same
# everywhere, just not fancy. The <style> block only adds dark mode for the
# clients that understand it (Outlook.com/app, iOS Mail, Gmail app).

_FONT = "'Segoe UI', -apple-system, Roboto, Helvetica, Arial, sans-serif"
_INDIGO, _INDIGO_DARK = "#4f46e5", "#4338ca"
_MONTHS = (
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
)

_HEAD = f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{html.escape(DIGEST_SUBJECT)}</title>
<style>
  @media (prefers-color-scheme: dark) {{
    .bg-body {{ background: #0b1120 !important; }}
    .bg-card {{ background: #131c31 !important; border-color: #25314d !important; }}
    .t-main, .t-main a {{ color: #e5e9f2 !important; }}
    .t-muted {{ color: #93a1b8 !important; }}
    .t-body {{ color: #c3cbda !important; }}
    .h-markt {{ color: #93c5fd !important; }}
    .h-nieuws {{ color: #94a3b8 !important; }}
    .btn-up {{ background: #12351f !important; border-color: #4ade80 !important; }}
    .btn-up a {{ color: #4ade80 !important; }}
    .btn-down {{ background: #3b1618 !important; border-color: #f87171 !important; }}
    .btn-down a {{ color: #f87171 !important; }}
  }}
  @media only screen and (max-width: 520px) {{
    .stack {{ display: block !important; width: 100% !important; }}
    .stack-gap {{ display: none !important; }}
    .btn-cell {{ display: block !important; width: 100% !important; margin-bottom: 8px !important; }}
    .btn-cell a {{ display: block !important; text-align: center !important; }}
  }}
</style>"""


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


def _href(url: str) -> str:
    """An http(s) URL made safe for an attribute; anything else becomes '#'."""
    return html.escape(url, quote=True) if url.lower().startswith(("http://", "https://")) else "#"


def _vote_url(item_id: int, action: str, digest_id: int | None) -> str:
    """Where a 👍/👎 button leads. With a recorded digest: that digest's page,
    which records the vote after login. Without one (the scoring-service could
    not record it): the old link, which redirects through the login too."""
    if digest_id is not None:
        return f"{config.FEEDBACK_BASE_URL}/admin/digest/{digest_id}?vote={item_id}:{action}"
    label = "interessant" if action == "like" else "niet_interessant"
    return f"{config.FEEDBACK_BASE_URL}/feedback-link?item_id={item_id}&label={label}"


def _button(url: str, label: str, css: str, background: str, border: str, color: str) -> str:
    return (
        f'<td class="btn-cell {css}" bgcolor="{background}" style="background:{background};border:1px solid {border};border-radius:8px;">'
        f'<a href="{html.escape(url, quote=True)}" style="display:inline-block;padding:10px 16px;font-family:{_FONT};'
        f'font-size:14px;font-weight:600;line-height:18px;color:{color};text-decoration:none;">{label}</a></td>'
    )


def _card_html(item: dict, digest_id: int | None) -> str:
    title = html.escape(item["title"])
    url = _href(item["url"])
    meta = html.escape(item["source_name"])
    if item.get("relevance_score") is not None:
        meta += f" &nbsp;·&nbsp; score {item['relevance_score']:.2f}"
    summary = (
        f'<tr><td class="t-body" style="padding:0 18px 14px;font-family:{_FONT};font-size:14px;line-height:21px;color:#475569;">'
        f"{html.escape(item['summary'])}</td></tr>"
        if item.get("summary")
        else ""
    )
    up = _button(_vote_url(item["item_id"], "like", digest_id), "\U0001F44D&nbsp; Interessant", "btn-up", "#f0fdf4", "#86efac", "#166534")
    down = _button(_vote_url(item["item_id"], "dislike", digest_id), "\U0001F44E&nbsp; Niet interessant", "btn-down", "#fef2f2", "#fca5a5", "#991b1b")
    return (
        '<table role="presentation" class="bg-card" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;margin:0 0 12px;">'
        f'<tr><td class="t-main" style="padding:16px 18px 3px;font-family:{_FONT};font-size:16px;line-height:22px;font-weight:600;color:#0f172a;">'
        f'<a href="{url}" style="color:#0f172a;text-decoration:none;">{title}</a></td></tr>'
        f'<tr><td class="t-muted" style="padding:0 18px 8px;font-family:{_FONT};font-size:13px;line-height:18px;color:#64748b;">{meta}</td></tr>'
        f"{summary}"
        '<tr><td style="padding:0 18px 16px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'{up}<td class="stack-gap" width="8" style="width:8px;font-size:0;line-height:0;">&nbsp;</td>{down}'
        "</tr></table></td></tr></table>"
    )


def _section_html(heading: str, category: str, items: list[dict], digest_id: int | None) -> str:
    color = "#1d4ed8" if category == "markt" else "#475569"
    title = (
        f'<tr><td class="h-{category}" style="padding:24px 2px 10px;font-family:{_FONT};font-size:12px;line-height:16px;font-weight:700;'
        f'letter-spacing:0.08em;text-transform:uppercase;color:{color};">{html.escape(heading)}'
        f'<span style="font-weight:400;letter-spacing:0;"> &nbsp;·&nbsp; {len(items)}</span></td></tr>'
    )
    if not items:
        return title + (
            f'<tr><td class="t-muted" style="padding:0 2px 8px;font-family:{_FONT};font-size:14px;color:#64748b;">'
            "Geen nieuwe items in deze categorie.</td></tr>"
        )
    return title + "".join(f"<tr><td>{_card_html(item, digest_id)}</td></tr>" for item in items)


def _dutch_date(when: datetime) -> str:
    return f"{when.day} {_MONTHS[when.month - 1]} {when.year}"


def build_digest_html(
    items_by_category: dict[str, list[dict]],
    digest_id: int | None = None,
    when: datetime | None = None,
) -> str:
    """One mail, one section per DIGEST_SECTIONS entry (an empty one says so).
    `digest_id` (from record_digest) makes the 👍/👎 buttons open that digest."""
    when = when or datetime.now(timezone.utc)
    counts = " · ".join(f"{len(items_by_category.get(cat, []))} {label.lower()}" for cat, label in DIGEST_SECTIONS)
    sections = "".join(
        _section_html(heading, category, items_by_category.get(category, []), digest_id)
        for category, heading in DIGEST_SECTIONS
    )
    open_url = f"{config.FEEDBACK_BASE_URL}/admin/digest/{digest_id}" if digest_id is not None else f"{config.FEEDBACK_BASE_URL}/admin/digest/latest"
    header_button = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#ffffff" '
        f'style="background:#ffffff;border-radius:8px;"><a href="{html.escape(open_url, quote=True)}" '
        f'style="display:inline-block;padding:10px 18px;font-family:{_FONT};font-size:14px;font-weight:700;'
        f'color:{_INDIGO_DARK};text-decoration:none;">Open de digest &rarr;</a></td></tr></table>'
    )
    return (
        '<!doctype html><html lang="nl"><head>'
        f"{_HEAD}</head>"
        '<body class="bg-body" style="margin:0;padding:0;background:#f3f5fa;">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#f3f5fa;">{html.escape(counts)}</div>'
        '<table role="presentation" class="bg-body" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f3f5fa" style="background:#f3f5fa;">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;">'
        # header
        f'<tr><td bgcolor="{_INDIGO}" style="background:{_INDIGO};border-radius:14px;padding:24px 26px;">'
        f'<div style="font-family:{_FONT};font-size:12px;line-height:16px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:#c7d2fe;">Trendwatch</div>'
        f'<div style="font-family:{_FONT};font-size:24px;line-height:30px;font-weight:700;color:#ffffff;padding:4px 0 2px;">Dagelijkse digest</div>'
        f'<div style="font-family:{_FONT};font-size:14px;line-height:20px;color:#e0e7ff;padding:0 0 16px;">{_dutch_date(when)} &nbsp;·&nbsp; {html.escape(counts)}</div>'
        f"{header_button}</td></tr>"
        f"{sections}"
        # footer
        f'<tr><td class="t-muted" style="padding:18px 4px 4px;font-family:{_FONT};font-size:12px;line-height:18px;color:#64748b;">'
        "Klik op &#128077; of &#128078; om te beoordelen: je logt in als dat nodig is en blijft op &eacute;&eacute;n pagina, "
        "waar je de hele digest kunt doorlopen. Elk duimpje maakt de volgende digest beter."
        "</td></tr>"
        "</table></td></tr></table></body></html>"
    )


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


def record_digest(kind: str, top_by_category: dict[str, list[dict]]) -> int | None:
    """Tell the scoring-service which items this digest holds; returns the
    digest id the mail's buttons point at, or None if that failed (the mail
    then falls back to the old links, which still work)."""
    items = [
        {"item_id": item["item_id"], "category": category}
        for category, _heading in DIGEST_SECTIONS
        for item in top_by_category.get(category, [])
    ]
    try:
        resp = httpx.post(
            f"{config.SCORING_SERVICE_URL}/digests", json={"kind": kind, "items": items}, timeout=30.0
        )
        resp.raise_for_status()
        return resp.json()["digest_id"]
    except (httpx.HTTPError, KeyError, ValueError):
        logger.exception("Kon de digest niet vastleggen; de knoppen in de mail gebruiken de oude links.")
        return None


def _mark_digest_mailed(digest_id: int | None) -> None:
    if digest_id is None:
        return
    try:
        httpx.post(f"{config.SCORING_SERVICE_URL}/digests/{digest_id}/mailed", timeout=30.0).raise_for_status()
    except httpx.HTTPError:
        logger.exception("Digest %s is verstuurd, maar kon niet als verstuurd worden gemarkeerd.", digest_id)


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

    digest_id = record_digest("daily", top_by_category)
    if not send_digest(build_digest_html(top_by_category, digest_id)):
        return  # dry run: leave the items unmarked
    _mark_digest_mailed(digest_id)

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
    anything, so it never takes items away from the scheduled digest. It is
    recorded as a "preview" digest, so its buttons work like any other."""
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
    digest_id = record_digest("preview", top_by_category)
    if send_digest(build_digest_html(top_by_category, digest_id)):
        _mark_digest_mailed(digest_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
