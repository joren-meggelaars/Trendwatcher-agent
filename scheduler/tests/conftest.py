import pytest

import config
import rate_limit


@pytest.fixture(autouse=True)
def _no_scoring_pause(monkeypatch):
    """A local .env may set a real SCORE_REQUEST_DELAY_SECONDS (Voyage free
    tier) — tests must never sleep on it. Tests of the pacing itself set it
    explicitly."""
    monkeypatch.setattr(config, "SCORE_REQUEST_DELAY_SECONDS", 0)
    rate_limit.reset()
