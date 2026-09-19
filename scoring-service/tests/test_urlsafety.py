import pytest

from app import admin as admin_module
from app.urlsafety import validate_source_url


@pytest.mark.parametrize(
    "url, type_",
    [
        ("https://example.com/feed.xml", "rss"),
        ("http://example.com/feed", "rss"),
        ("HTTPS://Example.COM/Feed", "rss"),
        ("https://example.com:8443/feed", "rss"),
        ("https://93.184.216.34/feed", "rss"),
        ("https://[2606:4700:4700::1111]/feed", "rss"),
        ("  https://example.com/feed  ", "rss"),
        ("example.com", "unknown"),
        ("www.example.co.uk", "unknown"),
        ("sub-domain.example.com.", "unknown"),
        ("mailto:nieuwsbrief@example.com", "mailbox"),
    ],
)
def test_ordinary_sources_are_accepted(url, type_):
    validate_source_url(url, type_)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "data:text/html,hi", "gopher://example.com/",
        "mailto:someone@example.com",  # a mailto is only for the mailbox type
        "http:example.com", "https:/example.com",
        "C:\\Windows\\win.ini", "/etc/passwd", "//example.com/x", "example.com/path", "example.com:8080", "not a url", "localhost",
        "", "   ",
    ],
)
def test_odd_addresses_are_refused(url):
    with pytest.raises(ValueError):
        validate_source_url(url, "rss")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/feed", "http://LOCALHOST:8080/", "http://app.localhost/", "http://printer.local/", "https://db.internal/feed",
        "http://127.0.0.1/", "http://10.0.100.8:8080/health", "http://192.168.1.1/", "http://169.254.169.254/latest/",
        "http://100.64.0.1/", "http://[::1]/", "http://[fe80::1]/", "http://[::ffff:10.0.0.1]/", "http://0.0.0.0/",
        "printer.local", "db.internal",
    ],
)
def test_addresses_that_point_inside_are_refused(url):
    with pytest.raises(ValueError, match="intern"):
        validate_source_url(url, "rss")


def test_credentials_in_the_address_and_broken_ports_are_refused():
    for url in ("https://user:pw@example.com/feed", "https://user@example.com/feed"):
        with pytest.raises(ValueError, match="gebruikersnaam"):
            validate_source_url(url, "rss")
    with pytest.raises(ValueError, match="poort"):
        validate_source_url("https://example.com:notaport/feed", "rss")


def test_a_mailbox_source_must_be_a_mailto_address():
    for url in ("https://example.com", "example.com", "mailto:", "mailto:no-at-sign", "mailto:a@b"):
        with pytest.raises(ValueError):
            validate_source_url(url, "mailbox")


# --- where it applies ----------------------------------------------------------------------------------------------


def test_the_json_api_refuses_a_bad_source_with_422_and_creates_nothing(client):
    resp = client.post("/sources", json={"url": "file:///etc/passwd", "type": "rss"})

    assert resp.status_code == 422 and "http" in resp.json()["detail"]
    assert client.get("/sources").json() == []


def test_the_json_api_still_accepts_a_good_source(client):
    resp = client.post("/sources", json={"url": "https://example.com/feed.xml", "type": "rss"})

    assert resp.status_code in (200, 201) and resp.json()["url"] == "https://example.com/feed.xml"


def test_the_admin_form_shows_why_a_source_was_not_added(admin_client):
    resp = admin_client.post("/admin/sources", data={"url": "http://10.0.100.8:8080/health", "type": "rss"}, follow_redirects=True)

    assert resp.status_code == 200
    assert "Bron niet toegevoegd" in resp.text and "intern" in resp.text
    assert "10.0.100.8" not in resp.text.split("Bron niet toegevoegd")[0]  # nothing was stored: only the message repeats it
    assert admin_client.get("/sources").json() == []


def test_the_error_message_in_the_address_is_escaped_and_capped(admin_client):
    resp = admin_client.get("/admin/sources", params={"error": "<script>alert(1)</script>" + "x" * 500})

    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text
    assert "x" * 201 not in resp.text


def test_the_admin_form_accepts_a_good_source(admin_client):
    resp = admin_client.post("/admin/sources", data={"url": "https://good.example.com/feed", "type": "rss"}, follow_redirects=True)

    assert "Bron niet toegevoegd" not in resp.text and "good.example.com" in resp.text


# --- links in the pages ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://example.com/a?b=1", "https://example.com/a?b=1"),
        ("http://example.com", "http://example.com"),
        ("HTTPS://EXAMPLE.COM", "HTTPS://EXAMPLE.COM"),
        (" https://example.com", " https://example.com"),
        ("javascript:alert(1)", "#"),
        ("JaVaScRiPt:alert(1)", "#"),
        (" javascript:alert(1)", "#"),
        ("data:text/html,<script>1</script>", "#"),
        ("vbscript:msgbox(1)", "#"),
        ("file:///etc/passwd", "#"),
        ("//evil.example.com/x", "#"),
        ("/admin/logout", "#"),
        ("", "#"),
        (None, "#"),
    ],
)
def test_only_http_links_become_links(value, expected):
    assert admin_module._safe_url(value) == expected


def test_the_filter_is_registered_for_the_templates():
    assert admin_module.templates.env.filters["safe_url"] is admin_module._safe_url
