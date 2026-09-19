import httpx

import config
import jobs.daily_digest as daily_digest


_DIGEST_ITEM = {
    "item_id": 1,
    "title": "Kritieke kwetsbaarheid ontdekt",
    "url": "https://example.com/article",
    "source_name": "Example Security Blog",
    "source_url": "https://example.com/feed",
    "summary": "Een korte samenvatting.",
    "relevance_score": 0.87,
}


def test_build_digest_html_links_article_and_source():
    html_out = daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]})

    assert '<a href="https://example.com/article">Kritieke kwetsbaarheid ontdekt</a>' in html_out
    assert '<a href="https://example.com/feed">Example Security Blog</a>' in html_out


def test_build_digest_html_uses_thumbs_for_feedback_links():
    html_out = daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]})

    assert "\U0001F44D" in html_out  # 👍
    assert "\U0001F44E" in html_out  # 👎
    # & is correctly HTML-escaped to &amp; inside the href attribute.
    assert "item_id=1&amp;label=interessant" in html_out
    assert "item_id=1&amp;label=niet_interessant" in html_out


def test_build_digest_html_feedback_links_use_public_base_url_not_internal_one(monkeypatch):
    monkeypatch.setattr(config, "SCORING_SERVICE_URL", "http://scoring-service:8000")
    monkeypatch.setattr(config, "FEEDBACK_BASE_URL", "http://10.0.100.8:8000")

    html_out = daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]})

    assert "http://10.0.100.8:8000/feedback-link?item_id=1&amp;label=interessant" in html_out
    assert "scoring-service" not in html_out


def test_build_digest_html_escapes_untrusted_feed_content():
    malicious_item = {
        **_DIGEST_ITEM,
        "title": '<script>alert(1)</script>',
        "summary": '"><img src=x onerror=alert(2)>',
        "source_name": "<b>Evil</b> Feed",
    }

    html_out = daily_digest.build_digest_html({"markt": [malicious_item]})

    # The dangerous markup must never appear as live tags/attributes — only
    # as inert, escaped text (so a browser/mail client can't execute it).
    assert "<script>" not in html_out
    assert "<img" not in html_out
    assert "<b>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "&lt;img" in html_out


def test_build_digest_html_falls_back_to_plain_text_for_non_http_url():
    item = {**_DIGEST_ITEM, "url": "javascript:alert(1)"}

    html_out = daily_digest.build_digest_html({"markt": [item]})

    assert "javascript:" not in html_out
    assert "Kritieke kwetsbaarheid ontdekt" in html_out


def test_send_digest_dry_run_does_not_call_graph(monkeypatch, caplog):
    monkeypatch.setattr(config, "DIGEST_DRY_RUN", True)
    monkeypatch.setattr(config, "DIGEST_MAILBOX", "digest@example.com")
    monkeypatch.setattr(config, "DIGEST_TO_EMAIL", "reader@example.com")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("httpx.post should not be called in dry-run mode")

    monkeypatch.setattr(daily_digest.httpx, "post", _fail_if_called)

    with caplog.at_level("INFO"):
        daily_digest.send_digest("<html>digest</html>")

    assert "console gelogd" in caplog.text


