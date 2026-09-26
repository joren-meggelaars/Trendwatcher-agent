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

    assert 'href="https://example.com/article"' in html_out and ">Kritieke kwetsbaarheid ontdekt</a>" in html_out
    assert "Example Security Blog" in html_out  # the source, as text next to the score
    assert "score 0.87" in html_out


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
    assert html_out.index("Marktitem") < html_out.index("Nieuws<span")  # the heading, not the item title
    assert html_out.index("Nieuws<span") < html_out.index("Nieuwsitem")


def test_build_digest_html_says_so_when_a_category_is_empty():
    html_out = daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]})

    assert html_out.index("Nieuws<span") < html_out.index("Geen nieuwe items in deze categorie.")


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

    class _Recorded:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"digest_id": 42}

    monkeypatch.setattr(daily_digest.httpx, "get", _fake_get)
    monkeypatch.setattr(daily_digest.httpx, "post", lambda *a, **k: _Recorded())  # recording the digest
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
    assert "example.com &nbsp;" in html_out  # derived source name, shown as text
    assert "/admin/digest/42?vote=7:like" in html_out


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


# --- the look of the mail ---------------------------------------------------------------------


def test_the_mail_is_built_from_tables_with_inline_styles_and_has_no_scripts():
    html_out = daily_digest.build_digest_html({"markt": [_DIGEST_ITEM], "nieuws": [_DIGEST_ITEM]}, digest_id=5)

    assert 'role="presentation"' in html_out and "<table" in html_out
    assert "<script" not in html_out.lower()
    assert "display:flex" not in html_out and "grid" not in html_out  # Outlook desktop would ignore them
    assert 'bgcolor="#4f46e5"' in html_out  # header colour as an attribute too, for Outlook


def test_the_mail_supports_dark_mode_and_a_small_screen():
    html_out = daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]})

    assert "prefers-color-scheme: dark" in html_out
    assert 'name="color-scheme"' in html_out
    assert "max-width: 520px" in html_out


def test_the_header_shows_the_date_in_dutch_and_what_is_in_the_digest():
    from datetime import datetime, timezone

    html_out = daily_digest.build_digest_html(
        {"markt": [_DIGEST_ITEM, _DIGEST_ITEM], "nieuws": [_DIGEST_ITEM]},
        when=datetime(2026, 9, 19, 7, 0, tzinfo=timezone.utc),
    )

    assert "19 september 2026" in html_out
    assert "2 marktontwikkeling" in html_out and "1 nieuws" in html_out
    assert "Dagelijkse digest" in html_out


def test_every_item_gets_both_buttons_pointing_at_its_own_vote(monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_BASE_URL", "https://trend.example.com")
    html_out = daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]}, digest_id=9)

    assert "https://trend.example.com/admin/digest/9?vote=1:like" in html_out
    assert "https://trend.example.com/admin/digest/9?vote=1:dislike" in html_out
    assert "Interessant" in html_out and "Niet interessant" in html_out


def test_an_item_without_a_summary_still_renders_and_a_non_http_link_is_defused():
    item = {**_DIGEST_ITEM, "summary": "", "url": "javascript:alert(1)"}

    html_out = daily_digest.build_digest_html({"markt": [item]})

    assert "javascript:" not in html_out
    assert "Kritieke kwetsbaarheid ontdekt" in html_out


def test_the_mail_shows_the_local_date_not_the_utc_one(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(config, "TIMEZONE", "Europe/Amsterdam")
    late_evening_utc = datetime(2026, 9, 19, 22, 30, tzinfo=timezone.utc)  # already the 20th in the Netherlands

    assert "20 september 2026" in daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]}, when=late_evening_utc)
    assert "20 september 2026" in daily_digest.build_digest_html({"markt": [_DIGEST_ITEM]}, when=late_evening_utc.replace(tzinfo=None))
