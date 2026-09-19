"""Is this an acceptable address for a source? Checked when a source is created
(admin form, POST /sources, seed script), so nothing odd gets into the
database in the first place.

This is a syntax-level check: it does not look anything up. The address a
source resolves to is checked again, and pinned, every time it is actually
downloaded (app/safe_fetch.py), because that is what can change later.

Accepted:
- http:// and https:// addresses without user:password, not pointing at an
  address literal that is private/loopback/link-local, not "localhost";
- a bare domain name (example.com) — the "unknown" candidate sources that link
  following and discovery register; they are never fetched as they are;
- mailto:someone@example.com, only for the "mailbox" type (newsletter sources).
Everything else is refused: file:, ftp:, javascript:, data:, paths, "host:port".
"""

import ipaddress
import re
from urllib.parse import urlsplit

from app.safe_fetch import is_public_address

_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)
_HOSTNAME = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.IGNORECASE)
_MAILTO = re.compile(r"^mailto:[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal")


def _refuse_internal_name(host: str) -> None:
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        raise ValueError("het adres wijst naar een intern netwerk")


def validate_source_url(url: str, type_: str) -> None:
    """Raise ValueError (with a message fit to show the admin) if `url` may not
    become a source of this type."""
    value = (url or "").strip()
    if not value:
        raise ValueError("de URL is leeg")

    if type_ == "mailbox":
        if not _MAILTO.match(value):
            raise ValueError("een mailbox-bron heeft de vorm mailto:naam@voorbeeld.nl")
        return

    if _SCHEME.match(value):
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or "://" not in value:
            raise ValueError("alleen http(s)-adressen zijn toegestaan")
        try:
            parts.port  # raises ValueError for a broken port
        except ValueError as exc:
            raise ValueError("ongeldige poort in het adres") from exc
        if parts.username is not None or parts.password is not None:
            raise ValueError("adressen met een gebruikersnaam of wachtwoord zijn niet toegestaan")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise ValueError("er staat geen servernaam in het adres")
        _refuse_internal_name(host)
        try:
            ipaddress.ip_address(host)
            is_literal = True
        except ValueError:
            is_literal = False
        if is_literal and not is_public_address(host):
            raise ValueError("het adres wijst naar een intern of niet-openbaar netwerk")
        return

    # No scheme: only a plain domain name, e.g. "example.com".
    host = value.rstrip(".").lower()
    if not _HOSTNAME.match(host):
        raise ValueError("geen geldig adres: begin met https:// of geef alleen een domeinnaam")
    _refuse_internal_name(host)
