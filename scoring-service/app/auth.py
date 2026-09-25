"""Auth for the admin GUI: one fixed account, session-cookie based.

A signed session cookie (Starlette's SessionMiddleware) behind a single
username/password. Optionally people sign in through Authentik instead
(app/oidc.py); that ends in the same session, and the password login then
stays as the emergency way in.
The GUI can be reachable from the internet, so a few things are stricter than
"local only" would need: bcrypt hash, a brake on wrong passwords
(app/security.py), a server-side expiry on every session and a new session on
every login.

How long a login lasts is decided at login: "onthoud mij" gives 30 days, without
it 12 hours. The cookie itself is valid for 30 days (see SESSION_MAX_AGE); the
expiry stored *inside* the signed session is what is enforced.
"""

import time

from fastapi import Request
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_KEY = "is_admin"
SESSION_EXPIRES_KEY = "exp"
SESSION_USER_KEY = "user"  # set by a sign-in through Authentik (app/oidc.py)

REMEMBER_DAYS = 30
SHORT_SESSION_HOURS = 12
SESSION_MAX_AGE = REMEMBER_DAYS * 24 * 60 * 60  # cookie lifetime; the stored expiry is what counts

DEFAULT_LANDING = "/admin/sources"


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_admin_credentials(username: str, password: str) -> bool:
    if not settings.admin_password_hash:
        return False
    if username != settings.admin_username:
        return False
    return _pwd_context.verify(password, settings.admin_password_hash)


def start_session(request: Request, remember: bool, *, seconds: int | None = None, user: str = "") -> None:
    """A fresh session for a successful login (never reuse the one from before
    it), valid 30 days with `remember`, otherwise 12 hours. A sign-in through
    Authentik passes its own lifetime (`seconds`) and who signed in (`user`)."""
    request.session.clear()
    request.session[SESSION_KEY] = True
    if seconds is None:
        seconds = REMEMBER_DAYS * 24 * 3600 if remember else SHORT_SESSION_HOURS * 3600
    request.session[SESSION_EXPIRES_KEY] = int(time.time() + min(seconds, SESSION_MAX_AGE))
    if user:
        request.session[SESSION_USER_KEY] = user


def safe_next(value: str | None, default: str = DEFAULT_LANDING) -> str:
    """Where to go after login, taken from the URL: only a path inside the
    admin, never another site (open redirect) and never the login page itself."""
    if (
        not value
        or not value.startswith("/admin/")
        or value.startswith("/admin/login")
        or "\\" in value
        or "\r" in value
        or "\n" in value
    ):
        return default
    return value


class NotAuthenticated(Exception):
    """Raised by require_admin_session; caught by an app-level handler that
    redirects to /admin/login instead of returning a raw 401/500."""


def require_admin_session(request: Request) -> None:
    if not request.session.get(SESSION_KEY):
        raise NotAuthenticated()
    expires = request.session.get(SESSION_EXPIRES_KEY)
    if expires is None or expires < time.time():
        request.session.clear()
        raise NotAuthenticated()
