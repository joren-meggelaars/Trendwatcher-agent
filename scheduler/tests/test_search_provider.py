import config
from search_provider import BraveSearchProvider, get_search_provider


def test_get_search_provider_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_PROVIDER", "")
    monkeypatch.setattr(config, "SEARCH_API_KEY", "")
    assert get_search_provider() is None


def test_get_search_provider_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_PROVIDER", "brave")
    monkeypatch.setattr(config, "SEARCH_API_KEY", "")
    assert get_search_provider() is None


def test_get_search_provider_returns_brave_provider_when_configured(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_PROVIDER", "brave")
    monkeypatch.setattr(config, "SEARCH_API_KEY", "test-key")
    provider = get_search_provider()
    assert isinstance(provider, BraveSearchProvider)
