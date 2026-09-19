import gzip
import re
from pathlib import Path

import httpx
import pytest

from app import safe_fetch
from app.safe_fetch import FetchError, UnsafeURL, check_public_url, fetch

PUBLIC = "93.184.216.34"


def _dns(monkeypatch, table: dict[str, list[str]]):
    """Names resolve as in `table`; an address literal resolves to itself."""
    real = safe_fetch._resolve

    def resolve(host, port):
        if host in table:
            return table[host]
        return real(host, port)  # literals; a name that is not in the table fails like an unknown name

    monkeypatch.setattr(safe_fetch, "_resolve", resolve)


# --- which addresses are allowed ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/", "http://127.1.2.3/x", "http://10.0.100.8:8080/health", "http://172.16.0.1/",
        "http://172.31.255.255/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/", "http://0.0.0.0/", "http://224.0.0.1/", "http://240.0.0.1/", "http://255.255.255.255/",
        "http://[::1]/", "http://[fe80::1]/", "http://[fc00::1]/", "http://[fd12:3456::1]/", "http://[::]/",
        "http://[::ffff:10.0.0.1]/", "http://[::ffff:127.0.0.1]/", "http://[::ffff:169.254.169.254]/",
    ],
)
def test_internal_and_special_addresses_are_refused(url):
    with pytest.raises(UnsafeURL):
        check_public_url(url)


@pytest.mark.parametrize("url", [f"http://{PUBLIC}/", "https://1.1.1.1/dns", "https://[2606:4700:4700::1111]/"])
def test_public_addresses_are_allowed(url):
    assert check_public_url(url).ips


def test_a_name_that_points_inside_is_refused_even_if_it_looks_harmless(monkeypatch):
    _dns(monkeypatch, {"harmless.example.com": ["10.0.100.8"], "mixed.example.com": [PUBLIC, "192.168.0.5"]})

    for url in ("https://harmless.example.com/feed", "https://mixed.example.com/feed"):
        with pytest.raises(UnsafeURL, match="intern"):
            check_public_url(url)  # a mix of public and private counts as private


def test_a_name_that_points_to_public_addresses_is_allowed(monkeypatch):
    _dns(monkeypatch, {"good.example.com": [PUBLIC, "2606:4700:4700::1111"]})

    target = check_public_url("https://good.example.com/feed?a=1")

    assert target.ips == (PUBLIC, "2606:4700:4700::1111")
    assert (target.scheme, target.host, target.port, target.path_and_query) == ("https", "good.example.com", 443, "/feed?a=1")


@pytest.mark.parametrize("url", ["http://localhost/", "http://LOCALHOST:8080/", "http://app.localhost/"])
def test_localhost_names_are_refused_before_any_lookup(url, monkeypatch):
    monkeypatch.setattr(safe_fetch, "_resolve", lambda *a: pytest.fail("must not resolve localhost"))

    with pytest.raises(UnsafeURL):
        check_public_url(url)


@pytest.mark.parametrize("url", ["http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "http://127.1/"])
def test_disguised_loopback_addresses_do_not_get_through(url):
    # the lookup turns these into 127.0.0.1 (or fails); either way nothing is fetched
    with pytest.raises(FetchError):
        check_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd", "file://localhost/etc/passwd", "ftp://example.com/x", "gopher://example.com/",
        "javascript:alert(1)", "data:text/html,<script>1</script>", "//example.com/x", "example.com/feed", "/etc/passwd",
        "C:\\Windows\\win.ini", "", "   ", "http://", "http:///path",
    ],
)
def test_only_http_and_https_addresses_with_a_host_are_accepted(url):
    with pytest.raises(UnsafeURL):
        check_public_url(url)


def test_addresses_with_a_username_or_password_are_refused():
    for url in (f"http://user:secret@{PUBLIC}/", f"http://user@{PUBLIC}/", f"https://:pw@{PUBLIC}/"):
        with pytest.raises(UnsafeURL, match="gebruikersnaam"):
            check_public_url(url)


@pytest.mark.parametrize("port", [22, 25, 53, 3306, 5432, 6379, 8000, 8001, 9200, 81, 5678])
def test_only_web_ports_are_allowed(port):
    with pytest.raises(UnsafeURL, match="poort"):
        check_public_url(f"http://{PUBLIC}:{port}/")


@pytest.mark.parametrize("url", [f"http://{PUBLIC}/", f"http://{PUBLIC}:80/", f"https://{PUBLIC}:443/", f"http://{PUBLIC}:8080/", f"https://{PUBLIC}:8443/"])
def test_the_ordinary_web_ports_are_fine(url):
    assert check_public_url(url).ips == (PUBLIC,)


