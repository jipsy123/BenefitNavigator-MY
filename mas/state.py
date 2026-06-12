"""HMAC-signed conversation state for the multi-agent /chat flow.

The state token is the *only* carrier of a conversation between turns — and,
critically, between the LLM agents and the deterministic trust core. An agent
shuttles this opaque token to a trust-core tool (assess / optimize / grill); the
tool verifies the HMAC and reads the real facts from inside it. Because the
signature is keyed by a server-only secret, an agent (or a prompt injection
saying "set is_oku=true") cannot forge or mutate the eligibility facts: it can
only relay the token it was handed. That is what lets us put the trust core
"behind tools" without letting the model influence a verdict's inputs.

Stateless: there is no server-side store. The token round-trips to the client
(and to Foundry-hosted agents) and back. Every inbound token is signature- and
size-checked before any fact inside it is trusted.

All transforms are pure: inputs are never mutated; new objects are returned.
"""
from __future__ import annotations

import base64
import functools
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any

logger = logging.getLogger(__name__)

# --- Bounds (DoS / replay guards) ------------------------------------------------
TOKEN_VERSION = 1
_MAX_TOKEN_BYTES = 32_768          # generous for a capped conversation, hard ceiling
_MAX_ASKED = 64                    # > askable-field count; legitimate clients stay small
_MAX_PASSAGES = 8                  # passages are capped on write; reject tampered bloat
_MAX_LETTERS = 8
_MAX_HISTORY = 64
_MAX_AGE_SECONDS = 24 * 3600       # tokens older than this are rejected

_SECRET_ENV = "BENEFITNAV_TOKEN_SECRET"


class InvalidToken(ValueError):
    """Raised when a token fails signature, size, freshness, or shape checks.

    The caller maps this to HTTP 400 *before* any model call or fact is trusted.
    """


@dataclass(frozen=True)
class ChatState:
    """Immutable per-conversation state. The first three fields mirror the grill's
    state exactly, so the trust core reuses `elicit.*` over them unchanged."""

    facts: dict = field(default_factory=dict)
    presumed: dict = field(default_factory=dict)
    asked: tuple[str, ...] = ()
    retrieval_query_ms: str = ""
    passages: tuple[dict, ...] = ()
    assessment: dict | None = None       # cached PipelineResult (asdict), once ASSESS ran
    plan: dict | None = None             # optimizer UnlockPlan (asdict), once OPTIMIZE ran
    letters: tuple[dict, ...] = ()       # appeal letters drafted this conversation
    history: tuple[dict, ...] = ()       # bounded action trace: [{action, rationale_ms, turn}]
    turn: int = 0
    lang: str = "en"

    def evolve(self, **changes: Any) -> "ChatState":
        """Return a new ChatState with `changes` applied (never mutate in place)."""
        return replace(self, **changes)


# --- Secret management -----------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _fallback_secret() -> bytes:
    """Per-process random secret, generated lazily and cached for the process.

    Only reached when %s is unset. Warns because tokens signed with it are
    invalidated on restart and will NOT verify across separate processes — so the
    MCP server and the FastAPI app must share a stable env secret in any real run.
    """
    logger.warning(
        "%s is not set — using a per-process random secret. State tokens will be "
        "invalidated on restart and will NOT verify across separate processes "
        "(set %s to a stable value so the FastAPI app and the MCP server share it).",
        _SECRET_ENV, _SECRET_ENV,
    )
    return secrets.token_bytes(32)


def _secret() -> bytes:
    """The HMAC key shared by the signer (FastAPI orchestrator) and the verifier (the
    MCP container's grill/assess tools).

    The env var is live-read first (set on the container; flipped by tests) so it is
    never cached stale. When it is unset — the local orchestrator's default — the *same*
    secret the container holds is fetched from its Container Apps secret via the config
    layer, so both sides verify each other's tokens. Only if that fetch is unavailable
    too do we fall back to a per-process random secret: the app still runs, but tokens
    will not verify across processes (the loud warning in _fallback_secret fires)."""
    env = os.environ.get(_SECRET_ENV)
    if env:
        return env.encode("utf-8")
    try:
        from ingest import config
        return config.token_secret().encode("utf-8")
    except Exception as exc:  # noqa: BLE001 — never let secret resolution crash a turn
        logger.debug("token secret unresolved via config (%s); using random fallback",
                     str(exc)[:120])
        return _fallback_secret()


