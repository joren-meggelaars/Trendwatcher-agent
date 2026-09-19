"""Local admin GUI (/admin/*): server-rendered Jinja2 templates directly in
this FastAPI app — no separate frontend project, no build step. Kept fully
separate from the JSON API routes in main.py (/score, /feedback, /sources
stay pure JSON, untouched by this module).
"""

import html
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import (
    digest_settings,
    digest_view,
    discovery,
    models,
    review,
    runtime_settings,
    safe_fetch,
    schemas,
    scoring,
    security,
    textclean,
)
from app.auth import (
    REMEMBER_DAYS,
    SESSION_MAX_AGE,
    NotAuthenticated,
    require_admin_session,
    safe_next,
    start_session,
    verify_admin_credentials,
)
from app.config import settings
from app.database import get_db
from app.embeddings import EmbeddingProvider, get_embedding_provider
from app.scoring import score_and_store

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
# Items stored before titles were cleaned still carry HTML entities.
templates.env.filters["clean_text"] = textclean.clean_text


def _safe_url(value: str | None) -> str:
    """For an href: the URL if it is http(s), otherwise '#'. Feed data decides
    these addresses, so a javascript: or data: link must never become a link."""
    return value if value and value.strip().lower().startswith(("http://", "https://")) else "#"


templates.env.filters["safe_url"] = _safe_url

ITEMS_PER_PAGE = 50


def _nav_key(path: str) -> str:
    """Which menu entry a page belongs to (for the highlight in the sidebar)."""
    for prefix, key in (
        ("/admin/digest", "digests"),
        ("/admin/review", "review"),
        ("/admin/sources", "sources"),
        ("/admin/items/batch-add", "batch"),
        ("/admin/items", "items"),
        ("/admin/settings", "settings"),
        ("/admin/config", "config"),
    ):
        if path.startswith(prefix):
            return key
    return ""


templates.env.globals["nav_key"] = _nav_key
_STATUSES = ("kandidaat", "actief", "gedeactiveerd")

_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _strip_html(text: str) -> str:
    """Same simple approach as scheduler/jobs/weekly_discovery.py's _strip_html.

    Not imported from there: the scheduler is a separate uv project/venv by
    design (independent Azure deployability), so there's no shared package to
    import from — duplicating this ~2-line helper is cheaper than coupling
    the two projects together.
    """
    return html.unescape(" ".join(_TAG_RE.sub(" ", text).split()))


def _extract_title(html_text: str, fallback: str) -> str:
    match = _TITLE_RE.search(html_text)
    if match:
        title = html.unescape(" ".join(match.group(1).split()))
        if title:
            return title
    return fallback


# --- auth ---------------------------------------------------------------


@router.get("/")
def admin_root() -> RedirectResponse:
    return RedirectResponse(url="/admin/sources")


def _logged_in(request: Request) -> bool:
    try:
        require_admin_session(request)
    except NotAuthenticated:
        return False
    return True


