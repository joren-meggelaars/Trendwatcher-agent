"""Sign in with Authentik (app/oidc.py) against a fake Authentik: the whole
flow through the GUI, the checks on the id_token, and the config guard."""

import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from app import oidc
from app.config import Settings, settings

ISSUER = "https://auth.example.nl/application/o/trendwatcher/"
CALLBACK = "https://tw.example.nl/admin/oidc/callback"
SECRET = "s3cret-for-tests"


def _jwt(claims: dict) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


class FakeAuthentik:
    """Answers discovery and the token endpoint; remembers every call."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.groups = ["trendwatcher-admin"]
        self.claim_overrides: dict = {}
        self.nonce = ""
        self.down = False

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers, data))
        if self.down:
            raise oidc.httpx.ConnectError("connection refused")
        if url.endswith("/.well-known/openid-configuration"):
            return 200, json.dumps(
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": "https://auth.example.nl/application/o/authorize/",
                    "token_endpoint": "https://auth.example.nl/application/o/token/",
                }
            ).encode()
        if url.endswith("/token/"):
            if data.get("client_secret") != SECRET or data.get("code") != "the-code":
                return 400, b'{"error": "invalid_grant"}'
            claims = {
                "iss": ISSUER,
                "aud": "trendwatcher",
                "sub": "user-1",
                "exp": time.time() + 300,
                "nonce": self.nonce,
                "name": "Joren",
                "groups": self.groups,
                **self.claim_overrides,
            }
            return 200, json.dumps({"id_token": _jwt(claims), "access_token": "at"}).encode()
        return 404, b""


@pytest.fixture()
def authentik(monkeypatch):
    fake = FakeAuthentik()
    monkeypatch.setattr(oidc, "fetch", fake)
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", "trendwatcher")
    monkeypatch.setattr(settings, "oidc_client_secret", SECRET)
    monkeypatch.setattr(settings, "oidc_redirect_uris", CALLBACK)
    monkeypatch.setattr(settings, "oidc_internal_url", "")
    monkeypatch.setattr(settings, "oidc_admin_group", "trendwatcher-admin")
    monkeypatch.setattr(settings, "oidc_session_days", 7)
    oidc.forget_metadata()
    yield fake
    oidc.forget_metadata()


def _start(client, authentik, next_path=None):
    """Press the button; returns the authorize URL's query (and tells the fake the nonce)."""
    params = {"next": next_path} if next_path else None
    resp = client.get("/admin/oidc/start", params=params, follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("https://auth.example.nl/application/o/authorize/?")
    query = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
    authentik.nonce = query["nonce"]
    return query


def _logged_in(client) -> bool:
    return client.get("/admin/sources", follow_redirects=False).status_code == 200


def test_without_an_issuer_the_login_page_only_has_the_password_form(client):
    page = client.get("/admin/login").text
    assert "/admin/oidc/start" not in page
    assert 'name="password"' in page
    assert client.get("/admin/oidc/start", follow_redirects=False).headers["location"] == "/admin/login"


def test_with_an_issuer_the_button_comes_first_and_the_password_is_folded_away(client, authentik):
    page = client.get("/admin/login", params={"next": "/admin/digest/5"}).text
    assert "/admin/oidc/start?next=/admin/digest/5" in page
    assert '<details class="login-fallback">' in page  # closed
    assert 'name="password"' in page


def test_a_member_of_the_group_signs_in_and_lands_where_the_login_was_needed(client, authentik):
    query = _start(client, authentik, "/admin/digest/5")
    assert query["client_id"] == "trendwatcher"
    assert query["redirect_uri"] == CALLBACK
    assert query["code_challenge_method"] == "S256"

    resp = client.get(
        "/admin/oidc/callback", params={"code": "the-code", "state": query["state"]}, follow_redirects=False
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/digest/5"
    assert _logged_in(client)
    token_call = next(c for c in authentik.calls if c[1].endswith("/token/"))
    assert token_call[3]["code_verifier"]  # PKCE
    assert token_call[3]["redirect_uri"] == CALLBACK


def test_the_session_lasts_oidc_session_days(client, authentik, monkeypatch):
    query = _start(client, authentik)
    client.get("/admin/oidc/callback", params={"code": "the-code", "state": query["state"]})
    # The expiry sits inside the signed cookie: jump the clock instead of decoding it.
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 6.9 * 86400)
    assert _logged_in(client)
    monkeypatch.setattr(time, "time", lambda: real_time() + 7.1 * 86400)
    assert not _logged_in(client)


def test_someone_outside_the_group_is_refused(client, authentik):
    authentik.groups = ["clothing-advisor-admin"]
    query = _start(client, authentik)

    resp = client.get("/admin/oidc/callback", params={"code": "the-code", "state": query["state"]})

    assert resp.status_code == 403
    assert "geen toegang" in resp.text
    assert not _logged_in(client)


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example/application/o/trendwatcher/"},
        {"aud": "clothing-advisor"},
        {"exp": time.time() - 3600},
        {"nonce": "someone-elses-nonce"},
        {"sub": ""},
    ],
)
def test_a_wrong_id_token_is_refused(client, authentik, overrides):
    authentik.claim_overrides = overrides
    query = _start(client, authentik)

    resp = client.get("/admin/oidc/callback", params={"code": "the-code", "state": query["state"]})

    assert resp.status_code == 400
    assert not _logged_in(client)


