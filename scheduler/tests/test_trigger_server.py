import threading
import time
import urllib.request

import trigger_server


def _free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_trigger_endpoint_runs_target_in_background_and_returns_202():
    called = threading.Event()

    def _target():
        called.set()

    port = _free_port()
    server = trigger_server.start(port, _target)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/trigger/daily-digest", method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202

        assert called.wait(timeout=2), "background target was not invoked"
    finally:
        server.shutdown()


def test_unknown_path_returns_404():
    port = _free_port()
    server = trigger_server.start(port, lambda: None)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/nope", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTPError for unknown path")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()


def test_target_exception_is_caught_and_does_not_crash_server(caplog):
    def _failing_target():
        raise RuntimeError("boom")

    port = _free_port()
    server = trigger_server.start(port, _failing_target)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/trigger/daily-digest", method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202
        time.sleep(0.2)  # let the background thread raise
    finally:
        server.shutdown()


def test_digest_now_route_runs_its_own_target():
    daily_called = threading.Event()
    now_called = threading.Event()

    port = _free_port()
    server = trigger_server.start(port, daily_called.set, now_called.set)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/trigger/digest-now", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202

        assert now_called.wait(timeout=2)
        assert not daily_called.is_set()
    finally:
        server.shutdown()


def test_digest_now_route_is_404_when_no_target_configured():
    port = _free_port()
    server = trigger_server.start(port, lambda: None)  # no digest_now_run
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/trigger/digest-now", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
