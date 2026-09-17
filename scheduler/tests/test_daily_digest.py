import httpx
import pytest

import config
import jobs.daily_digest as daily_digest


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.request = httpx.Request("POST", "http://test/score")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code} error", request=self.request, response=self)

    def json(self) -> dict:
        return self._json_data


class _FakeClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0

    def post(self, url, json):
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


_ENTRY = {
    "title": "T",
    "link": "https://example.com/a",
    "raw_content": "content",
    "source_name": "Test Source",
}
_SOURCE = {"url": "https://source.example.com", "id": 1}


def test_score_entry_succeeds_first_try(monkeypatch):
    monkeypatch.setattr(daily_digest.time, "sleep", lambda s: None)
    client = _FakeClient([_FakeResponse(200, {"item_id": 1, "summary": "s", "relevance_score": 0.7})])

    result = daily_digest.score_entry(client, _ENTRY, _SOURCE)

    assert result["item_id"] == 1
    assert client.calls == 1


def test_score_entry_retries_on_500_then_succeeds(monkeypatch):
    monkeypatch.setattr(daily_digest.time, "sleep", lambda s: None)
    client = _FakeClient(
        [_FakeResponse(500), _FakeResponse(200, {"item_id": 2, "summary": "s", "relevance_score": 0.6})]
    )

    result = daily_digest.score_entry(client, _ENTRY, _SOURCE)

    assert result["item_id"] == 2
    assert client.calls == 2


def test_score_entry_retries_on_connection_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(daily_digest.time, "sleep", lambda s: None)
    request = httpx.Request("POST", "http://test/score")
    client = _FakeClient(
        [
            httpx.ReadError("connection forcibly closed by the remote host", request=request),
            _FakeResponse(200, {"item_id": 3, "summary": "s", "relevance_score": 0.5}),
        ]
    )

    result = daily_digest.score_entry(client, _ENTRY, _SOURCE)

    assert result["item_id"] == 3
    assert client.calls == 2


def test_score_entry_gives_up_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(daily_digest.time, "sleep", lambda s: None)
    client = _FakeClient([_FakeResponse(500), _FakeResponse(500), _FakeResponse(500)])

    with pytest.raises(httpx.HTTPStatusError):
        daily_digest.score_entry(client, _ENTRY, _SOURCE)

    assert client.calls == 3  # 1 initial attempt + 2 retries, matching _SCORE_RETRY_DELAYS


def test_score_entry_does_not_retry_on_4xx(monkeypatch):
    monkeypatch.setattr(daily_digest.time, "sleep", lambda s: None)
    client = _FakeClient([_FakeResponse(404)])

    with pytest.raises(httpx.HTTPStatusError):
        daily_digest.score_entry(client, _ENTRY, _SOURCE)

    assert client.calls == 1


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
    html_out = daily_digest.build_digest_html([_DIGEST_ITEM])

    assert '<a href="https://example.com/article">Kritieke kwetsbaarheid ontdekt</a>' in html_out
    assert '<a href="https://example.com/feed">Example Security Blog</a>' in html_out


def test_build_digest_html_uses_thumbs_for_feedback_links():
    html_out = daily_digest.build_digest_html([_DIGEST_ITEM])

    assert "\U0001F44D" in html_out  # 👍
    assert "\U0001F44E" in html_out  # 👎
    # & is correctly HTML-escaped to &amp; inside the href attribute.
    assert "item_id=1&amp;label=interessant" in html_out
    assert "item_id=1&amp;label=niet_interessant" in html_out


def test_build_digest_html_escapes_untrusted_feed_content():
    malicious_item = {
        **_DIGEST_ITEM,
        "title": '<script>alert(1)</script>',
        "summary": '"><img src=x onerror=alert(2)>',
        "source_name": "<b>Evil</b> Feed",
    }

    html_out = daily_digest.build_digest_html([malicious_item])

    # The dangerous markup must never appear as live tags/attributes — only
    # as inert, escaped text (so a browser/mail client can't execute it).
    assert "<script>" not in html_out
    assert "<img" not in html_out
    assert "<b>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "&lt;img" in html_out


def test_build_digest_html_falls_back_to_plain_text_for_non_http_url():
    item = {**_DIGEST_ITEM, "url": "javascript:alert(1)"}

    html_out = daily_digest.build_digest_html([item])

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
