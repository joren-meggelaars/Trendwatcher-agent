import config
import jobs.mailbox_ingest as mailbox_ingest


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._json_data


def test_strip_html_decodes_entities_and_removes_tags():
    html_body = "<p>Kwetsbaarheid &amp; patch <b>nu</b> beschikbaar</p>"
    assert mailbox_ingest._strip_html(html_body) == "Kwetsbaarheid & patch nu beschikbaar"


def test_run_skips_when_disabled(monkeypatch, caplog):
    monkeypatch.setattr(config, "MAILBOX_INGEST_ENABLED", False)

    def _fail_if_called():
        raise AssertionError("get_graph_token should not be called when disabled")

    monkeypatch.setattr(mailbox_ingest, "get_graph_token", _fail_if_called)

    with caplog.at_level("INFO"):
        mailbox_ingest.run()

    assert "staat uit" in caplog.text


def test_run_skips_when_enabled_but_no_mailbox_configured(monkeypatch, caplog):
    monkeypatch.setattr(config, "MAILBOX_INGEST_ENABLED", True)
    monkeypatch.setattr(config, "DIGEST_MAILBOX", "")

    def _fail_if_called():
        raise AssertionError("get_graph_token should not be called without a mailbox")

    monkeypatch.setattr(mailbox_ingest, "get_graph_token", _fail_if_called)

    with caplog.at_level("WARNING"):
        mailbox_ingest.run()

    assert "DIGEST_MAILBOX is leeg" in caplog.text


def test_ensure_source_for_sender_reuses_existing_source():
    calls = {"post": 0}

    class _FakeClient:
        def get(self, url):
            assert url.endswith("/sources")
            return _FakeResponse([{"id": 1, "url": "mailto:news@tldrsec.com"}])

        def post(self, url, json):
            calls["post"] += 1
            raise AssertionError("should not create a new source when one already exists")

    source = mailbox_ingest.ensure_source_for_sender(_FakeClient(), "News@TldrSec.com")
    assert source == {"id": 1, "url": "mailto:news@tldrsec.com"}
    assert calls["post"] == 0


def test_ensure_source_for_sender_creates_when_missing():
    class _FakeClient:
        def get(self, url):
            return _FakeResponse([])

        def post(self, url, json):
            assert json == {
                "url": "mailto:news@tldrsec.com",
                "type": "mailbox",
                "discovery_method": "mailbox",
            }
            return _FakeResponse({"id": 2, "url": "mailto:news@tldrsec.com"})

    source = mailbox_ingest.ensure_source_for_sender(_FakeClient(), "news@tldrsec.com")
    assert source["id"] == 2


def test_score_message_builds_expected_payload():
    class _FakeClient:
        def post(self, url, json):
            self.sent = json
            return _FakeResponse({"item_id": 1, "summary": "s", "relevance_score": 0.7})

    client = _FakeClient()
    message = {
        "subject": "tl;dr sec #250",
        "body": {"content": "<p>Deze week: <b>een hoop</b> nieuws</p>"},
        "webLink": "https://outlook.office.com/mail/id/xyz",
    }
    source = {"id": 3}

    result = mailbox_ingest.score_message(client, message, "news@tldrsec.com", source)

    assert client.sent["source"] == "news@tldrsec.com"
    assert client.sent["title"] == "tl;dr sec #250"
    assert client.sent["url"] == "https://outlook.office.com/mail/id/xyz"
    assert client.sent["raw_content"] == "Deze week: een hoop nieuws"
    assert client.sent["source_id"] == 3
    assert result["relevance_score"] == 0.7
