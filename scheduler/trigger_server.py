"""Minimal internal HTTP server so the admin GUI's "verstuur nu" button can
kick off an immediate digest, and so the scheduler can be told to re-read the
settings changed in the GUI without waiting for the next sync tick.

Deliberately stdlib-only (no FastAPI/uvicorn) to keep the scheduler's
dependency footprint minimal, matching config.py's own reasoning. Only meant
to be reachable from scoring-service over the shared Docker network — never
published to the host/internet (see docker-compose.yml: no `ports:` entry
for this).
"""

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        logger.info("%s - %s", self.address_string(), format % args)

    def do_POST(self) -> None:
        routes = {
            "/trigger/daily-digest": self.server.daily_digest_run,  # type: ignore[attr-defined]
            "/trigger/digest-now": self.server.digest_now_run,  # type: ignore[attr-defined]
            "/trigger/sync-settings": self.server.sync_settings_run,  # type: ignore[attr-defined]
        }
        target = routes.get(self.path)
        if target is None:
            self._respond(404, b'{"error":"not found"}')
            return
        self._run_in_background(target)
        self._respond(202, b'{"status":"accepted"}')

    def _run_in_background(self, target) -> None:
        def _wrapped():
            try:
                target()
            except Exception:
                logger.exception("Handmatig getriggerde job faalde")

        threading.Thread(target=_wrapped, daemon=True).start()

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(port: int, daily_digest_run, digest_now_run=None, sync_settings_run=None) -> ThreadingHTTPServer:
    """Starts the trigger server on a background thread and returns it
    (caller keeps a reference so it isn't garbage-collected).

    daily_digest_run: the scheduled digest (mail the best not-yet-mailed items).
    digest_now_run: immediate preview mail of the best already-scored items —
    what the admin GUI's "verstuur nu" button uses.
    sync_settings_run: re-read the settings changed in the admin GUI now,
    instead of at the next sync tick (called after saving /admin/config).
    """
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)  # noqa: S104 - internal network only, see module docstring
    server.daily_digest_run = daily_digest_run  # type: ignore[attr-defined]
    server.digest_now_run = digest_now_run  # type: ignore[attr-defined]
    server.sync_settings_run = sync_settings_run  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Trigger-server luistert intern op poort %d", port)
    return server
