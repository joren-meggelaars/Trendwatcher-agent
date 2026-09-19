import httpx
import pytest

import config
import jobs.daily_digest as daily_digest


def _top_item(item_id, title, score=0.5):
    return {
        "item_id": item_id,
        "title": title,
        "url": f"https://example.com/{item_id}",
        "summary": "Samenvatting",
        "relevance_score": score,
        "source_url": "https://www.example.com/feed",
    }


DIGEST_ID = 42


def _patch_run(monkeypatch, items_by_category, *, sent=True, top_n=5, mark_error=None, record_error=None):
    captured = {
        "gets": [], "html": None, "mark_calls": 0, "marked": None,
        "recorded": None, "record_calls": 0, "mailed_calls": [],
    }

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return self._payload if isinstance(self._payload, dict) else [dict(i) for i in self._payload]

    def _fake_get(url, params, timeout):
        captured["gets"].append(params)
        return _Resp(items_by_category.get(params["category"], []))

    def _fake_post(url, json=None, timeout=None):
        if url.endswith("/digests"):
            captured["record_calls"] += 1
            if record_error:
                raise record_error
            captured["recorded"] = json
            return _Resp({"digest_id": DIGEST_ID})
        if url.endswith("/mailed"):
            captured["mailed_calls"].append(url)
            return _Resp({})
        captured["mark_calls"] += 1
        if mark_error:
            raise mark_error
        captured["mark_url"] = url
        captured["marked"] = json["item_ids"]
        return _Resp([])

    def _fake_send(html_body):
        captured["html"] = html_body
        if isinstance(sent, Exception):
            raise sent
        return sent

    monkeypatch.setattr(daily_digest.httpx, "get", _fake_get)
    monkeypatch.setattr(daily_digest.httpx, "post", _fake_post)
    monkeypatch.setattr(daily_digest, "fetch_digest_settings", lambda: {"digest_top_n": top_n})
    monkeypatch.setattr(daily_digest, "send_digest", _fake_send)
    monkeypatch.setattr(config, "FEEDBACK_BASE_URL", "https://trend.example.com")
    return captured


def test_run_mails_undigested_top_n_per_category_then_marks_exactly_those(monkeypatch):
    captured = _patch_run(
        monkeypatch,
        {"markt": [_top_item(1, "Acme overname"), _top_item(2, "Series B")], "nieuws": [_top_item(3, "Kritieke bug")]},
        top_n=2,
    )

    daily_digest.run()

    assert captured["gets"] == [
        {"days": config.DIGEST_LOOKBACK_DAYS, "limit": 2, "category": "markt", "undigested": True},
        {"days": config.DIGEST_LOOKBACK_DAYS, "limit": 2, "category": "nieuws", "undigested": True},
    ]
    assert "Acme overname" in captured["html"] and "Kritieke bug" in captured["html"]
    assert captured["mark_url"].endswith("/items/mark-digested")
    assert captured["marked"] == [1, 2, 3]


def test_run_records_the_digest_first_and_its_buttons_open_that_digest(monkeypatch):
    captured = _patch_run(
        monkeypatch, {"markt": [_top_item(1, "Acme overname")], "nieuws": [_top_item(3, "Kritieke bug")]}
    )

    daily_digest.run()

    assert captured["recorded"] == {
        "kind": "daily",
        "items": [{"item_id": 1, "category": "markt"}, {"item_id": 3, "category": "nieuws"}],
    }
    body = captured["html"]
    assert f"https://trend.example.com/admin/digest/{DIGEST_ID}?vote=1:like" in body
    assert f"https://trend.example.com/admin/digest/{DIGEST_ID}?vote=3:dislike" in body
    assert f'href="https://trend.example.com/admin/digest/{DIGEST_ID}"' in body  # "Open de digest"
    assert "feedback-link" not in body


def test_run_marks_the_digest_mailed_only_after_a_real_send(monkeypatch):
    sent = _patch_run(monkeypatch, {"markt": [_top_item(1, "Acme overname")]})
    daily_digest.run()
    assert sent["mailed_calls"] == [f"{config.SCORING_SERVICE_URL}/digests/{DIGEST_ID}/mailed"]

    dry = _patch_run(monkeypatch, {"markt": [_top_item(1, "Acme overname")]}, sent=False)
    daily_digest.run()
    assert dry["record_calls"] == 1 and dry["mailed_calls"] == []  # recorded (visible in the GUI), but not "mailed"


def test_run_still_mails_with_the_old_links_when_the_digest_cannot_be_recorded(monkeypatch):
    captured = _patch_run(
        monkeypatch,
        {"markt": [_top_item(1, "Acme overname")]},
        record_error=httpx.ConnectError("down", request=httpx.Request("POST", "http://test/digests")),
    )

    daily_digest.run()

    assert "feedback-link?item_id=1&amp;label=interessant" in captured["html"]
    assert captured["mailed_calls"] == []  # nothing to mark: it was never recorded
    assert captured["marked"] == [1]  # the mail went out, so its items are still marked as mailed


def test_run_does_not_mark_anything_on_a_dry_run(monkeypatch):
    captured = _patch_run(monkeypatch, {"markt": [_top_item(1, "Acme overname")]}, sent=False)

    daily_digest.run()

    assert captured["html"] is not None  # the digest was composed and logged
    assert captured["mark_calls"] == 0


def test_run_does_not_mark_anything_when_sending_fails(monkeypatch):
    captured = _patch_run(monkeypatch, {"markt": [_top_item(1, "Acme overname")]}, sent=RuntimeError("Graph down"))

    with pytest.raises(RuntimeError):
        daily_digest.run()

    assert captured["mark_calls"] == 0 and captured["mailed_calls"] == []  # tried again next time, not lost


def test_run_sends_nothing_and_records_nothing_without_new_items(monkeypatch):
    captured = _patch_run(monkeypatch, {})

    daily_digest.run()

    assert captured["html"] is None and captured["mark_calls"] == 0 and captured["record_calls"] == 0


def test_run_survives_a_failing_mark_call_after_the_mail_went_out(monkeypatch, caplog):
    captured = _patch_run(
        monkeypatch,
        {"markt": [_top_item(1, "Acme overname")]},
        mark_error=httpx.ConnectError("down", request=httpx.Request("POST", "http://test")),
    )

    with caplog.at_level("ERROR"):
        daily_digest.run()  # must not raise: the mail is already out

    assert captured["html"] is not None
    assert "niet als gemaild" in caplog.text


def test_run_now_is_a_preview_that_neither_filters_nor_marks_but_is_recorded(monkeypatch):
    captured = _patch_run(monkeypatch, {"markt": [_top_item(1, "Acme overname")]})

    daily_digest.run_now()

    assert all("undigested" not in params for params in captured["gets"])
    assert captured["mark_calls"] == 0
    assert captured["recorded"]["kind"] == "preview"
    assert f"/admin/digest/{DIGEST_ID}?vote=1:like" in captured["html"]


def test_send_digest_reports_whether_it_really_sent(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_DRY_RUN", True)
    assert daily_digest.send_digest("<html/>") is False

    monkeypatch.setattr(config, "DIGEST_DRY_RUN", False)
    monkeypatch.setattr(config, "DIGEST_MAILBOX", "digest@example.com")
    monkeypatch.setattr(config, "DIGEST_TO_EMAIL", "reader@example.com")
    monkeypatch.setattr(daily_digest, "get_graph_token", lambda: "t")

    class _Ok:
        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(daily_digest.httpx, "post", lambda *a, **k: _Ok())
    assert daily_digest.send_digest("<html/>") is True
