"""Tests for ingest.config.token_secret — the shared HMAC secret resolver.

The whole point of this function is that the signer (FastAPI) and the verifier (the
MCP container) resolve the *same* secret: env var on the container, az-fetched
Container Apps secret locally. These tests pin that contract without ever calling `az`.
"""
from __future__ import annotations

import pytest

from ingest import config


@pytest.fixture(autouse=True)
def _clear_cache():
    """token_secret is lru_cached; clear it around each test so env changes take."""
    config.token_secret.cache_clear()
    yield
    config.token_secret.cache_clear()


def test_prefers_env_var_without_calling_az(monkeypatch):
    monkeypatch.setenv("BENEFITNAV_TOKEN_SECRET", "from-env")
    monkeypatch.setattr(config, "_az",
                        lambda *a: pytest.fail("az must not be called when env is set"))
    assert config.token_secret() == "from-env"


def test_fetches_container_secret_when_env_unset(monkeypatch):
    monkeypatch.delenv("BENEFITNAV_TOKEN_SECRET", raising=False)
    captured: dict = {}

    def fake_az(*args: str) -> str:
        captured["args"] = args
        return "from-container"

    monkeypatch.setattr(config, "_az", fake_az)
    assert config.token_secret() == "from-container"
    # Targets the right Container Apps secret on the trust-core MCP app.
    assert "containerapp" in captured["args"]
    assert config.MCP_CONTAINER_APP in captured["args"]
    assert config.MCP_TOKEN_SECRET_NAME in captured["args"]


def test_result_is_cached_one_az_call(monkeypatch):
    monkeypatch.delenv("BENEFITNAV_TOKEN_SECRET", raising=False)
    calls = {"n": 0}

    def fake_az(*args: str) -> str:
        calls["n"] += 1
        return "cached-value"

    monkeypatch.setattr(config, "_az", fake_az)
    assert config.token_secret() == "cached-value"
    assert config.token_secret() == "cached-value"
    assert calls["n"] == 1  # second call served from the lru_cache, no extra az
