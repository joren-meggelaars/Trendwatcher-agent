"""Auth for the local admin GUI: one fixed account, session-cookie based.

Deliberately not OAuth/JWT — /admin/* never leaves the local network, so a
signed session cookie (via Starlette's SessionMiddleware) behind a single
username/password is enough.
"""

from fastapi import Request
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_KEY = "is_admin"


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_admin_credentials(username: str, password: str) -> bool:
    if not settings.admin_password_hash:
        return False
    if username != settings.admin_username:
        return False
    return _pwd_context.verify(password, settings.admin_password_hash)


class NotAuthenticated(Exception):
    """Raised by require_admin_session; caught by an app-level handler that
    redirects to /admin/login instead of returning a raw 401/500."""


def require_admin_session(request: Request) -> None:
    if not request.session.get(SESSION_KEY):
        raise NotAuthenticated()
