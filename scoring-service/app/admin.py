"""Local admin GUI (/admin/*): server-rendered Jinja2 templates directly in
this FastAPI app — no separate frontend project, no build step. Kept fully
separate from the JSON API routes in main.py (/score, /feedback, /sources
stay pure JSON, untouched by this module).
"""

import html
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import digest_settings, discovery, models
from app.auth import NotAuthenticated, SESSION_KEY, require_admin_session, verify_admin_credentials
from app.config import settings
from app.database import get_db
from app.embeddings import EmbeddingProvider, get_embedding_provider
from app.scoring import score_and_store

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

ITEMS_PER_PAGE = 50
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


@router.get("/login")
def login_form(request: Request):
    if request.session.get(SESSION_KEY):
        return RedirectResponse(url="/admin/sources", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if verify_admin_credentials(username, password):
        request.session[SESSION_KEY] = True
        return RedirectResponse(url="/admin/sources", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Ongeldige gebruikersnaam of wachtwoord."},
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
        {"sources": sources, "item_counts": item_counts, "statuses": _STATUSES},
    )


@router.post("/sources")
def create_source_from_admin(
    url: str = Form(...),
    type: str = Form(...),
    db: Session = Depends(get_db),
    _admin: None = Depends(require_admin_session),
) -> RedirectResponse:
    discovery.create_source(db, url, type, "manual")
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

        try:
            page_resp = httpx.get(url, timeout=15.0, follow_redirects=True)
            page_resp.raise_for_status()
        except httpx.HTTPError as exc:
            results.append({"url": url, "ok": False, "reason": f"Kon content niet ophalen: {exc}"})
            continue

        title = _extract_title(page_resp.text, fallback=url)
        raw_content = _strip_html(page_resp.text)[:5000] or title

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


def _handle_not_authenticated(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(url="/admin/login", status_code=303)


def register(app: FastAPI) -> None:
    app.include_router(router)
    app.add_exception_handler(NotAuthenticated, _handle_not_authenticated)
