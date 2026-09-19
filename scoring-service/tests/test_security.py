import re
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, security
from app.config import settings
from app.database import get_db
from tests.conftest import ADMIN_TEST_PASSWORD, ADMIN_TEST_USERNAME

LOGIN = {"username": ADMIN_TEST_USERNAME, "password": ADMIN_TEST_PASSWORD}
WRONG = {"username": ADMIN_TEST_USERNAME, "password": "wrong"}


# --- sessions: "onthoud mij", expiry, no open redirects ----------------------------------------


def test_remember_me_gives_30_days_and_without_it_12_hours():
    remembered, short = SimpleNamespace(session={}), SimpleNamespace(session={})

    auth.start_session(remembered, remember=True)
    auth.start_session(short, remember=False)

    now = time.time()
    assert 29.9 * 86400 < remembered.session["exp"] - now < 30.1 * 86400
    assert 11.9 * 3600 < short.session["exp"] - now < 12.1 * 3600


def test_a_login_always_starts_a_fresh_session():
    request = SimpleNamespace(session={"leftover": "from before", "is_admin": False})

    auth.start_session(request, remember=False)

    assert "leftover" not in request.session


def test_an_expired_or_undated_session_is_refused_and_cleared():
    for session in ({"is_admin": True, "exp": time.time() - 1}, {"is_admin": True}):
        request = SimpleNamespace(session=session)
        with pytest.raises(auth.NotAuthenticated):
            auth.require_admin_session(request)
        assert request.session == {}


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/admin/digest/5?vote=7:like", "/admin/digest/5?vote=7:like"),
        ("/admin/review", "/admin/review"),
        (None, "/admin/sources"),
        ("", "/admin/sources"),
        ("https://evil.example/admin/x", "/admin/sources"),
        ("//evil.example/admin/x", "/admin/sources"),
        ("/elsewhere", "/admin/sources"),
        ("/admin/login?next=/admin/x", "/admin/sources"),  # never bounce back to the login itself
        ("/admin/x\\..\\evil", "/admin/sources"),
        ("/admin/x\r\nSet-Cookie: a=b", "/admin/sources"),
    ],
)
def test_safe_next_only_allows_paths_inside_the_admin(value, expected):
    assert auth.safe_next(value) == expected


def test_login_goes_to_next_but_never_to_another_site(client):
    ok = client.post("/admin/login", data={**LOGIN, "next": "/admin/digest/3"}, follow_redirects=False)
    assert ok.headers["location"] == "/admin/digest/3"

    for evil in ("https://evil.example/", "//evil.example/", "/admin/login"):
        client.post("/admin/logout")
        resp = client.post("/admin/login", data={**LOGIN, "next": evil}, follow_redirects=False)
        assert resp.headers["location"] == "/admin/sources", evil


def test_an_already_logged_in_visitor_is_sent_on_from_the_login_page(admin_client):
    resp = admin_client.get("/admin/login", params={"next": "/admin/digest/9"}, follow_redirects=False)

    assert resp.status_code == 303 and resp.headers["location"] == "/admin/digest/9"


# --- the brake on wrong passwords -----------------------------------------------------------------


def test_five_wrong_passwords_lock_that_address_out_even_for_the_right_one(client):
    for _ in range(5):
        assert client.post("/admin/login", data=WRONG).status_code == 401

    locked = client.post("/admin/login", data=LOGIN)

    assert locked.status_code == 429
    assert "Te veel mislukte pogingen" in locked.text and int(locked.headers["retry-after"]) > 0
    assert client.get("/admin/sources", follow_redirects=False).status_code == 303  # still not logged in