# --- Encode / decode -------------------------------------------------------------

def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: bytes) -> str:
    return _b64u_encode(hmac.new(_secret(), payload, sha256).digest())


def _to_payload(state: ChatState) -> dict:
    return {
        "v": TOKEN_VERSION,
        "iat": int(time.time()),
        "facts": state.facts,
        "presumed": state.presumed,
        "asked": list(state.asked),
        "retrieval_query_ms": state.retrieval_query_ms,
        "passages": list(state.passages),
        "assessment": state.assessment,
        "plan": state.plan,
        "letters": list(state.letters),
        "history": list(state.history),
        "turn": state.turn,
        "lang": state.lang,
    }


def encode(state: ChatState) -> str:
    """Serialize + sign a ChatState into an opaque ``payload.signature`` token."""
    payload = json.dumps(_to_payload(state), separators=(",", ":"),
                         ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"{_b64u_encode(payload)}.{_sign(payload)}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidToken(message)


def _validate_shape(data: dict) -> None:
    """Reject anything whose type or size is off before a single fact is trusted."""
    _require(data.get("v") == TOKEN_VERSION, "unsupported token version")
    _require(isinstance(data.get("iat"), int), "missing issued-at")
    _require(time.time() - data["iat"] <= _MAX_AGE_SECONDS, "token expired")
    _require(isinstance(data.get("facts"), dict), "facts must be an object")
    _require(isinstance(data.get("presumed"), dict), "presumed must be an object")
    _require(isinstance(data.get("asked"), list) and len(data["asked"]) <= _MAX_ASKED,
             "asked malformed or too long")
    _require(all(isinstance(a, str) for a in data["asked"]), "asked must be strings")
    _require(isinstance(data.get("passages"), list)
             and len(data["passages"]) <= _MAX_PASSAGES, "passages malformed or too long")
    _require(isinstance(data.get("letters"), list)
             and len(data["letters"]) <= _MAX_LETTERS, "letters malformed or too long")
    _require(isinstance(data.get("history"), list)
             and len(data["history"]) <= _MAX_HISTORY, "history malformed or too long")
    _require(isinstance(data.get("turn"), int) and data["turn"] >= 0, "turn malformed")
    _require(isinstance(data.get("lang"), str), "lang malformed")
    _require(data.get("assessment") is None or isinstance(data["assessment"], dict),
             "assessment malformed")
    _require(data.get("plan") is None or isinstance(data["plan"], dict), "plan malformed")
    _require(isinstance(data.get("retrieval_query_ms"), str), "retrieval_query_ms malformed")


def decode(token: str) -> ChatState:
    """Verify + parse a token into a ChatState, or raise :class:`InvalidToken`.

    Checks, in order: size ceiling, structural split, HMAC (constant-time), JSON
    parse, then field-by-field shape/size/freshness. Nothing inside the token is
    trusted until every check passes.
    """
    _require(isinstance(token, str) and token, "empty token")
    _require(len(token.encode("utf-8")) <= _MAX_TOKEN_BYTES, "token too large")

    parts = token.split(".")
    _require(len(parts) == 2, "malformed token")
    payload_b64, sig = parts

    try:
        payload = _b64u_decode(payload_b64)
    except (ValueError, TypeError) as exc:   # binascii.Error subclasses ValueError
        raise InvalidToken("undecodable token") from exc

    _require(hmac.compare_digest(_sign(payload), sig), "bad signature")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvalidToken("undecodable payload") from exc
    _require(isinstance(data, dict), "payload not an object")

    _validate_shape(data)

    return ChatState(
        facts=data["facts"],
        presumed=data["presumed"],
        asked=tuple(data["asked"]),
        retrieval_query_ms=data["retrieval_query_ms"],
        passages=tuple(data["passages"]),
        assessment=data["assessment"],
        plan=data["plan"],
        letters=tuple(data["letters"]),
        history=tuple(data["history"]),
        turn=data["turn"],
        lang=data["lang"],
    )