@router.get("/login")
def login_form(request: Request, next: str | None = None):
    destination = safe_next(next)
    if _logged_in(request):
        return RedirectResponse(url=destination, status_code=303)
    # `next` is where the login was needed (e.g. a 👍 link from the mail): back there afterwards.
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": destination if next else "", "remember_days": REMEMBER_DAYS}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember: str | None = Form(None),
    next: str | None = Form(None),
):
    key = security.client_ip(request)
    context = {"next": safe_next(next) if next else "", "remember_days": REMEMBER_DAYS}

    locked = security.login_throttle.seconds_locked(key)
    if locked:  # no password check at all while locked: neither guessing nor CPU burning
        minutes = (locked + 59) // 60
        return templates.TemplateResponse(
            request,
            "login.html",
            {**context, "error": f"Te veel mislukte pogingen. Probeer het over {minutes} minu{'ut' if minutes == 1 else 'ten'} opnieuw."},
            status_code=429,
            headers={"Retry-After": str(locked)},
        )

    if verify_admin_credentials(username, password):
        security.login_throttle.record_success(key)
        start_session(request, remember=bool(remember))
        return RedirectResponse(url=safe_next(next), status_code=303)

    security.login_throttle.record_failure(key)
    return templates.TemplateResponse(
        request,
        "login.html",
        {**context, "error": "Ongeldige gebruikersnaam of wachtwoord."},
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


# --- sources --------------------------------------------------------------


@router.get("/sources")
def list_sources_page(
    request: Request,
    status: str | None = None,
    error: str | None = None,
    notice: str | None = None,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    query = db.query(models.Source)
    if status in _STATUSES:
        query = query.filter(models.Source.status == status)
    sources = query.order_by(models.Source.id.desc()).all()

    item_counts = dict(
        db.query(models.Item.source_id, func.count(models.Item.id))
        .filter(models.Item.source_id.is_not(None))
        .group_by(models.Item.source_id)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "sources": sources,
            "item_counts": item_counts,
            "statuses": _STATUSES,
            "current_status": status if status in _STATUSES else None,
            "error": error[:200] if error else None,
            "notice": notice[:200] if notice else None,
        },
    )


@router.post("/sources")
def create_source_from_admin(
    url: str = Form(...),
    type: str = Form(...),
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
) -> RedirectResponse:
    try:
        discovery.create_source(db, url, type, "manual")
    except ValueError as exc:  # an address that may not become a source (see app/urlsafety.py)
        return RedirectResponse(url=f"/admin/sources?{urlencode({'error': f'Bron niet toegevoegd: {exc}.'})}", status_code=303)
    return RedirectResponse(url="/admin/sources", status_code=303)


@router.post("/sources/{source_id}/override-status")
def override_source_status(
    source_id: int,
    new_status: str = Form(...),
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
) -> RedirectResponse:
    source = db.get(models.Source, source_id)
    if source is not None and new_status in _STATUSES:
        source.status = new_status
        db.commit()
    return RedirectResponse(url="/admin/sources", status_code=303)


@router.post("/sources/bulk-status")
def bulk_override_source_status(
    new_status: str = Form(...),
    ids: list[int] = Form(default=[]),
    back: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
) -> RedirectResponse:
    """Set the status of the ticked sources in one go (same effect as the
    per-row "Zet status", which stays available)."""
    back_query = {"status": back} if back in _STATUSES else {}
    if new_status not in _STATUSES:
        return RedirectResponse(url=_sources_url(back_query, error="Onbekende status."), status_code=303)
    wanted = set(ids)
    if not wanted:
        return RedirectResponse(url=_sources_url(back_query, error="Geen bronnen geselecteerd."), status_code=303)

    changed = (
        db.query(models.Source)
        .filter(models.Source.id.in_(wanted), models.Source.status != new_status)
        .update({models.Source.status: new_status}, synchronize_session=False)
    )
    db.commit()
    unchanged = len(wanted) - changed
    message = f"{changed} bron{'nen' if changed != 1 else ''} op {new_status} gezet"
    if unchanged:
        message += f" ({unchanged} had die status al of bestaat niet meer)"
    return RedirectResponse(url=_sources_url(back_query, notice=message + "."), status_code=303)


def _sources_url(query: dict[str, str], **flash: str) -> str:
    return f"/admin/sources?{urlencode({**query, **flash})}"


# --- items ------------------------------------------------------------------


@router.get("/items")
def list_items_page(
    request: Request,
    source_id: int | None = None,
    min_score: float | None = None,
    days: int | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    query = db.query(models.Item)
    if source_id is not None:
        query = query.filter(models.Item.source_id == source_id)
    if min_score is not None:
        query = query.filter(models.Item.relevance_score >= min_score)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(models.Item.created_at >= cutoff)

    total = query.count()
    page = max(1, page)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    items = (
        query.order_by(models.Item.id.desc())
        .offset((page - 1) * ITEMS_PER_PAGE)
        .limit(ITEMS_PER_PAGE)
        .all()
    )

    feedback_by_item: dict[int, str] = {}
    item_ids = [i.id for i in items]
    if item_ids:
        rows = (
            db.query(models.Feedback.item_id, models.Feedback.label)
            .filter(models.Feedback.item_id.in_(item_ids))
            .order_by(models.Feedback.id.desc())
            .all()
        )
        for item_id, label in rows:
            feedback_by_item.setdefault(item_id, label)  # first hit per id = most recent (desc order)

    sources = db.query(models.Source).order_by(models.Source.id).all()

    filter_params: dict[str, int | float] = {}
    if source_id is not None:
        filter_params["source_id"] = source_id
    if min_score is not None:
        filter_params["min_score"] = min_score
    if days is not None:
        filter_params["days"] = days

    def _page_url(target_page: int) -> str:
        return "/admin/items?" + urlencode({**filter_params, "page": target_page})

    return templates.TemplateResponse(
        request,
        "items.html",
        {
            "items": items,
            "feedback_by_item": feedback_by_item,
            "sources": sources,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "filters": {"source_id": source_id, "min_score": min_score, "days": days},
            "prev_url": _page_url(page - 1) if page > 1 else None,
            "next_url": _page_url(page + 1) if page < total_pages else None,
        },
    )


# --- batch add ----------------------------------------------------------


@router.get("/items/batch-add")
def batch_add_form(request: Request, _admin: None = Depends(require_admin_session)):
    return templates.TemplateResponse(request, "batch_add.html", {"results": None})


@router.post("/items/batch-add")
def batch_add_submit(
    request: Request,
    urls: str = Form(...),
    db: Session = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
    _admin: None = Depends(require_admin_session),
):
    results = []

    for url in (line.strip() for line in urls.splitlines()):
        if not url:
            continue

        if not url.lower().startswith(("http://", "https://")):
            results.append({"url": url, "ok": False, "reason": "Ongeldige URL (moet met http(s):// beginnen)."})
            continue

        # The address is somebody else's input: fetch it the safe way (public
        # addresses only, pinned, size and time limits; see app/safe_fetch.py).
        try:
            page = safe_fetch.fetch(url, max_bytes=2_000_000)
        except safe_fetch.UnsafeURL as exc:
            results.append({"url": url, "ok": False, "reason": f"Niet toegestaan: {exc}."})
            continue
        except safe_fetch.FetchError as exc:
            results.append({"url": url, "ok": False, "reason": f"Kon content niet ophalen: {exc}."})
            continue

        page_text = page.text
        title = _extract_title(page_text, fallback=url)
        raw_content = _strip_html(page_text)[:5000] or title

        # Broad catch is deliberate: one bad item (embedding-provider error,
        # unexpected response shape, ...) must not abort the rest of the
        # batch — every URL gets its own success/failure row.
        try:
            item = score_and_store(
                db,
                provider,
                source="admin-batch-add",
                title=title,
                url=url,
                raw_content=raw_content,
                source_id=None,
                user_id=settings.default_user_id,
            )
        except Exception as exc:  # noqa: BLE001 - intentional, see comment above
            results.append({"url": url, "ok": False, "reason": f"Server error bij scoren: {exc}"})
            continue

        results.append(
            {
                "url": url,
                "ok": True,
                "title": title,
                "score": item.relevance_score,
                "item_id": item.id,
            }
        )

    return templates.TemplateResponse(request, "batch_add.html", {"results": results})


# --- digest settings + manual trigger ---------------------------------


@router.get("/settings")
def settings_form(
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    current = digest_settings.get_digest_settings(db)
    return templates.TemplateResponse(request, "settings.html", {"settings": current, "sent": None, "error": None})


@router.post("/settings")
def settings_submit(
    request: Request,
    digest_hour: int = Form(...),
    digest_top_n: int = Form(...),
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    digest_hour = max(0, min(23, digest_hour))
    digest_top_n = max(1, min(50, digest_top_n))
    current = digest_settings.update_digest_settings(db, digest_hour, digest_top_n)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": current, "sent": None, "error": None, "saved": True},
    )


@router.post("/settings/send-now")
def send_digest_now(
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    current = digest_settings.get_digest_settings(db)
    sent = None
    error = None
    try:
        resp = httpx.post(f"{settings.scheduler_url}/trigger/digest-now", timeout=10.0)
        resp.raise_for_status()
        sent = (
            "Digest wordt verstuurd met de beste recent gescoorde items — check binnen enkele "
            "seconden je mailbox (of de scheduler-logs bij DIGEST_DRY_RUN)."
        )
    except httpx.HTTPError as exc:
        error = f"Kon de scheduler niet bereiken op {settings.scheduler_url}: {exc}"

    return templates.TemplateResponse(
        request, "settings.html", {"settings": current, "sent": sent, "error": error},
    )


# --- configuration (env-like settings) -----------------------------------


def _render_config(
    request: Request,
    db: Session,
    *,
    submitted: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    message: str | None = None,
    warning: str | None = None,
):
    """`submitted` = the raw form values to show again after a validation error,
    so nothing typed is lost."""
    groups: dict[str, list[dict]] = {}
    for row in runtime_settings.describe_all(db):
        key = row.spec.key
        shown = (
            submitted[key]
            if submitted is not None and key in submitted
            else (runtime_settings.to_text(row.override) if row.override is not None else "")
        )
        groups.setdefault(row.spec.group, []).append(
            {
                "spec": row.spec,
                "value": shown,
                "overridden": row.override is not None,
                "env_text": runtime_settings.to_text(row.env) if row.env is not None else None,
                "default_text": runtime_settings.to_text(row.spec.default),
                "error": (errors or {}).get(key),
            }
        )
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "groups": groups,
            "console_only": runtime_settings.CONSOLE_ONLY,
            "message": message,
            "warning": warning,
            "has_errors": bool(errors),
        },
    )


@router.get("/config")
def config_form(
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    return _render_config(request, db)


@router.post("/config")
async def config_submit(
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    form = await request.form()
    submitted = {
        spec.key: str(form[f"s__{spec.key}"])
        for spec in runtime_settings.SPECS
        if f"s__{spec.key}" in form
    }
    changed, errors = runtime_settings.save_overrides(db, submitted)
    if errors:
        return _render_config(request, db, submitted=submitted, errors=errors)
    if not changed:
        return _render_config(request, db, message="Geen wijzigingen.")

    scheduler_changed = [k for k in changed if runtime_settings.spec_for(k).owner == "scheduler"]
    message = f"Opgeslagen ({len(changed)} wijziging{'en' if len(changed) != 1 else ''})."
    warning = None
    if scheduler_changed:
        try:
            httpx.post(f"{settings.scheduler_url}/trigger/sync-settings", timeout=5.0).raise_for_status()
            message += " De scheduler heeft de wijzigingen direct overgenomen."
        except httpx.HTTPError:
            warning = (
                "De scheduler is nu niet bereikbaar. Zijn wijzigingen worden binnen enkele minuten "
                "vanzelf overgenomen."
            )
    return _render_config(request, db, message=message, warning=warning)


# --- review: quick thumbs on articles, independent of the digest --------------
#
# Offers stored articles one after another for a 👍/👎 (or a skip), so initial
# feedback can be given fast instead of waiting for it to show up in a digest.
# The page works without JavaScript (plain form posts); with it, a click removes
# the card without a reload and the keys y/n/s/u do the same from the keyboard.

_REVIEW_CATEGORIES = {"markt": "markt", "nieuws": "nieuws", "alles": None}
_REVIEW_ACTIONS = {"like": "interessant", "dislike": "niet_interessant", "skip": review.SKIPPED}


def _review_category(value: str) -> str:
    return value if value in _REVIEW_CATEGORIES else "markt"


def _wants_json(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch"


def _source_label(source: str) -> str:
    if source.lower().startswith("mailto:"):
        return source[len("mailto:"):]
    return discovery.extract_domain(source) or source


@router.get("/review")
def review_page(
    request: Request,
    category: str = "markt",
    updated: int | None = None,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    category = _review_category(category)
    view = review.overview(db, settings.default_user_id, _REVIEW_CATEGORIES[category])
    cards = []
    for candidate in view.queue:
        title, summary = review.card_text(candidate.item)
        cards.append(
            {
                "id": candidate.item.id,
                "title": title,
                "summary": summary,
                "url": candidate.item.url,
                "category": candidate.category,
                "score": candidate.item.relevance_score,
                "source": _source_label(candidate.item.source),
                "created": candidate.item.created_at,
            }
        )
    return templates.TemplateResponse(
        request,
        "review.html",
        {"cards": cards, "category": category, "waiting": view.waiting, "given": view.given, "updated": updated},
    )


# Declared before /review/{item_id}: otherwise "rescore" would be parsed as an item id.
@router.post("/review/rescore")
async def review_rescore(
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    form = await request.form()
    category = _review_category(str(form.get("category", "markt")))
    updated = scoring.rescore_recent(db, settings.default_user_id, force=True)
    if _wants_json(request):
        return JSONResponse({"updated": updated})
    return RedirectResponse(url=f"/admin/review?category={category}&updated={updated}", status_code=303)


@router.post("/review/undo/{feedback_id}")
async def review_undo(
    feedback_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    form = await request.form()
    category = _review_category(str(form.get("category", "markt")))
    feedback = db.get(models.Feedback, feedback_id)
    if feedback is not None and feedback.user_id == settings.default_user_id:
        db.delete(feedback)
        db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True})
    return RedirectResponse(url=f"/admin/review?category={category}", status_code=303)


@router.post("/review/{item_id}")
async def review_action(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    form = await request.form()
    label = _REVIEW_ACTIONS.get(str(form.get("action", "")))
    category = _review_category(str(form.get("category", "markt")))

    if label is None or db.get(models.Item, item_id) is None:
        if _wants_json(request):
            return JSONResponse({"ok": False}, status_code=422 if label is None else 404)
        return RedirectResponse(url=f"/admin/review?category={category}", status_code=303)

    # One answer per item: a double click (or a thumb already given through the
    # mail link) must not add a second row.
    feedback = (
        db.query(models.Feedback)
        .filter(models.Feedback.item_id == item_id, models.Feedback.user_id == settings.default_user_id)
        .order_by(models.Feedback.id.desc())
        .first()
    )
    created = feedback is None
    if created:
        feedback = models.Feedback(item_id=item_id, user_id=settings.default_user_id, label=label)
        db.add(feedback)
        db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "feedback_id": feedback.id, "created": created, "label": feedback.label})
    return RedirectResponse(url=f"/admin/review?category={category}", status_code=303)


# --- digests: the pages the mail links to ---------------------------------------
#
# A 👍/👎 link in the digest mail opens /admin/digest/<id>?vote=<item>:<action>.
# Not logged in: the login page first (and back here afterwards). The page
# records that vote itself — with a POST from the page, never from the link, so
# a mail scanner or link preview that fetches the URL cannot cast votes — and
# shows the whole digest to work through without leaving the page.

def _not_found(request: Request, what: str):
    return templates.TemplateResponse(request, "error.html", {"title": "Niet gevonden", "message": what}, status_code=404)


@router.get("/digests")
def digests_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    return templates.TemplateResponse(
        request, "digests.html", {"rows": digest_view.archive(db, settings.default_user_id)}
    )


# Declared before /digest/{digest_id}: otherwise "latest" would be parsed as an id.
@router.get("/digest/latest")
def digest_latest(db: Session = Depends(get_db), _admin: None = Depends(require_admin_session)):
    latest = digest_view.latest_digest_id(db)
    return RedirectResponse(url=f"/admin/digest/{latest}" if latest else "/admin/digests", status_code=303)


@router.get("/digest/{digest_id}")
def digest_page(
    request: Request,
    digest_id: int,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    """One digest as it was mailed, to vote on. A `?vote=<item>:<like|dislike>`
    (from a mail link) is applied by the page's script with a POST, not here."""
    page = digest_view.digest_page(db, digest_id, settings.default_user_id, _source_label)
    if page is None:
        return _not_found(request, "Deze digest bestaat niet (meer).")
    return templates.TemplateResponse(request, "digest.html", page)


@router.post("/vote/{item_id}")
async def vote(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    """Make like/dislike/clear your one answer for an item (used by the digest
    and review pages). Answers with the previous state, for an exact undo."""
    form = await request.form()
    action = str(form.get("action", ""))
    if action not in ("like", "dislike", "clear"):
        return JSONResponse({"ok": False}, status_code=422)
    if db.get(models.Item, item_id) is None:
        return JSONResponse({"ok": False}, status_code=404)
    previous, new = digest_view.set_vote(db, settings.default_user_id, item_id, action)
    return JSONResponse({"ok": True, "vote": new, "previous": previous})


@router.get("/vote-link")
def vote_link(
    item_id: int,
    label: str,
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
):
    """Where an old-style mail link (/feedback-link) ends up once logged in:
    the newest digest containing the item, with the vote to record. Mails from
    before digests were recorded have no such digest: the review page then."""
    action = digest_view.action_for(label)
    if action is None:
        return RedirectResponse(url="/admin/digests", status_code=303)
    digest_id = digest_view.latest_digest_containing(db, item_id)
    target = f"/admin/digest/{digest_id}" if digest_id else "/admin/review?category=alles"
    separator = "&" if "?" in target else "?"
    return RedirectResponse(url=f"{target}{separator}vote={item_id}:{action}", status_code=303)


# Public (no login, no state change): the 👍/👎 links in mails sent before the
# digest pages existed. They only redirect — through the login — to /vote-link.
public_router = APIRouter()


@public_router.get("/feedback-link")
def feedback_link(item_id: int, label: schemas.FeedbackLabel) -> RedirectResponse:
    return RedirectResponse(url=f"/admin/vote-link?{urlencode({'item_id': item_id, 'label': label})}", status_code=303)


def _handle_not_authenticated(request: Request, exc: NotAuthenticated):
    if _wants_json(request):  # the session ran out while a page was open
        return JSONResponse({"ok": False, "login": True}, status_code=401)
    if request.method == "GET":
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(url=f"/admin/login?next={quote(target, safe='')}", status_code=303)
    return RedirectResponse(url="/admin/login", status_code=303)


STATIC_DIR = Path(__file__).resolve().parent / "static"


def session_options() -> dict:
    """SessionMiddleware settings shared by every app that serves the GUI."""
    return {
        "secret_key": settings.session_secret_key,
        "max_age": SESSION_MAX_AGE,
        "same_site": "lax",  # sent on the top-level navigation from a mail link, not on cross-site posts
        "https_only": settings.session_cookie_secure,
    }


def register(app: FastAPI) -> None:
    app.include_router(router)
    app.include_router(public_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.add_exception_handler(NotAuthenticated, _handle_not_authenticated)
    security.register(app)
