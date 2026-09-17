import msal
import pytest

import graph_client


class _FakeApp:
    def __init__(self, result: dict) -> None:
        self._result = result

    def acquire_token_for_client(self, scopes):
        return self._result


def test_get_graph_token_returns_access_token(monkeypatch):
    monkeypatch.setattr(graph_client, "_app", None)
    monkeypatch.setattr(
        msal, "ConfidentialClientApplication", lambda **kwargs: _FakeApp({"access_token": "abc123"})
    )
    assert graph_client.get_graph_token() == "abc123"


def test_get_graph_token_raises_on_error_response(monkeypatch):
    monkeypatch.setattr(graph_client, "_app", None)
    monkeypatch.setattr(
        msal,
        "ConfidentialClientApplication",
        lambda **kwargs: _FakeApp({"error": "invalid_client", "error_description": "bad secret"}),
    )
    with pytest.raises(RuntimeError, match="invalid_client"):
        graph_client.get_graph_token()