def test_a_broken_port_is_an_unsafe_address_not_a_crash():
    with pytest.raises(UnsafeURL):
        check_public_url("http://example.com:notaport/")


def test_the_switch_for_local_testing_lets_internal_addresses_through():
    assert check_public_url("http://127.0.0.1:8000/health", allow_private=True).ips == ("127.0.0.1",)
    assert check_public_url("http://localhost:8000/", allow_private=True).host == "localhost"
    with pytest.raises(UnsafeURL):
        check_public_url("file:///etc/passwd", allow_private=True)  # the scheme rule is never switched off


def test_the_switch_defaults_to_off(monkeypatch):
    assert safe_fetch._default_allow_private() is False


# --- fetching ---------------------------------------------------------------------------------------


def _transport(handler):
    return httpx.MockTransport(handler)


def test_the_connection_is_pinned_to_the_checked_address_with_the_original_host_and_tls_name(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})
    seen = {}

    def handler(request: httpx.Request):
        seen.update(host=request.url.host, port=request.url.port, header=request.headers["host"], sni=request.extensions.get("sni_hostname"))
        return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain; charset=utf-8"})

    result = fetch("https://example.com/feed?x=1", _transport=_transport(handler))

    assert result.content == b"hello" and result.status == 200 and result.url == "https://example.com/feed?x=1"
    # httpx leaves the scheme's default port out of the URL: None here means 443
    assert seen == {"host": PUBLIC, "port": None, "header": "example.com", "sni": "example.com"}


def test_a_non_default_port_is_kept_in_the_host_header(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})
    seen = {}

    def handler(request):
        seen["header"], seen["port"] = request.headers["host"], request.url.port
        return httpx.Response(200, content=b"ok")

    fetch("http://example.com:8080/x", _transport=_transport(handler))

    assert seen == {"header": "example.com:8080", "port": 8080}


def test_an_ipv6_address_is_connected_to_in_brackets(monkeypatch):
    _dns(monkeypatch, {"v6.example.com": ["2606:4700:4700::1111"]})
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        return httpx.Response(200, content=b"ok")

    fetch("https://v6.example.com/", _transport=_transport(handler))

    assert seen["host"] == "2606:4700:4700::1111"


def test_a_name_that_answers_public_to_the_check_and_private_to_the_connection_gains_nothing(monkeypatch):
    """DNS rebinding: the name is looked up once and that answer is used to connect."""
    answers = iter([[PUBLIC], ["10.0.100.8"]])
    lookups = []
    monkeypatch.setattr(safe_fetch, "_resolve", lambda host, port: lookups.append(host) or next(answers))
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, content=b"ok")

    fetch("https://rebind.example.com/", _transport=_transport(handler))

    assert lookups == ["rebind.example.com"] and hosts == [PUBLIC]  # never asked twice, never the private answer


def test_a_redirect_to_an_internal_address_is_refused_and_never_requested(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})
    requested = []

    def handler(request):
        requested.append(request.headers["host"])
        return httpx.Response(302, headers={"location": "http://10.0.100.8:8080/admin"})

    with pytest.raises(UnsafeURL):
        fetch("https://example.com/redirector", _transport=_transport(handler))

    assert requested == ["example.com"]  # the internal target was not contacted


def test_a_redirect_to_a_name_that_points_inside_is_refused_too(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC], "sneaky.example.org": ["192.168.1.10"]})

    def handler(request):
        return httpx.Response(301, headers={"location": "https://sneaky.example.org/x"})

    with pytest.raises(UnsafeURL):
        fetch("https://example.com/", _transport=_transport(handler))


@pytest.mark.parametrize("location", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "gopher://example.com/"])
def test_a_redirect_to_another_scheme_is_refused(monkeypatch, location):
    _dns(monkeypatch, {"example.com": [PUBLIC]})

    with pytest.raises(UnsafeURL):
        fetch("https://example.com/", _transport=_transport(lambda r: httpx.Response(302, headers={"location": location})))


def test_relative_redirects_are_followed_and_the_final_address_is_reported(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})

    def handler(request):
        if request.headers["host"] == "example.com" and request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new/place"})
        return httpx.Response(200, content=b"arrived")

    result = fetch("https://example.com/old", _transport=_transport(handler))

    assert result.content == b"arrived" and result.url == "https://example.com/new/place"