def test_another_address_is_not_locked_and_success_resets_the_count(client):
    for _ in range(5):
        client.post("/admin/login", data=WRONG, headers={"X-Forwarded-For": "203.0.113.5"})

    other = client.post("/admin/login", data=LOGIN, headers={"X-Forwarded-For": "203.0.113.6"}, follow_redirects=False)
    assert other.status_code == 303

    for _ in range(4):
        client.post("/admin/login", data=WRONG, headers={"X-Forwarded-For": "203.0.113.7"})
    client.post("/admin/login", data=LOGIN, headers={"X-Forwarded-For": "203.0.113.7"})  # success: forget the failures
    for _ in range(4):
        assert client.post("/admin/login", data=WRONG, headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 401


def test_only_the_address_our_own_proxy_appended_counts(client):
    """A client can put anything in X-Forwarded-For; the proxy appends the real
    address last, so cycling through fake first entries must not dodge the lock."""
    for i in range(5):
        client.post("/admin/login", data=WRONG, headers={"X-Forwarded-For": f"10.0.0.{i}, 198.51.100.9"})

    resp = client.post("/admin/login", data=LOGIN, headers={"X-Forwarded-For": "10.9.9.9, 198.51.100.9"})

    assert resp.status_code == 429


def test_throttle_unlocks_after_the_lockout_and_forgets_old_failures():
    clock = {"now": 1000.0}
    throttle = security.LoginThrottle(max_failures=3, window=60, lockout=120)
    original = security.time.monotonic
    security.time.monotonic = lambda: clock["now"]
    try:
        for _ in range(3):
            throttle.record_failure("a")
        assert throttle.seconds_locked("a") > 0
        clock["now"] += 121
        assert throttle.seconds_locked("a") == 0

        throttle.record_failure("b")
        throttle.record_failure("b")
        clock["now"] += 61  # the two failures have aged out of the window
        throttle.record_failure("b")
        assert throttle.seconds_locked("b") == 0
    finally:
        security.time.monotonic = original


# --- cross-site requests and response headers -----------------------------------------------------


def test_a_post_from_another_site_is_refused(admin_client):
    for headers in ({"Origin": "https://evil.example"}, {"Referer": "https://evil.example/page"}, {"Origin": "null"}):
        resp = admin_client.post("/admin/config", data={"s__INGEST_INTERVAL_MINUTES": "10"}, headers=headers)
        assert resp.status_code == 403, headers
    assert admin_client.get("/settings/runtime").json() == {"overrides": {}}  # nothing was changed


def test_a_post_from_this_site_or_without_origin_is_accepted(admin_client):
    same_site = admin_client.post("/admin/config", data={}, headers={"Origin": "http://testserver"})
    no_origin = admin_client.post("/admin/config", data={})  # curl, tests: a browser never omits both

    assert same_site.status_code == 200 and no_origin.status_code == 200


def test_the_public_address_and_forwarded_host_are_accepted_behind_a_proxy(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://trend.example.com")

    public = admin_client.post("/admin/config", data={}, headers={"Origin": "https://trend.example.com"})
    forwarded = admin_client.post(
        "/admin/config", data={}, headers={"Origin": "https://other.example.org", "X-Forwarded-Host": "other.example.org"}
    )
    stranger = admin_client.post("/admin/config", data={}, headers={"Origin": "https://evil.example"})

    assert public.status_code == 200 and forwarded.status_code == 200 and stranger.status_code == 403


def test_reading_pages_is_never_blocked_by_the_origin_check(admin_client):
    assert admin_client.get("/admin/sources", headers={"Origin": "https://evil.example"}).status_code == 200


def test_the_json_api_is_not_subject_to_the_admin_origin_rule(client):
    resp = client.post(
        "/score", headers={"Origin": "https://anywhere.example"},
        json={"source": "s", "title": "t", "url": "https://x/1", "raw_content": "text"},
    )

    assert resp.status_code == 200  # the scheduler calls it without an Origin at all


def test_admin_pages_carry_strict_security_headers_and_the_api_does_not(admin_client):
    page = admin_client.get("/admin/sources")

    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "same-origin"
    assert "content-security-policy" not in admin_client.get("/health").headers


# --- nothing inline: the CSP would block it, so it must not be there ------------------------------------


def _admin_pages(admin_client, client, db_session):
    from tests.test_digest_pages import _digest, _item

    item = _item(db_session, "Page item")
    digest_id = _digest(client, {"markt": [item]})
    return {
        "login": None,
        "sources": "/admin/sources", "items": "/admin/items", "batch": "/admin/items/batch-add",
        "settings": "/admin/settings", "config": "/admin/config", "review": "/admin/review?category=alles",
        "digests": "/admin/digests", "digest": f"/admin/digest/{digest_id}", "missing": "/admin/digest/99999",
    }


def test_no_page_has_inline_scripts_styles_or_event_handlers(admin_client, client, db_session):
    pages = _admin_pages(admin_client, client, db_session)
    texts = {name: admin_client.get(path).text for name, path in pages.items() if path}
    texts["login"] = TestClient(client.app).get("/admin/login").text

    for name, html in texts.items():
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), f"{name}: inline <script>"
        assert not re.search(r"\sstyle=", html), f"{name}: inline style attribute"
        assert not re.search(r"<style", html), f"{name}: <style> block"
        assert not re.search(r"\son(click|submit|change|load|keydown)=", html, re.I), f"{name}: inline handler"


def test_static_files_are_served_and_cannot_escape_their_folder(client):
    css = client.get("/static/app.css")
    js = client.get("/static/digest.js")

    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert client.get("/static/../config.py").status_code == 404
    assert client.get("/static/%2e%2e/config.py").status_code == 404


# --- the public app: the GUI and nothing else ----------------------------------------------------------


@pytest.fixture()
def web_client(db_session):
    from app import web

    def override_get_db():
        yield db_session

    web.app.dependency_overrides[get_db] = override_get_db
    with TestClient(web.app) as c:
        yield c
    web.app.dependency_overrides.clear()


API_ROUTES = [
    ("POST", "/score"), ("GET", "/sources"), ("POST", "/sources"), ("POST", "/sources/evaluate-all"),
    ("POST", "/feedback"), ("GET", "/items/top"), ("GET", "/items/recent-feedback"),
    ("POST", "/items/mark-digested"), ("POST", "/items/rescore"), ("POST", "/digests"),
    ("GET", "/settings/digest"), ("PUT", "/settings/digest"), ("GET", "/settings/runtime"),
    ("POST", "/settings/runtime/env"), ("GET", "/docs"), ("GET", "/redoc"), ("GET", "/openapi.json"),
]


@pytest.mark.parametrize("method, path", API_ROUTES)
def test_the_public_app_has_no_api_routes(web_client, method, path):
    resp = web_client.request(method, path, json={})

    assert resp.status_code in (404, 405), f"{method} {path} answered {resp.status_code}"


def test_the_public_app_serves_the_gui_the_health_check_and_the_old_mail_links(web_client):
    assert web_client.get("/health").json() == {"status": "ok"}
    assert web_client.get("/", follow_redirects=False).headers["location"] == "/admin/"
    assert web_client.get("/static/app.css").status_code == 200
    old_link = web_client.get("/feedback-link", params={"item_id": 1, "label": "interessant"}, follow_redirects=False)
    assert old_link.status_code == 303 and old_link.headers["location"].startswith("/admin/vote-link")

    assert web_client.get("/admin/sources", follow_redirects=False).status_code == 303  # login required
    login = web_client.post("/admin/login", data=LOGIN, follow_redirects=False)
    assert login.status_code == 303
    assert web_client.get("/admin/sources").status_code == 200


def test_the_public_app_applies_the_same_protections(web_client):
    web_client.post("/admin/login", data=LOGIN)

    assert web_client.post("/admin/config", data={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert "script-src 'self'" in web_client.get("/admin/sources").headers["content-security-policy"]
    for _ in range(5):
        web_client.post("/admin/logout")
        web_client.post("/admin/login", data=WRONG)
    assert web_client.post("/admin/login", data=LOGIN).status_code == 429
