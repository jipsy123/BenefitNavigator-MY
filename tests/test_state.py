"""Tests for the signed conversation-state token (mas/state.py).

The token is trust-critical: it is the carrier that lets agents shuttle facts to
the deterministic core *without being able to alter them*. So the security
properties (tamper, forge, replay, oversize, shape) get the most coverage.
"""
from __future__ import annotations

import json

import pytest

from mas import state
from mas.state import ChatState, InvalidToken, decode, encode


def _full_state() -> ChatState:
    return ChatState(
        facts={"citizen": True, "age": 34, "individual_income": 800},
        presumed={"is_oku": {"value": False, "reason_ms": "tidak dinyatakan"}},
        asked=("age", "individual_income"),
        retrieval_query_ms="bantuan ibu tunggal",
        passages=({"content": "petikan", "doc_name": "x"},),
        assessment={"ok": True, "total_monthly_min": 100},
        plan={"baseline_monthly_min": 100, "steps": []},
        letters=({"program_name_ms": "STR", "body_ms": "..."},),
        history=({"action": "ASSESS", "rationale_ms": "nilai", "turn": 1},),
        turn=3,
        lang="ms",
    )


# --- round trip ------------------------------------------------------------------

def test_round_trip_preserves_all_fields():
    original = _full_state()
    restored = decode(encode(original))
    assert restored == original


def test_round_trip_empty_state():
    assert decode(encode(ChatState())) == ChatState()


def test_evolve_is_immutable():
    s = ChatState(turn=1)
    s2 = s.evolve(turn=2, lang="ta")
    assert s.turn == 1 and s.lang == "en"      # original untouched
    assert s2.turn == 2 and s2.lang == "ta"


# --- malformed input -------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "no-dot-here", "a.b.c", "...", "@@@.@@@"])
def test_garbage_tokens_rejected(bad):
    with pytest.raises(InvalidToken):
        decode(bad)


def test_non_string_token_rejected():
    with pytest.raises(InvalidToken):
        decode(None)  # type: ignore[arg-type]


# --- tamper / forge --------------------------------------------------------------

def test_tampered_payload_rejected():
    token = encode(_full_state())
    payload_b64, sig = token.split(".")
    # Flip one character of the payload; signature no longer matches.
    flipped = ("Z" if payload_b64[0] != "Z" else "Y") + payload_b64[1:]
    with pytest.raises(InvalidToken, match="signature"):
        decode(f"{flipped}.{sig}")


def test_tampered_signature_rejected():
    token = encode(_full_state())
    payload_b64, sig = token.split(".")
    flipped = ("Z" if sig[0] != "Z" else "Y") + sig[1:]
    with pytest.raises(InvalidToken, match="signature"):
        decode(f"{payload_b64}.{flipped}")


def test_secret_mismatch_rejected(monkeypatch):
    """A token signed under one secret must not verify under another — this is the
    property that stops a hosted agent from forging its own facts."""
    monkeypatch.setenv("BENEFITNAV_TOKEN_SECRET", "secret-A")
    token = encode(_full_state())
    monkeypatch.setenv("BENEFITNAV_TOKEN_SECRET", "secret-B")
    with pytest.raises(InvalidToken, match="signature"):
        decode(token)


def test_secret_delegates_to_config_when_env_unset(monkeypatch):
    """With no BENEFITNAV_TOKEN_SECRET in the environment — the local orchestrator's
    default — the key is sourced from config.token_secret (the same Container Apps
    secret the MCP container verifies with), NOT the per-process random fallback."""
    monkeypatch.delenv("BENEFITNAV_TOKEN_SECRET", raising=False)
    from ingest import config
    monkeypatch.setattr(config, "token_secret", lambda: "shared-with-container")
    assert state._secret() == b"shared-with-container"


def test_agent_cannot_smuggle_a_fact(monkeypatch):
    """Simulate an agent trying to flip is_oku inside the payload: re-encoding the
    altered facts requires the secret it does not have, so the forged token fails."""
    monkeypatch.setenv("BENEFITNAV_TOKEN_SECRET", "server-only")
    honest = decode(encode(_full_state()))
    assert honest.facts.get("is_oku") is None
    # The "agent" (no secret) builds a payload claiming disability and guesses a sig.
    forged_payload = state._b64u_encode(
        json.dumps({**state._to_payload(honest), "facts": {**honest.facts, "is_oku": True}},
                   separators=(",", ":")).encode("utf-8"))
    with pytest.raises(InvalidToken):
        decode(f"{forged_payload}.{state._b64u_encode(b'guessed-signature')}")


# --- replay / size ---------------------------------------------------------------

def test_expired_token_rejected(monkeypatch):
    token = encode(_full_state())
    real_time = state.time.time()
    monkeypatch.setattr(state.time, "time",
                        lambda: real_time + state._MAX_AGE_SECONDS + 100)
    with pytest.raises(InvalidToken, match="expired"):
        decode(token)


def test_oversize_token_rejected():
    huge = ChatState(facts={f"k{i}": "x" * 200 for i in range(500)})
    with pytest.raises(InvalidToken, match="too large"):
        decode(encode(huge))


def test_too_many_asked_rejected(monkeypatch):
    """A token whose `asked` list exceeds the cap is rejected even when validly
    signed — the shape guard runs after the signature check."""
    monkeypatch.setenv("BENEFITNAV_TOKEN_SECRET", "s")
    payload = {**state._to_payload(ChatState()),
               "asked": [f"f{i}" for i in range(state._MAX_ASKED + 1)]}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    token = f"{state._b64u_encode(raw)}.{state._sign(raw)}"
    with pytest.raises(InvalidToken, match="asked"):
        decode(token)