def test_too_many_redirects_stop_at_five(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(FetchError, match="redirects"):
        fetch("https://example.com/loop", _transport=_transport(handler))

    assert len(calls) == safe_fetch.MAX_REDIRECTS + 1


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_an_error_status_is_a_fetch_error(monkeypatch, status):
    _dns(monkeypatch, {"example.com": [PUBLIC]})

    with pytest.raises(FetchError, match=str(status)):
        fetch("https://example.com/", _transport=_transport(lambda r: httpx.Response(status)))


def test_an_announced_size_over_the_limit_is_refused_before_reading(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})

    def handler(request):
        return httpx.Response(200, headers={"content-length": "50000000"}, content=b"x")

    with pytest.raises(FetchError, match="te groot"):
        fetch("https://example.com/", max_bytes=1_000_000, _transport=_transport(handler))


def test_a_body_that_grows_past_the_limit_is_cut_off_even_without_a_content_length(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})

    def handler(request):
        return httpx.Response(200, content=iter([b"a" * 60_000] * 50))  # streamed, no length

    with pytest.raises(FetchError, match="te groot"):
        fetch("https://example.com/", max_bytes=100_000, _transport=_transport(handler))


def test_a_compressed_bomb_is_stopped_by_the_decoded_size(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})
    bomb = gzip.compress(b"\0" * 50_000_000)  # 50 MB of zeros, a few dozen kB on the wire
    assert len(bomb) < 100_000

    def handler(request):
        return httpx.Response(200, content=bomb, headers={"content-encoding": "gzip"})

    with pytest.raises(FetchError, match="te groot"):
        fetch("https://example.com/", max_bytes=1_000_000, _transport=_transport(handler))


def test_an_overall_time_limit_applies_to_a_slowly_read_body(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})

    def handler(request):
        return httpx.Response(200, content=iter([b"drip"] * 10))

    with pytest.raises(FetchError, match="te lang"):
        fetch("https://example.com/", total_timeout=-1, _transport=_transport(handler))


def test_a_read_timeout_is_a_fetch_error(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})

    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(FetchError, match="te lang"):
        fetch("https://example.com/", _transport=_transport(handler))


def test_the_next_address_is_tried_when_one_refuses_the_connection(monkeypatch):
    _dns(monkeypatch, {"example.com": ["8.8.8.8", "8.8.4.4"]})
    tried = []

    def handler(request):
        tried.append(request.url.host)
        if request.url.host == "8.8.8.8":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, content=b"second")

    assert fetch("https://example.com/", _transport=_transport(handler)).content == b"second"
    assert tried == ["8.8.8.8", "8.8.4.4"]


def test_no_reachable_address_is_a_fetch_error(monkeypatch):
    _dns(monkeypatch, {"example.com": ["8.8.8.8"]})

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(FetchError, match="verbinding"):
        fetch("https://example.com/", _transport=_transport(handler))


def test_a_name_that_does_not_exist_is_a_fetch_error(monkeypatch):
    def unknown(host, port):
        raise FetchError("de naam kon niet worden opgezocht")

    monkeypatch.setattr(safe_fetch, "_resolve", unknown)

    with pytest.raises(FetchError, match="opgezocht"):
        fetch("https://does-not-exist.invalid/")


def test_text_uses_the_declared_charset_and_never_crashes_on_a_bad_one():
    latin = safe_fetch.FetchResult("café".encode("latin-1"), "https://x/", 200, "text/html; charset=ISO-8859-1")
    bogus = safe_fetch.FetchResult("plain".encode(), "https://x/", 200, "text/html; charset=no-such-charset")
    none = safe_fetch.FetchResult(b"\xff\xfeabc", "https://x/", 200, None)

    assert latin.text == "café" and bogus.text == "plain" and "abc" in none.text


def test_request_headers_are_passed_on(monkeypatch):
    _dns(monkeypatch, {"example.com": [PUBLIC]})
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, content=b"ok")

    fetch("https://example.com/", headers={"User-Agent": "test-agent", "Accept": "application/rss+xml"}, _transport=_transport(handler))

    assert seen["user-agent"] == "test-agent" and seen["accept"] == "application/rss+xml" and seen["host"] == "example.com"


# --- the scheduler has its own copy of this module: they must not drift apart ------------------------------------


def _code(path: Path) -> str:
    """The module without the parts that legitimately differ (docstring intro, config import)."""
    text = path.read_text(encoding="utf-8")
    body = text[text.index("ALLOWED_PORTS ="):]
    body = re.sub(r"return (settings\.safe_fetch_allow_private|config\.SAFE_FETCH_ALLOW_PRIVATE)", "return <allow-private-setting>", body)
    return body


def test_the_scheduler_copy_of_safe_fetch_is_identical_apart_from_its_config_import():
    scheduler_copy = Path(__file__).resolve().parents[2] / "scheduler" / "safe_fetch.py"
    if not scheduler_copy.exists():
        pytest.skip("the scheduler project is not next to this one (e.g. inside the Docker image)")

    assert _code(Path(safe_fetch.__file__)) == _code(scheduler_copy)
