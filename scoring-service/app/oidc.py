"""Sign in with Authentik (OpenID Connect): authorization-code flow with PKCE.

Optional: with OIDC_ISSUER empty the GUI only knows the username/password login
(app/auth.py). With it set, the login page gets a "Inloggen met je account"
button and the password form stays underneath as the emergency way in when
Authentik is down.

The sign-in only decides *who* you are; whether you may use the GUI follows
from one Authentik group (OIDC_ADMIN_GROUP). The GUI has no read-only mode, so
there is no viewer group.

Only this server talks to Authentik's token endpoint, through OIDC_INTERNAL_URL
when Authentik is not reachable by its public name from inside the container
(the shared docker network `identity-apps`); the browser is always sent to the
public address. The id_token comes straight from the token endpoint over a
direct server-to-server call, which OpenID Connect Core 3.1.3.7 accepts instead
of checking its signature; issuer, audience, expiry and nonce are checked here.

The sign-in in progress (state, nonce, PKCE verifier, where to go afterwards)
lives in the signed session cookie itself, so nothing is stored server-side.
Same pattern as Clothing Advisor's clothing_advisor/oidc.py.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

import httpx

from app.config import settings

log = logging.getLogger(__name__)

PENDING_KEY = "oidc_pending"
PENDING_TTL = 600  # seconds to finish a sign-in once started
META_TTL = 3600

# (method, url, headers, form body) -> (status, body). Replaced in tests.
Fetch = Callable[[str, str, dict[str, str], dict[str, str] | None], tuple[int, bytes]]


class OidcError(Exception):
    """The sign-in failed (bad state, bad token, Authentik unreachable...). The message is for the log, not the user."""


class OidcDenied(OidcError):
    """Signed in fine, but this person is not in the group that may use the GUI."""


def _httpx_fetch(method: str, url: str, headers: dict[str, str], data: dict[str, str] | None) -> tuple[int, bytes]:
    resp = httpx.request(method, url, headers=headers, data=data, timeout=10.0, follow_redirects=False)
    return resp.status_code, resp.content


fetch: Fetch = _httpx_fetch
_meta: dict[str, Any] | None = None
_meta_at = 0.0


def enabled() -> bool:
    return bool(settings.oidc_issuer)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _request(method: str, url: str, data: dict[str, str] | None = None) -> tuple[int, bytes]:
    """One call to Authentik. With OIDC_INTERNAL_URL the request goes to that
    address but still names the public host (Host + X-Forwarded-Proto), so
    Authentik builds the same issuer and URLs as it does for the browser."""
    headers = {"Accept": "application/json", "User-Agent": "trendwatcher"}
    if settings.oidc_internal_url:
        public, internal = urlparse(url), urlparse(settings.oidc_internal_url)
        url = public._replace(scheme=internal.scheme, netloc=internal.netloc).geturl()
        headers.update({"Host": public.netloc, "X-Forwarded-Proto": public.scheme})
    try:
        return fetch(method, url, headers, data)
    except httpx.HTTPError as exc:
        raise OidcError(f"cannot reach the sign-in service: {exc}") from exc


def _metadata() -> dict[str, Any]:
    global _meta, _meta_at
    if _meta and time.time() - _meta_at < META_TTL:
        return _meta
    issuer = settings.oidc_issuer
    status, body = _request("GET", issuer.rstrip("/") + "/.well-known/openid-configuration")
    if status != 200:
        raise OidcError(f"discovery answered {status}")
    try:
        meta = json.loads(body)
    except ValueError as exc:
        raise OidcError("discovery did not return JSON") from exc
    if str(meta.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
        raise OidcError(
            f"issuer mismatch: OIDC_ISSUER is {issuer!r} but Authentik says {meta.get('issuer')!r} "
            "(through OIDC_INTERNAL_URL, check that the public host is passed on)"
        )
    for key in ("authorization_endpoint", "token_endpoint"):
        if not meta.get(key):
            raise OidcError(f"discovery has no {key}")
    _meta, _meta_at = meta, time.time()
    return meta


def forget_metadata() -> None:
    global _meta, _meta_at
    _meta, _meta_at = None, 0.0


def redirect_uri_for(host: str) -> str:
    """The registered callback on the address the browser is using: it must
    come back to the same host, or the session cookie is not there."""
    uris = settings.oidc_redirect_uri_list
    for uri in uris:
        if urlparse(uri).netloc.lower() == host.strip().lower():
            return uri
    return uris[0]


def start(session: dict, host: str, next_path: str) -> str:
    """Remember a new sign-in in the session; returns where to send the browser."""
    meta = _metadata()
    redirect_uri = redirect_uri_for(host)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    session[PENDING_KEY] = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "next": next_path,
        "redirect_uri": redirect_uri,
        "exp": int(time.time() + PENDING_TTL),
    }
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
        }
    )
    endpoint = meta["authorization_endpoint"]
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"


def finish(session: dict, code: str, state: str) -> tuple[str, str]:
    """Exchange the code and check who this is: (display name, where to go next).
    The pending sign-in is taken out of the session whatever the outcome."""
    pending = session.pop(PENDING_KEY, None)
    if not isinstance(pending, dict) or float(pending.get("exp", 0)) <= time.time():
        raise OidcError("no sign-in in progress (session cookie missing or expired)")
    if not code or not state or not hmac.compare_digest(str(pending["state"]), state):
        raise OidcError("state mismatch")

    meta = _metadata()
    status, body = _request(
        "POST",
        meta["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
            "code_verifier": pending["verifier"],
        },
    )
    if status != 200:
        raise OidcError(f"token endpoint answered {status}: {body[:200]!r}")
    try:
        claims = _claims(json.loads(body)["id_token"])
    except (ValueError, KeyError, TypeError) as exc:
        raise OidcError(f"unusable token response: {exc}") from exc

    if str(claims.get("iss", "")).rstrip("/") != settings.oidc_issuer.rstrip("/"):
        raise OidcError(f"wrong issuer in id_token: {claims.get('iss')!r}")
    aud = claims.get("aud")
    if settings.oidc_client_id not in (aud if isinstance(aud, list) else [aud]):
        raise OidcError(f"id_token is not for this client: aud={aud!r}")
    if float(claims.get("exp", 0)) <= time.time() - 60:
        raise OidcError("id_token has expired")
    if not hmac.compare_digest(str(claims.get("nonce", "")), str(pending["nonce"])):
        raise OidcError("nonce mismatch")
    sub = str(claims.get("sub", ""))
    if not sub:
        raise OidcError("id_token has no sub")

    groups = claims.get("groups")
    if not isinstance(groups, list) or settings.oidc_admin_group not in {str(g) for g in groups}:
        raise OidcDenied(f"{sub} is not in {settings.oidc_admin_group!r}")
    name = str(claims.get("name") or claims.get("preferred_username") or claims.get("email") or sub)
    return name, str(pending["next"])


def _claims(id_token: str) -> dict[str, Any]:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("id_token is not a JWT")
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    if not isinstance(claims, dict):
        raise ValueError("id_token payload is not an object")
    return claims