def test_send_digest_skips_graph_when_mailbox_or_recipient_missing(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_DRY_RUN", False)
    monkeypatch.setattr(config, "DIGEST_MAILBOX", "")
    monkeypatch.setattr(config, "DIGEST_TO_EMAIL", "reader@example.com")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("httpx.post should not be called without a mailbox")

    monkeypatch.setattr(daily_digest.httpx, "post", _fail_if_called)

    daily_digest.send_digest("<html>digest</html>")


def test_send_digest_calls_graph_sendmail_when_configured(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_DRY_RUN", False)
    monkeypatch.setattr(config, "DIGEST_MAILBOX", "digest@example.com")
    monkeypatch.setattr(config, "DIGEST_TO_EMAIL", "reader@example.com")
    monkeypatch.setattr(daily_digest, "get_graph_token", lambda: "fake-token")

    captured = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

    def _fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(daily_digest.httpx, "post", _fake_post)

    daily_digest.send_digest("<html>digest</html>")

    assert captured["url"] == "https://graph.microsoft.com/v1.0/users/digest@example.com/sendMail"
    assert captured["headers"]["Authorization"] == "Bearer fake-token"
    assert captured["json"]["message"]["toRecipients"][0]["emailAddress"]["address"] == "reader@example.com"
    assert captured["json"]["message"]["body"]["content"] == "<html>digest</html>"


def test_source_name_uses_host_without_www_or_mailto_address():
    assert daily_digest._source_name("https://www.krebsonsecurity.com/feed/") == "krebsonsecurity.com"
    assert daily_digest._source_name("https://feeds.ncsc.nl/nieuws.rss") == "feeds.ncsc.nl"
    assert daily_digest._source_name("mailto:news@tldrsec.com") == "news@tldrsec.com"


def _top_item(item_id, title, score=0.5):
    return {
        "item_id": item_id,
        "title": title,
        "url": f"https://example.com/{item_id}",
        "summary": "Samenvatting",
        "relevance_score": score,
        "source_url": "https://www.example.com/feed",
    }


def test_build_digest_html_has_a_section_per_category_in_order():
    html_out = daily_digest.build_digest_html(
        {"nieuws": [_DIGEST_ITEM | {"title": "Nieuwsitem"}], "markt": [_DIGEST_ITEM | {"title": "Marktitem"}]}
    )

    assert html_out.index("Marktontwikkeling") < html_out.index("Marktitem")
    assert html_out.index("Marktitem") < html_out.index("Nieuws</h2>")
    assert html_out.index("Nieuws</h2>") < html_out.index("Nieuwsitem")


def test_build_digest_html_says_so_when_a_category_is_empty():
    html_out = daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]})

    assert "Nieuws</h2><p>Geen nieuwe items in deze categorie.</p>" in html_out


def _patch_run_now(monkeypatch, items_by_category, top_n=5):
    captured = {"requests": [], "html": None}

    class _Resp:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return [dict(i) for i in self._items]

    def _fake_get(url, params, timeout):
        captured["requests"].append((url, params))
        return _Resp(items_by_category.get(params["category"], []))

    monkeypatch.setattr(daily_digest.httpx, "get", _fake_get)
    monkeypatch.setattr(daily_digest, "fetch_digest_settings", lambda: {"digest_top_n": top_n})
    monkeypatch.setattr(daily_digest, "send_digest", lambda html_body: captured.__setitem__("html", html_body))
    return captured


def test_run_now_mails_top_items_per_category_without_scoring(monkeypatch):
    captured = _patch_run_now(
        monkeypatch,
        {"markt": [_top_item(7, "Acme overname")], "nieuws": [_top_item(8, "Kritieke bug")]},
        top_n=3,
    )

    daily_digest.run_now()

    assert [(url.rsplit("/", 1)[-1], p["category"], p["limit"], p["days"]) for url, p in captured["requests"]] == [
        ("top", "markt", 3, config.MANUAL_DIGEST_LOOKBACK_DAYS),
        ("top", "nieuws", 3, config.MANUAL_DIGEST_LOOKBACK_DAYS),
    ]
    html_out = captured["html"]
    assert "Acme overname" in html_out and "Kritieke bug" in html_out
    assert html_out.index("Acme overname") < html_out.index("Kritieke bug")
    assert "example.com</a>" in html_out  # derived source name
    assert "item_id=7&amp;label=interessant" in html_out


def test_run_now_still_sends_when_only_one_category_has_items(monkeypatch):
    captured = _patch_run_now(monkeypatch, {"markt": [_top_item(7, "Acme overname")]})

    daily_digest.run_now()

    assert "Acme overname" in captured["html"]
    assert "Geen nieuwe items in deze categorie." in captured["html"]


def test_run_now_sends_nothing_when_no_items_at_all(monkeypatch):
    captured = _patch_run_now(monkeypatch, {})

    daily_digest.run_now()

    assert captured["html"] is None


def test_run_now_sends_nothing_when_scoring_service_unreachable(monkeypatch):
    def _failing_get(url, params, timeout):
        raise httpx.ConnectError("down", request=httpx.Request("GET", url))

    sent = []
    monkeypatch.setattr(daily_digest.httpx, "get", _failing_get)
    monkeypatch.setattr(daily_digest, "fetch_digest_settings", lambda: {"digest_top_n": 5})
    monkeypatch.setattr(daily_digest, "send_digest", lambda html_body: sent.append(html_body))

    daily_digest.run_now()

    assert sent == []
