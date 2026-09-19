"""Fetching URLs that somebody else controls, without letting them aim the
server at itself or at the network behind it.

Every place that downloads something from an address a person, a feed, a
search result or a redirect supplied goes through here (admin "batch-add",
weekly discovery, feed ingest). This is a copy of
scoring-service/app/safe_fetch.py (separate project, same code; keep them
identical apart from the imports and _default_allow_private).

What it guarantees:
- Only http/https, no user:password in the address, only ports 80/443/8080/8443.
- The host must resolve to public addresses only. Loopback, private ranges
  (10/8, 172.16/12, 192.168/16, the Docker networks), link-local
  (169.254.x.x, cloud metadata), CGNAT, multicast and reserved addresses are
  refused, IPv6 and IPv4-mapped IPv6 included. If a name resolves to both a
  public and a private address it is refused.
- The connection is made to the address that was checked (the name is looked
  up once), with the original Host header and TLS name, so a domain that
  answers "public" to the check and "internal" to the connection (DNS
  rebinding) gains nothing.
- Redirects are followed by hand, at most 5, and every hop goes through the
  same checks.
- Time limits (connect, read and overall) and a maximum size that also
  counts the *decoded* bytes (a small gzip cannot expand into gigabytes).

SAFE_FETCH_ALLOW_PRIVATE=true switches the address check off, for a local
test setup only. Console-only setting; never set it on the VM.
"""

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

import config

ALLOWED_PORTS = {80, 443, 8080, 8443}
MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class FetchError(Exception):
    """Anything that stops a fetch; the message is fit to show to the admin."""


class UnsafeURL(FetchError):
    """The address is not allowed to be fetched (as opposed to failing to load)."""


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    ips: tuple[str, ...]
    path_and_query: str

    @property
    def authority(self) -> str:
        """The Host header: the name as given, port only when it is not the default."""
        default = 443 if self.scheme == "https" else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    url: str  # after redirects
    status: int
    content_type: str | None

    @property
    def text(self) -> str:
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", self.content_type or "", re.IGNORECASE)
        if match:
            charset = match.group(1)
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


def _default_allow_private() -> bool:
    return config.SAFE_FETCH_ALLOW_PRIVATE


def is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped  # ::ffff:10.0.0.1 is 10.0.0.1
    return bool(
        address.is_global
        and not (address.is_multicast or address.is_reserved or address.is_loopback
                 or address.is_link_local or address.is_unspecified)
    )


def _resolve(host: str, port: int) -> list[str]:
    """Every address the name points to (one lookup; the caller connects to these)."""
    try:
        ipaddress.ip_address(host)
        return [host]  # already a literal address
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError("de naam kon niet worden opgezocht") from exc
    return list(dict.fromkeys(info[4][0] for info in infos))


def check_public_url(url: str, *, allow_private: bool | None = None) -> Target:
    """Validate an address and return where to connect. Raises UnsafeURL."""
    if allow_private is None:
        allow_private = _default_allow_private()
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL("ongeldig adres") from exc

    if parts.scheme.lower() not in ("http", "https"):
        raise UnsafeURL("alleen http(s)-adressen zijn toegestaan")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURL("adressen met een gebruikersnaam of wachtwoord zijn niet toegestaan")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeURL("er staat geen servernaam in het adres")
    scheme = parts.scheme.lower()
    port = port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS and not allow_private:
        raise UnsafeURL(f"poort {port} is niet toegestaan")
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        if not allow_private:
            raise UnsafeURL("het adres wijst naar deze server zelf")

    ips = _resolve(host, port)
    if not ips:
        raise FetchError("de naam kon niet worden opgezocht")
    if not allow_private and not all(is_public_address(ip) for ip in ips):
        raise UnsafeURL("het adres wijst naar een intern of niet-openbaar netwerk")

    path_and_query = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return Target(scheme, host, port, tuple(ips), path_and_query)


def _pinned_url(target: Target, ip: str) -> str:
    host = f"[{ip}]" if ":" in ip else ip
    return f"{target.scheme}://{host}:{target.port}{target.path_and_query}"


def _read_limited(response: httpx.Response, max_bytes: int, deadline: float) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise FetchError(f"het antwoord is te groot (meer dan {max_bytes // 1024} kB)")
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        size += len(chunk)  # decoded bytes: a compressed bomb is stopped here
        if size > max_bytes:
            raise FetchError(f"het antwoord is te groot (meer dan {max_bytes // 1024} kB)")
        if time.monotonic() > deadline:
            raise FetchError("het ophalen duurde te lang")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch(
    url: str,
    *,
    max_bytes: int = 2_000_000,
    timeout: float = 15.0,
    total_timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    allow_private: bool | None = None,
    _transport: httpx.BaseTransport | None = None,
) -> FetchResult:
    """GET a URL safely (see the module docstring). Raises FetchError (UnsafeURL
    when the address is not allowed); an HTTP status >= 400 also raises."""
    deadline = time.monotonic() + total_timeout
    current = url
    request_headers = {"Accept": "*/*", **(headers or {})}

    with httpx.Client(
        transport=_transport,
        timeout=httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0),
        follow_redirects=False,
    ) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            target = check_public_url(current, allow_private=allow_private)
            hop_headers = {**request_headers, "Host": target.authority}
            last_error: Exception | None = None
            for ip in target.ips:
                try:
                    with client.stream(
                        "GET",
                        _pinned_url(target, ip),
                        headers=hop_headers,
                        extensions={"sni_hostname": target.host.encode("idna").decode("ascii")},
                    ) as response:
                        status = response.status_code
                        location = response.headers.get("location")
                        if status in _REDIRECT_STATUSES and location:
                            current = urljoin(current, location)
                            break  # next hop: validated again from the top
                        if status >= 400:
                            raise FetchError(f"de server antwoordde met HTTP {status}")
                        return FetchResult(
                            _read_limited(response, max_bytes, deadline),
                            current,
                            status,
                            response.headers.get("content-type"),
                        )
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    last_error = exc  # try the next address of this name
                    continue
                except httpx.TimeoutException as exc:
                    raise FetchError("het ophalen duurde te lang") from exc
                except httpx.InvalidURL as exc:
                    # httpx prepares the redirect request itself, and chokes on a nonsense Location header
                    raise UnsafeURL("de server verwijst door naar een ongeldig adres") from exc
                except httpx.HTTPError as exc:
                    raise FetchError(f"het ophalen mislukte ({type(exc).__name__})") from exc
            else:
                raise FetchError("kon geen verbinding maken") from last_error
        raise FetchError(f"te veel redirects (meer dan {MAX_REDIRECTS})")
