"""Session-wide pytest setup shared by the whole suite.

Pin the state-token HMAC secret to a fixed test value. This keeps token sign/verify
deterministic and — critically — ensures the unit suite never falls through to the
Azure-CLI fetch in ``ingest.config.token_secret`` (which would shell out to ``az``).
Loading this from conftest guarantees it runs before any test module is collected, so
it holds even when a single test file is run in isolation (e.g. ``pytest
tests/test_state.py``). Individual tests still monkeypatch this var to assert
cross-secret rejection; monkeypatch restores it afterward.
"""
import os

os.environ.setdefault("BENEFITNAV_TOKEN_SECRET", "test-secret")
