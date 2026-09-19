"""Hardening for the admin GUI, which may be reachable from the internet
(HTTPS through a reverse proxy).

- OriginCheckMiddleware: a state-changing request to /admin/* must come from
  this site. Browsers always send an Origin (or at least a Referer) on such a
  request; when it names another site it is refused. Together with the
  SameSite=Lax session cookie this stops cross-site request forgery without
  tokens in every form. Requests without either header (curl, tests) pass: a
  browser never omits both on a cross-site POST.
- SecurityHeadersMiddleware: no caching of admin pages (they show your data),
  no framing, no sniffing, a strict Content-Security-Policy (only own scripts
  and styles: no inline code, so an injected snippet would not run) and no
  Referer leaking the page address to the sites articles link to.
- LoginThrottle: a brake on wrong passwords per client address.
"""

import time
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.config import settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def client_ip(request: Request) -> str:
    """The address a request really came from. Behind the reverse proxy every
    connection comes from the proxy, which appends the real client as the LAST
    entry of X-Forwarded-For; earlier entries are whatever the client sent
    itself, so they are not trusted."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _allowed_hosts(request: Request) -> set[str]:
    hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")}
    if settings.public_base_url:
        hosts.add(urlsplit(settings.public_base_url).netloc)
    return {h.strip().lower() for h in hosts if h.strip()}


class OriginCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _UNSAFE_METHODS and request.url.path.startswith("/admin"):
            source = request.headers.get("origin") or request.headers.get("referer")
            if source is not None and urlsplit(source).netloc.lower() not in _allowed_hosts(request):
                return PlainTextResponse("Verzoek van een andere site geweigerd.", status_code=403)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/admin") or path.startswith("/static"):
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if path.startswith("/admin"):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "same-origin")
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class LoginThrottle:
    """After `max_failures` wrong passwords from one address within `window`
    seconds, that address is locked out for `lockout` seconds. In memory, per
    process (the GUI runs as one process); a restart forgets it."""

    def __init__(self, max_failures: int = 5, window: float = 15 * 60, lockout: float = 15 * 60) -> None:
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def seconds_locked(self, key: str) -> int:
        remaining = self._locked_until.get(key, 0) - time.monotonic()
        return int(remaining) + 1 if remaining > 0 else 0

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        recent.append(now)
        self._failures[key] = recent
        if len(recent) >= self.max_failures:
            self._locked_until[key] = now + self.lockout
            self._failures[key] = []
        self._forget_stale(now)

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def _forget_stale(self, now: float) -> None:
        for key in [k for k, until in self._locked_until.items() if until < now]:
            del self._locked_until[key]
        for key in [k for k, times in self._failures.items() if not times or now - times[-1] >= self.window]:
            del self._failures[key]


login_throttle = LoginThrottle()


def register(app) -> None:
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(OriginCheckMiddleware)