def test_a_wrong_state_or_a_replayed_callback_is_refused(client, authentik):
    query = _start(client, authentik)
    assert client.get("/admin/oidc/callback", params={"code": "the-code", "state": "forged"}).status_code == 400
    # The pending sign-in is gone after one attempt: the right state no longer works either.
    assert client.get("/admin/oidc/callback", params={"code": "the-code", "state": query["state"]}).status_code == 400
    assert not _logged_in(client)


def test_a_callback_without_a_started_sign_in_is_refused(client, authentik):
    resp = client.get("/admin/oidc/callback", params={"code": "the-code", "state": "x"})
    assert resp.status_code == 400
    assert '<details class="login-fallback" open>' in resp.text  # straight to the password


def test_a_refusal_from_authentik_shows_the_login_page(client, authentik):
    _start(client, authentik)
    resp = client.get("/admin/oidc/callback", params={"error": "access_denied"})
    assert resp.status_code == 400
    assert not _logged_in(client)


def test_when_authentik_is_down_the_password_form_opens(client, authentik):
    authentik.down = True
    resp = client.get("/admin/oidc/start")
    assert resp.status_code == 502
    assert '<details class="login-fallback" open>' in resp.text


def test_through_the_internal_url_the_public_host_is_passed_on(client, authentik, monkeypatch):
    monkeypatch.setattr(settings, "oidc_internal_url", "http://authentik:9000")
    query = _start(client, authentik)
    client.get("/admin/oidc/callback", params={"code": "the-code", "state": query["state"]})

    for _method, url, headers, _data in authentik.calls:
        assert url.startswith("http://authentik:9000/application/o/")
        assert headers["Host"] == "auth.example.nl"
        assert headers["X-Forwarded-Proto"] == "https"
    assert _logged_in(client)


def test_the_callback_matching_the_browsers_host_is_used(authentik, monkeypatch):
    monkeypatch.setattr(settings, "oidc_redirect_uris", f"{CALLBACK} https://tw.tailnet.ts.net/admin/oidc/callback")
    assert oidc.redirect_uri_for("tw.tailnet.ts.net") == "https://tw.tailnet.ts.net/admin/oidc/callback"
    assert oidc.redirect_uri_for("TW.EXAMPLE.NL") == CALLBACK
    assert oidc.redirect_uri_for("10.0.100.8:8000") == CALLBACK  # unknown host: the first one


def test_the_password_login_keeps_working_next_to_authentik(admin_client, authentik):
    assert _logged_in(admin_client)


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"oidc_client_secret": ""}, "OIDC_CLIENT_SECRET"),
        ({"oidc_redirect_uris": ""}, "OIDC_REDIRECT_URIS"),
        ({"oidc_issuer": "http://auth.example.nl/application/o/trendwatcher/"}, "https"),
        ({"oidc_redirect_uris": "https://tw.example.nl/app/oidc/callback"}, "/admin/oidc/callback"),
        ({"oidc_session_days": 0}, "OIDC_SESSION_DAYS"),
    ],
)
def test_a_half_filled_oidc_block_stops_the_start(fields, message):
    good = {"oidc_issuer": ISSUER, "oidc_client_secret": SECRET, "oidc_redirect_uris": CALLBACK}
    with pytest.raises(ValidationError, match=message):
        Settings(**{**good, **fields})
    Settings(**good)  # the complete block is fine
