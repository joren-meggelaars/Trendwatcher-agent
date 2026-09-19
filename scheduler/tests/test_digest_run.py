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


def _patch_run(monkeypatch, items_by_category, *, sent=True, top_n=5, mark_error=None):
    captured = {"gets": [], "marked": None, "html": None, "mark_calls": 0}

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return [dict(i) for i in self._payload]

    def _fake_get(url, params, timeout):
        captured["gets"].append(params)
        return _Resp(items_by_category.get(params["category"], []))

    def _fake_post(url, json, timeout):
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


def test_run_does_not_mark_anything_on_a_dry_run(monkeypatch):
    captured = _patch_run(monkeypatch, {"markt": [_top_item(1, "Acme overname")]}, sent=False)

    daily_digest.run()

    assert captured["html"] is not None  # the digest was composed and logged
    assert captured["mark_calls"] == 0


def test_run_does_not_mark_anything_when_sending_fails(monkeypatch):
    captured = _patch_run(monkeypatch, {"markt": [_top_item(1, "Acme overname")]}, sent=RuntimeError("Graph down"))

    with pytest.raises(RuntimeError):
        daily_digest.run()

    assert captured["mark_calls"] == 0  # tried again by the next digest, not lost


def test_run_sends_nothing_and_marks_nothing_without_new_items(monkeypatch):
    captured = _patch_run(monkeypatch, {})

    daily_digest.run()

    assert captured["html"] is None and captured["mark_calls"] == 0


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


def test_run_now_is_a_preview_that_neither_filters_nor_marks(monkeypatch):
    captured = _patch_run(monkeypatch, {"markt": [_top_item(1, "Acme overname")]})

    daily_digest.run_now()

    assert all("undigested" not in params for params in captured["gets"])
    assert captured["mark_calls"] == 0


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
