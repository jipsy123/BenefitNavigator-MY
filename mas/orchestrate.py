"""Per-turn /chat driver — FastAPI conducts; the Foundry agents execute.

This is the seam between the Foundry multi-agent layer and the deterministic trust
core. One call to :func:`run_chat` advances a conversation by one turn:

    decode token → shield → intake → ROUTE (agent) → NARRATE (specialist agent)
    → DUAL GATE → localize → re-sign token

Why FastAPI conducts (Option 1). Same-project Foundry→Foundry A2A delegation is an
open platform bug (the Orchestrator cannot call a specialist over A2A — the agent
card-path validation rejects every form). So instead of one Orchestrator-over-A2A
call, the network hop lives here: the Orchestrator agent still *reasons about
routing* (it returns just an action), and FastAPI invokes the chosen specialist
DIRECTLY via the Responses API — the path proven working in /tmp/probe_agent.py.
This keeps the system genuinely multi-agent on Foundry; only the delegation hop moved
from broken A2A into the conductor.

The agent layer decides *flow* (which action) and produces *language* (the question,
the explanation, the hand-off). The trust spine here decides *truth*:

  - Facts are extracted by intake and sanitised by ``elicit`` — never invented by the
    model. They live inside the HMAC-signed token; an agent can only relay the token,
    not alter the facts inside it (see mas/state.py).
  - Verdicts and amounts are recomputed in-process via ``compute.status.summarise``.
    This is the ground truth handed to the Communicator AND the ground truth the gate
    checks its narrative against — the agent's *relayed* amounts are never trusted.
    (FastAPI computing verdicts in-process IS the assessment role; there is no separate
    Assessor agent — the dual gate must own these values, so the conductor computes them
    here rather than round-tripping a trust-critical number through an LLM. The `assess`/
    `optimize` MCP tools remain as latent trust-core surface, callable but unattached.)
  - The amount guard (``verify.verify_amounts``) runs on EVERY agent message,
    regardless of action, so a fabricated RM can never pass.
  - Groundedness (Azure Content Safety) runs on assessment narratives.
  - Any gate failure ⇒ refuse and route to a human (Talian Kasih 15999).

FAIL-HARD (Foundry-or-fail). The system must genuinely use the Foundry multi-agent layer,
so there is NO local substitute for an agent's job. If the Orchestrator, Interview,
Retrieval, Communicator, or Escalation agent is unavailable (after retries) or returns an
unusable result, the turn FAILS with an `error` action — we never route, ground, phrase, or
narrate locally in its place. Retrieval is on the critical path: verdicts are still COMPUTED
independently of it (`summarise` runs first), but a turn cannot COMPLETE without it.

What is NOT a "local fallback" and therefore stays: the deterministic trust core
(`compute/`, reached by agents as MCP tools), the independent in-process verdict
recompute the gate verifies against, the dual safety gate, and the prompt-injection
shield — no agent does these; they are the spine the agents act through.

Streaming. `run_chat_stream` is the single pipeline: it yields meaningful progress
events (stage checks, which agent is running, the real MCP tool calls it makes, and the
question/narrative forming token-by-token) and ends with a terminal `done`/`error`
event. `run_chat` consumes it and returns the final `ChatTurn`, so the streamed demo
path and the JSON `/chat` path (and the test suite) exercise the SAME code.

All transforms are pure: the inbound ChatState is never mutated; a new one is signed.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace
from typing import Optional, Union

from agent import assumptions, intake, localize, narrate, safety, verify
from agent.orchestrator import _BLOCKED_MS, _REFUSAL_MS
from compute import checker, elicit
from compute.status import Assessment, summarise
from ingest import config

from . import agents, trust_tools
from .state import ChatState, InvalidToken, decode, encode

logger = logging.getLogger(__name__)

# Actions the Orchestrator (router) may choose. `refuse` is produced only by the gate.
ACTION_ASK = "ask"
ACTION_ASSESS = "assess"
ACTION_ESCALATE = "escalate"
ACTION_REFUSE = "refuse"
ACTION_ERROR = "error"          # produced only when a Foundry agent is unreachable (fail-hard)
_AGENT_ACTIONS = {ACTION_ASK, ACTION_ASSESS, ACTION_ESCALATE}

# Agent calls retry a few times before failing — still 100% Foundry (a retry is not a
# local fallback), just insurance against a transient 429 / malformed run. The heaviest
# call (assess → Communicator) is the one most likely to brush the shared TPM ceiling, so
# the window must be wide enough to ride out a brief rate-limit blip yet still surface a
# genuine outage fast: 3 tries with 1.2s·(attempt) backoff ≈ 3.6s worst case before fail.
# We deliberately do NOT honour a full Retry-After (could be 30–60s) — a long hang mid-demo
# is worse UX than a quick fail + retry banner.
_AGENT_ATTEMPTS = 3
_AGENT_BACKOFF_S = 1.2

_MAX_PASSAGES_FOR_NARRATION = 4
_PASSAGE_SNIPPET_CHARS = 600

# Mirrors SKIP_MS in web/app.js — the fixed sentence the Skip chip puts on the wire.
# Keep the two in sync. It means "skip THIS question": flow control, not language, so
# it is handled deterministically before ROUTE (the router used to read it as "give me
# my result now" and produced premature mid-interview assessments).
SKIP_SENTINEL_MS = "Saya tidak pasti tentang soalan itu dan ingin melangkaunya."

@dataclass(frozen=True)
class ChatTurn:
    """The result of one /chat turn — everything the API needs to answer the client."""
    token: str                       # new signed state token (carry back next turn)
    action: str                      # ask | assess | escalate | refuse
    reply: str                       # user-facing message in the display language
    reply_ms: str                    # canonical verified Malay (source of truth)
    rationale_ms: str = ""
    question: Optional[dict] = None  # deterministic FieldNeed serialisation (ask turns)
    progress: Optional[dict] = None  # {total, decided, undecided, asked} (ask turns)
    result: Optional[dict] = None    # localized assessment payload (assess turns)
    canonical_ms: Optional[dict] = None  # verified Malay assessment payload
    citations: list[dict] = field(default_factory=list)
    refused: bool = False
    done: bool = False               # interview complete / nothing left to ask
    turn: int = 0
    lang: str = "en"
    translation_ok: bool = True
    trace: list[dict] = field(default_factory=list)


# --- Foundry agent client + invocation -------------------------------------------

@lru_cache(maxsize=1)
def _project_client():
    """AIProjectClient bound to the Foundry project.

    Uses config.azure_credential() (DefaultAzureCredential) so the SAME code path
    works locally via `az login` AND inside the deployed conductor via the Container
    App's system-assigned managed identity — no code change between environments."""
    from azure.ai.projects import AIProjectClient
    return AIProjectClient(endpoint=config.FOUNDRY_PROJECT_ENDPOINT,
                           credential=config.azure_credential())


def _final_output_text(resp) -> str:
    """Return the LAST message the agent emitted, not `output_text`.

    A tool-using run (e.g. the Communicator's draft → grade → rewrite loop) emits each
    draft as its own message item, and `output_text` concatenates ALL of them — the
    citizen would see the narrative repeated once per rewrite. Only the final message
    is the agent's answer; `output_text` remains the fallback for shapes without
    message items."""
    messages = [item for item in (getattr(resp, "output", None) or [])
                if getattr(item, "type", "") == "message"]
    if messages:
        parts = (getattr(c, "text", "") or ""
                 for c in (getattr(messages[-1], "content", None) or []))
        text = "".join(parts).strip()
        if text:
            return text
    return (getattr(resp, "output_text", "") or "").strip()


class AgentUnavailable(RuntimeError):
    """A hosted Foundry agent could not produce a result (run failed to start, died
    mid-stream, or returned nothing). Under fail-hard there is no local substitute, so
    the turn ends with an `error` action — never a locally-synthesised answer."""


def _open_agent_stream(agent_id: str, prompt: str):
    """Start a streaming Responses run for one hosted Foundry agent, retrying transient
    failures at creation (e.g. a 429 before any token). A retry is still 100% Foundry —
    it is insurance, not a local fallback. Raises AgentUnavailable when out of attempts."""
    client = _project_client().get_openai_client()
    last_exc: Optional[Exception] = None
    for attempt in range(_AGENT_ATTEMPTS):
        try:
            return client.responses.create(
                input=prompt,
                extra_body={"agent_reference": {"type": "agent_reference", "name": agent_id}},
                stream=True,
            )
        except Exception as exc:  # noqa: BLE001 — retry transient, then fail hard
            last_exc = exc
            logger.warning("Agent %s create failed (attempt %d/%d): %s",
                           agent_id, attempt + 1, _AGENT_ATTEMPTS, str(exc)[:160])
            if attempt + 1 < _AGENT_ATTEMPTS:
                time.sleep(_AGENT_BACKOFF_S * (attempt + 1))
    raise AgentUnavailable(f"{agent_id}: {str(last_exc)[:200]}")


def _invoke_agent_stream(
    agent_id: str, prompt: str
) -> Iterator[Union[tuple[str, str], tuple[str, tuple[str, str]]]]:
    """Stream one hosted Foundry agent. Yields, in order:
      ('reset', '')      — a new message item began (the Communicator's rewrite replaces
                           its draft); consumers showing live text should clear it.
      ('tool', name)     — the agent invoked an MCP trust tool (grill_next / grade / …).
      ('tool_result', (name, output))
                         — a hosted MCP tool finished; `output` is its raw JSON return
                           string (deterministic — e.g. retrieve's passages). Emitted from
                           the per-item `done` event and/or the final response, whichever
                           carries a non-empty output.
      ('delta', text)    — a chunk of the agent's answer as it is generated.
      ('final', text)    — the authoritative final answer (last message only, matching
                           `_final_output_text`).
    Raises AgentUnavailable if the run cannot start or dies mid-stream (fail-hard)."""
    stream = _open_agent_stream(agent_id, prompt)
    final_response = None
    msg_buf: list[str] = []
    try:
        for event in stream:
            et = getattr(event, "type", "") or ""
            if et == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    msg_buf.append(delta)
                    yield ("delta", delta)
            elif et == "response.output_item.added":
                item = getattr(event, "item", None)
                itype = getattr(item, "type", "") or ""
                if itype == "message":
                    msg_buf = []                       # keep only the latest message
                    yield ("reset", "")
                elif any(k in itype for k in ("mcp", "tool", "function")):
                    name = getattr(item, "name", "") or ""
                    if name:
                        yield ("tool", name)
            elif et == "response.output_item.done":
                # Primary capture: a hosted MCP tool finished and (per Task 0 outcome 1) its
                # McpCall item carries the deterministic tool OUTPUT — surface it so the
                # conductor uses the function's real result, not the agent's retelling.
                item = getattr(event, "item", None)
                itype = getattr(item, "type", "") or ""
                if any(k in itype for k in ("mcp", "tool", "function")):
                    name = getattr(item, "name", "") or ""
                    output = getattr(item, "output", "") or ""
                    if name and output:
                        yield ("tool_result", (name, output))
            elif et == "response.completed":
                final_response = getattr(event, "response", None)
    except AgentUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — a mid-stream death is a hard failure
        raise AgentUnavailable(f"{agent_id} stream died: {str(exc)[:200]}") from exc
    # Secondary capture: the assembled final response carries every output item with its
    # populated `output`. Re-emit any MCP tool outputs from here so capture is robust even
    # if the per-item `done` event arrived without the output populated. _stream_retrieval
    # only accepts a parseable result, so a duplicate/empty emission is harmless.
    for fitem in (getattr(final_response, "output", None) or []):
        fitype = getattr(fitem, "type", "") or ""
        if any(k in fitype for k in ("mcp", "tool", "function")):
            fname = getattr(fitem, "name", "") or ""
            foutput = getattr(fitem, "output", "") or ""
            if fname and foutput:
                yield ("tool_result", (fname, foutput))
    final = (_final_output_text(final_response) if final_response is not None
             else "".join(msg_buf).strip())
    yield ("final", final)


def _invoke_agent(agent_id: str, prompt: str) -> str:
    """Consume an agent stream and return only its final text — for callers that do not
    surface tokens (the router and the escalation hand-off). Raises AgentUnavailable."""
    final = ""
    for kind, payload in _invoke_agent_stream(agent_id, prompt):
        if kind == "final":
            final = payload
    return final


def _parse_contract(text: str) -> dict:
    """Extract a JSON object from an agent's output, tolerating prose wrappers."""
    text = (text or "").strip()
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_proofs(output: str) -> Optional[list[dict]]:
    """Parse the `prove` tool's JSON output into a proofs list. Returns None when the tool
    produced nothing usable (blank/unparseable, an `error` key, or wrong shape) so the
    caller fails hard; returns the list (possibly with empty passages) when it genuinely ran."""
    if not output:
        return None
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("proofs"), list):
        return None
    return data["proofs"]


# --- ROUTE: the Orchestrator agent decides the action ----------------------------

_ROUTER_INSTRUCTION = (
    'Reply with ONE JSON object and nothing else: '
    '{"action": "ask|assess|escalate", "rationale_ms": "<one short Bahasa Melayu line, '
    'display only — never an amount or a verdict>"}. '
    'Use "ask" when the deterministic signal says a decision-relevant question remains '
    'and the citizen has not asked to skip; "assess" when the interview is complete OR '
    'the citizen wants their result now; "escalate" when the request is out of scope, '
    'distressing, unsafe, or cannot be handled by a benefits assessment.'
)


def _route(message: str, situation_ms: str) -> dict:
    """Invoke the Orchestrator agent (tool-less router) for a routing decision. Raises
    AgentUnavailable if the agent is unreachable (fail-hard — no deterministic default);
    returns {} only when the agent replied with unparseable text (also a hard failure,
    handled by the caller)."""
    prompt = (f"SITUATION (deterministic, already trust-checked):\n{situation_ms}\n\n"
              f"CITIZEN MESSAGE:\n{message}\n\n{_ROUTER_INSTRUCTION}")
    return _parse_contract(_invoke_agent(agents.ORCHESTRATOR.id, prompt))


# --- NARRATE: the matching specialist produces the user-facing text --------------

def _interview_prompt(token: str, message: str, situation_ms: str) -> str:
    return (
        f"STATE_TOKEN (pass this verbatim to the grill_next tool):\n{token}\n\n"
        f"DETERMINISTIC SIGNAL:\n{situation_ms}\n\n"
        f"CITIZEN MESSAGE:\n{message}\n\n"
        "Call grill_next(state_token) with the token above, then phrase the next "
        "question warmly and simply in Bahasa Melayu, briefly noting which benefit it "
        "could unlock. Output ONLY the question text — no JSON, no preamble.")


def _communicator_prompt(message: str, verdict_block_ms: str,
                         passages: list[dict]) -> str:
    src = "\n\n".join(
        (p.get("passage", "") or "")[:_PASSAGE_SNIPPET_CHARS]
        for p in passages[:_MAX_PASSAGES_FOR_NARRATION]) or "(tiada petikan tambahan)"
    return (
        "VERDIK MUKTAMAD (sumber kebenaran — guna HANYA angka RM di sini):\n"
        f"{verdict_block_ms}\n\n"
        "PETIKAN SUMBER RASMI (.gov.my; untuk konteks — jangan cipta angka baharu):\n"
        f"{src}\n\n"
        f"MESEJ RAKYAT:\n{message}\n\n"
        "Terangkan keputusan ini kepada rakyat dengan mesra dan ringkas dalam Bahasa "
        "Melayu mudah. Setiap jumlah RM mesti muncul verbatim dalam VERDIK di atas — "
        "jangan sekali-kali memperkenalkan angka baharu. Panggil grade(text) untuk "
        "menyemak kebolehbacaan; jika belum cukup mudah, tulis semula lebih ringkas dan "
        "semak lagi. Tulis perenggan biasa sahaja — JANGAN guna markdown (tiada **, ##, "
        "---, atau senarai bernombor) dan JANGAN guna emoji. JANGAN sebut talian "
        "bantuan atau nombor telefon (contoh: Talian Kasih 15999) dan JANGAN alihkan "
        "rakyat ke saluran lain sebagai ganti penjelasan — kad keputusan yang disahkan "
        "sudah membawa panduan itu; jika input mengelirukan, terangkan sahaja apa yang "
        "tertulis dalam VERDIK. (Panduan tempat memohon seperti pejabat JKM atau portal "
        "MyHASiL dibenarkan.) Output HANYA teks penjelasan akhir — tiada JSON.")


def _escalation_prompt(message: str, reason_ms: str) -> str:
    return (
        f"SEBAB:\n{reason_ms or 'Permintaan di luar skop atau memerlukan bantuan manusia.'}\n\n"
        f"MESEJ RAKYAT:\n{message}\n\n"
        "Hasilkan mesej ringkas dan mesra dalam Bahasa Melayu yang mengarahkan rakyat "
        "kepada Talian Kasih 15999 dan pejabat JKM/LHDN daerah mereka. Jangan tinggalkan "
        "jalan buntu — beri kenalan seterusnya yang konkrit. Output HANYA teks mesej.")


def _proof_prompt(token: str) -> str:
    return (
        f"STATE_TOKEN (pass this verbatim to the prove tool):\n{token}\n\n"
        "Call prove(state_token) with the token above EXACTLY once, then return the official "
        "passages it gives you, unchanged. Do not add commentary and do not decide eligibility.")


# --- deterministic situation summary (the router's guidance, not its decision) ----

def _situation_ms(known: dict, need: Optional[elicit.FieldNeed],
                  has_assessment: bool) -> str:
    """A compact Malay status line so the router can decide well. Deterministic: it
    reports the interview signal and what is known — it does not pre-decide."""
    if need is not None:
        prog = ", ".join(p.name_ms for p in need.programs) or "bantuan berkaitan"
        return (f"Temu bual BELUM selesai. Medan paling penting seterusnya: "
                f"'{need.field}' (boleh membuka: {prog}). "
                f"Progres: {elicit.progress(known, ())['decided']} fakta diketahui.")
    state = "sudah dikira" if has_assessment else "belum dikira"
    return (f"Temu bual SELESAI — tiada soalan kritikal berbaki. Penilaian {state}. "
            f"Wajar terus menilai (assess) melainkan rakyat meminta sesuatu yang lain.")


# --- the dual safety gate (non-bypassable; identical guarantees to agent/_finish) -

def _gate(narrative_ms: str, applicant, assessment: Assessment,
          passages: list[dict]) -> tuple[bool, dict]:
    """Run the dual guard on an agent narrative. Returns (ok, groundedness_dict).

    Hard guard (always): every RM amount must trace to a verdict / stated income /
    gazetted threshold. Soft guard: Content Safety groundedness against the verdicts
    and cited passages. ok is False if either trips."""
    thresholds = checker.load_thresholds()
    # The guaranteed monthly floor is a legitimate deterministic figure (the sum of the
    # eligible amounts), so the Communicator may state it; everything else must trace to
    # a verdict amount / stated income / gazetted threshold or it is fabricated.
    allowed = verify.allowed_amounts(applicant, thresholds,
                                     extra=(assessment.total_monthly_min,))
    amounts_ok, fabricated = verify.verify_amounts(narrative_ms, allowed)

    # Ground against the SAME deterministic facts the Communicator narrates from, plus the
    # whitelisted procedural facts (how/where to apply) and the cited passages — mirroring
    # the proven agent/orchestrator._finish gate. Dropping PROCEDURAL_FACTS_MS here (and
    # feeding a stringified amount dict instead of the clean facts block) made legitimate
    # "register at the JKM office / call Talian Kasih" guidance read as ungrounded and
    # falsely refuse a correct answer.
    grounding = ([narrate.build_facts_text(assessment), narrate.PROCEDURAL_FACTS_MS]
                 + [p.get("passage", "") for p in passages])
    g = safety.detect_groundedness(narrative_ms, grounding)

    ok = amounts_ok and not (g.available and not g.grounded)
    return ok, {"available": g.available, "grounded": g.grounded,
                "ungrounded_percentage": g.ungrounded_percentage,
                "amounts_ok": amounts_ok, "fabricated_amounts": fabricated}


def _amount_only_gate(text_ms: str, applicant) -> tuple[bool, list[int]]:
    """The hard amount guard alone (no groundedness) — applied to questions and
    hand-offs, where a full groundedness check would be wasteful but a fabricated RM
    must still never slip through."""
    thresholds = checker.load_thresholds()
    return verify.verify_amounts(text_ms, verify.allowed_amounts(applicant, thresholds))


# --- payload builders ------------------------------------------------------------

def _assessment_payload(narrative_ms: str, applicant, assessment: Assessment,
                        known: dict, proofs: list[dict]) -> dict:
    """Build the Malay assessment payload (the shape localize_assess expects), with each
    verdict citation enriched by its proving passage — on the per-programme cards AND the
    deduped top-level citations list. localize never translates citations, so passages stay
    verbatim Malay."""
    pmap = {(p.get("doc_name"), p.get("locator")): p.get("passage", "")
            for p in (proofs or [])}

    def _enrich(citation: dict) -> dict:
        return {**citation, "passage": pmap.get(
            (citation.get("doc_name"), citation.get("locator")), "")}

    eligible = []
    for r in assessment.eligible:
        d = checker.to_dict(r)
        d["citation"] = _enrich(d["citation"])
        eligible.append(d)
    gaps = []
    for g in assessment.gaps:
        d = trust_tools._gap_dict(g)
        d["citation"] = _enrich(d["citation"])
        gaps.append(d)

    # Same (doc_name, locator) dedup as trust_tools.proof_citations / _verdict_citations — keep aligned.
    seen: set[tuple] = set()
    citations: list[dict] = []
    for r in list(assessment.eligible) + list(assessment.gaps):
        c = r.citation
        key = (c.get("doc_name"), c.get("locator"))
        if key in seen or not c.get("source_url"):
            continue
        seen.add(key)
        citations.append({"doc_title": c.get("doc_title"), "locator": c.get("locator"),
                          "source_url": c.get("source_url"),
                          "passage": pmap.get(key, "")})

    return {
        "message_ms": narrative_ms,
        "assumptions_ms": list(assumptions.unspecified_ms(known)),
        "eligible": eligible,
        "gaps": gaps,
        "total_monthly_min": assessment.total_monthly_min,
        "citations": citations,
        "stages": [],
    }


# --- localisation helper ---------------------------------------------------------

def _localize_text(text_ms: str, lang: str) -> tuple[str, bool]:
    """Localize a single Malay string by reusing the verified assess localizer
    (so the same RM-amount safety check applies)."""
    if lang == "ms":
        return text_ms, True
    display, ok = localize.localize_assess({"message_ms": text_ms}, lang)
    return (display.get("message_ms", text_ms) if ok else text_ms), ok


def _refusal_turn(state: ChatState, lang: str, message_ms: str, *, action: str,
                  trace: list[dict], rationale_ms: str = "") -> ChatTurn:
    reply, ok = _localize_text(message_ms, lang)
    token = encode(state.evolve(turn=state.turn + 1, lang=lang))
    return ChatTurn(token=token, action=action, reply=reply, reply_ms=message_ms,
                    rationale_ms=rationale_ms, refused=(action == ACTION_REFUSE),
                    turn=state.turn + 1, lang=lang, translation_ok=ok, trace=trace)


# --- fail-hard helpers -----------------------------------------------------------

def _error_turn(state: ChatState, lang: str, trace: list[dict]) -> ChatTurn:
    """A fail-hard terminal turn: a Foundry agent was unreachable and there is NO local
    substitute. It carries no benefit answer — `reply`/`reply_ms` are empty and the client
    renders a localized 'service unavailable, please retry' notice from its own i18n (so we
    don't lean on the Translator during an outage). This is a failure, not a degraded answer."""
    token = encode(state.evolve(turn=state.turn + 1, lang=lang))
    return ChatTurn(token=token, action=ACTION_ERROR, reply="", reply_ms="",
                    turn=state.turn + 1, lang=lang, translation_ok=True, trace=trace)


def _fail(state: ChatState, lang: str, trace: list[dict], stage: str,
          exc: Exception) -> Iterator[dict]:
    """Record the stage failure and yield a terminal `error` event (Foundry-or-fail)."""
    logger.warning("Fail-hard at %s: %s", stage, str(exc)[:200])
    trace.append({"stage": stage, "status": "error", "detail": str(exc)[:120]})
    yield {"type": "error", "stage": stage, "detail": str(exc)[:200],
           "turn": _error_turn(state, lang, trace)}


def _stream_agent_text(agent_id: str, prompt: str, scope: str, *,
                       reveal: bool = True) -> Iterator[dict]:
    """Stream a specialist's run, translating the low-level invoker events into wire
    events scoped to `scope` (question / narrative / escalation). Returns the final text
    via generator return (capture with `final = yield from _stream_agent_text(...)`).
    AgentUnavailable propagates to the caller (fail-hard).

    reveal=False (the assessment narrative): forward only the agent's *activity* (its MCP
    trust-tool calls), NOT its raw text. The narrative must clear the dual safety gate
    BEFORE the user sees a single token — otherwise a fabricated RM would flash on screen
    a beat before the gate flips it to a refusal. So the verified text is revealed only in
    the terminal `done` event, after `_gate`. The question (amount-guard only, ~never
    quotes RM) keeps reveal=True so the citizen watches it form."""
    final = ""
    for kind, payload in _invoke_agent_stream(agent_id, prompt):
        if kind == "delta":
            if reveal:
                yield {"type": "delta", "scope": scope, "text": payload}
        elif kind == "reset":
            if reveal:
                yield {"type": "reset", "scope": scope}
        elif kind == "tool":
            yield {"type": "tool", "agent": scope, "tool": payload}
            # 'tool_result' is intentionally not forwarded here — only _stream_retrieval consumes it.
        elif kind == "final":
            final = payload
    return final


def _stream_retrieval(agent_id: str, prompt: str) -> Iterator[dict]:
    """Stream the Retrieval agent: surface its `prove` tool call as a wire event and
    capture the tool's DETERMINISTIC output (proofs from the KB — never the agent's prose).
    Returns the proofs list via generator return, or None if the agent produced no usable
    prove result (the caller then fails hard). AgentUnavailable propagates (fail-hard)."""
    passages: Optional[list[dict]] = None
    for kind, payload in _invoke_agent_stream(agent_id, prompt):
        if kind == "tool":
            yield {"type": "tool", "agent": "retrieval", "tool": payload}
        elif kind == "tool_result":
            name, output = payload
            if name == "prove":
                parsed = _parse_proofs(output)
                if parsed is not None:        # never let an empty/dup emission clobber a hit
                    passages = parsed
    return passages


# --- the turn ---------------------------------------------------------------------

def run_chat_stream(message: str, token: Optional[str] = None,
                    lang: str = "en") -> Iterator[dict]:
    """Advance the conversation by one turn, yielding meaningful progress events and a
    terminal `done` (verified turn) or `error` (Foundry unreachable) event. This is the
    single pipeline; `run_chat` consumes it for the JSON `/chat` path and the tests.

    Event shapes (all JSON-serialisable except the terminal `turn`, a ChatTurn the caller
    serialises): `stage`, `agent` (start/done), `tool`, `reset`, `delta`, `done`, `error`."""
    trace: list[dict] = []

    # 0) State -----------------------------------------------------------------
    try:
        state = decode(token) if token else ChatState(lang=lang)
    except InvalidToken as exc:
        raise ValueError(f"invalid state token: {exc}") from exc

    # 1) Prompt shield on untrusted free text (no agent does this — it stays) ---
    shield = safety.shield_prompt(message)
    if shield.available and shield.attack_detected:
        trace.append({"stage": "SHIELD", "status": "blocked"})
        yield {"type": "stage", "stage": "SHIELD", "status": "blocked"}
        yield {"type": "done",
               "turn": _refusal_turn(state, lang, _BLOCKED_MS, action=ACTION_ESCALATE,
                                     trace=trace)}
        return
    s_status = "ok" if shield.available else "unavailable"
    trace.append({"stage": "SHIELD", "status": s_status})
    yield {"type": "stage", "stage": "SHIELD", "status": s_status}

    # 2) Intake — KEPT (no agent produces the signed, trusted fact set; FastAPI must
    #    extract → sanitize → sign into the HMAC token). It degrades to "no new facts"
    #    on a transient error rather than failing the turn — the verdicts never need it.
    try:
        extracted = intake.run_intake(message)
        intake_ok = True
    except Exception as exc:  # noqa: BLE001 — degrade gracefully; never 500 the turn
        logger.warning("Intake failed (%s); proceeding with no extracted facts", str(exc)[:160])
        extracted = SimpleNamespace(facts={}, presumed={}, retrieval_query_ms=message)
        intake_ok = False
    facts = elicit.sanitize_facts({**state.facts, **extracted.facts})
    presumed = elicit.sanitize_presumptions({**state.presumed, **extracted.presumed}, facts)
    asked = list(state.asked)
    known = elicit.with_presumed(facts, presumed)
    need = elicit.next_field(known, asked)
    interview_done = need is None
    answered = elicit.progress(known, asked)
    i_status = "ok" if intake_ok else "degraded"
    trace.append({"stage": "INTAKE", "status": i_status, "answered": answered})
    yield {"type": "stage", "stage": "INTAKE", "status": i_status, "answered": answered}

    base_state = state.evolve(facts=facts, presumed=presumed, asked=tuple(asked),
                              retrieval_query_ms=extracted.retrieval_query_ms, lang=lang)
    agent_token = encode(base_state)
    situation = _situation_ms(known, need, state.assessment is not None)
    applicant = elicit.to_applicant(known)

    # 3) ROUTE — the Orchestrator agent decides the action. No deterministic default:
    #    if it is unreachable or returns no valid action, the turn FAILS (fail-hard).
    #    The skip sentinel is flow control (no agent does it) and is handled here.
    if message.strip() == SKIP_SENTINEL_MS and not interview_done:
        action, rationale_ms = ACTION_ASK, ""
        trace.append({"stage": "ROUTE", "status": "skip", "action": action})
        yield {"type": "stage", "stage": "ROUTE", "status": "skip", "action": action}
    else:
        yield {"type": "agent", "agent": "orchestrator", "phase": "start"}
        try:
            routing = _route(message, situation)
        except AgentUnavailable as exc:
            yield from _fail(base_state, lang, trace, "ROUTE", exc)
            return
        action = routing.get("action")
        if action not in _AGENT_ACTIONS:
            yield from _fail(base_state, lang, trace, "ROUTE",
                             AgentUnavailable("orchestrator returned no valid action"))
            return
        rationale_ms = (routing.get("rationale_ms") or "").strip()
        trace.append({"stage": "ROUTE", "status": "ok", "action": action})
        yield {"type": "agent", "agent": "orchestrator", "phase": "done",
               "action": action, "rationale_ms": rationale_ms}

    # 4) NARRATE — stream the matching specialist, then gate -------------------
    if action == ACTION_ESCALATE:
        yield {"type": "agent", "agent": "escalation", "phase": "start"}
        try:
            message_ms = yield from _stream_agent_text(
                agents.ESCALATION.id, _escalation_prompt(message, rationale_ms), "escalation")
        except AgentUnavailable as exc:
            yield from _fail(base_state, lang, trace, "ESCALATE", exc)
            return
        if not message_ms:
            yield from _fail(base_state, lang, trace, "ESCALATE",
                             AgentUnavailable("escalation agent returned nothing"))
            return
        ok, _ = _amount_only_gate(message_ms, applicant)
        if not ok:                                # fabricated RM in a hand-off → safety refusal
            yield {"type": "done",
                   "turn": _refusal_turn(base_state, lang, _REFUSAL_MS, action=ACTION_REFUSE,
                                         trace=trace, rationale_ms=rationale_ms)}
            return
        yield {"type": "done",
               "turn": _refusal_turn(base_state, lang, message_ms, action=ACTION_ESCALATE,
                                     trace=trace, rationale_ms=rationale_ms)}
        return

    if action == ACTION_ASK:
        if need is None:                          # nothing left to ask → assess instead
            action = ACTION_ASSESS
        else:
            yield {"type": "agent", "agent": "interview", "phase": "start"}
            try:
                message_ms = yield from _stream_agent_text(
                    agents.INTERVIEW.id,
                    _interview_prompt(agent_token, message, situation), "question")
            except AgentUnavailable as exc:
                yield from _fail(base_state, lang, trace, "INTERVIEW", exc)
                return
            if not message_ms:
                yield from _fail(base_state, lang, trace, "INTERVIEW",
                                 AgentUnavailable("interview agent returned nothing"))
                return
            ok, _ = _amount_only_gate(message_ms, applicant)
            if not ok:                            # a question that fabricated an RM → refuse
                trace.append({"stage": "GATE", "status": "refused", "scope": "question"})
                yield {"type": "done",
                       "turn": _refusal_turn(base_state, lang, _REFUSAL_MS,
                                             action=ACTION_REFUSE, trace=trace,
                                             rationale_ms=rationale_ms)}
                return
            new_asked = asked + [need.field] if need.field not in asked else asked
            new_state = base_state.evolve(asked=tuple(new_asked), turn=state.turn + 1)
            reply, tok_ok = _localize_text(message_ms, lang)
            yield {"type": "done", "turn": ChatTurn(
                token=encode(new_state), action=ACTION_ASK, reply=reply,
                reply_ms=message_ms, rationale_ms=rationale_ms,
                question=trust_tools._need_dict(need),
                progress=elicit.progress(known, tuple(new_asked)), done=False,
                turn=state.turn + 1, lang=lang, translation_ok=tok_ok, trace=trace)}
            return

    # action == ASSESS — verdicts are computed in-process (ground truth), then the Retrieval
    # agent grounds the narrative and the Communicator narrates. summarise() runs FIRST and
    # never changes based on retrieval; but per the Foundry-or-fail decision, retrieval is a
    # real agent on the critical path — there is NO in-process shadow, so if it cannot produce
    # passages the turn fails hard (action="error") rather than degrading.
    assessment = summarise(applicant)

    yield {"type": "agent", "agent": "retrieval", "phase": "start"}
    try:
        passages = yield from _stream_retrieval(
            agents.RETRIEVAL.id, _proof_prompt(agent_token))
    except AgentUnavailable as exc:
        yield from _fail(base_state, lang, trace, "RETRIEVE", exc)
        return
    if passages is None:
        yield from _fail(base_state, lang, trace, "RETRIEVE",
                         AgentUnavailable("retrieval agent produced no usable passages"))
        return
    # Fail-hard: every verdict citation must be proven. Compare the deduped citation keys
    # the verdicts carry against the keys actually proven (non-empty passage). Checking only
    # the returned list's emptiness is insufficient — it can't detect an under-covered set.
    # (No verdicts at all → both sets empty → proceeds, which is correct: nothing to prove.)
    verdict_keys = {
        (r.citation.get("doc_name"), r.citation.get("locator"))
        for r in list(assessment.eligible) + list(assessment.gaps)
        if r.citation.get("source_url")
    }
    proven_keys = {(p.get("doc_name"), p.get("locator")) for p in passages if p.get("passage")}
    if verdict_keys - proven_keys:
        yield from _fail(base_state, lang, trace, "RETRIEVE",
                         AgentUnavailable("a verdict citation could not be proven"))
        return
    trace.append({"stage": "RETRIEVE", "status": "ok", "passages": len(passages)})
    yield {"type": "stage", "stage": "RETRIEVE", "status": "ok", "passages": len(passages)}

    facts_text = narrate.build_facts_text(assessment)
    yield {"type": "agent", "agent": "communicator", "phase": "start"}
    try:
        # reveal=False: the narrative is gated (amount + groundedness) BEFORE the user sees
        # any of it. We stream the Communicator's activity (its grade tool calls), then
        # reveal the verified text only in the terminal `done` — never an ungated token.
        narrative_ms = yield from _stream_agent_text(
            agents.COMMUNICATOR.id, _communicator_prompt(message, facts_text, passages),
            "narrative", reveal=False)
    except AgentUnavailable as exc:
        yield from _fail(base_state, lang, trace, "COMMUNICATOR", exc)
        return
    if not narrative_ms:                          # no local degraded narrative — fail-hard
        yield from _fail(base_state, lang, trace, "COMMUNICATOR",
                         AgentUnavailable("communicator agent returned nothing"))
        return

    # Full dual gate (amount + groundedness). A present narrative that fails (fabricated
    # amount / ungrounded) is a real trust violation → refuse and route to a human.
    gate_ok, groundedness = _gate(narrative_ms, applicant, assessment, passages)
    trace.append({"stage": "GATE", "status": "ok" if gate_ok else "refused",
                  "degraded": False, **groundedness})
    yield {"type": "stage", "stage": "GATE", "status": "ok" if gate_ok else "refused",
           **groundedness}
    if not gate_ok:
        yield {"type": "done",
               "turn": _refusal_turn(base_state, lang, _REFUSAL_MS, action=ACTION_REFUSE,
                                     trace=trace)}
        return

    # 5) Verified → localize + persist -----------------------------------------
    canonical = _assessment_payload(narrative_ms, applicant, assessment, known, passages)
    result, tok_ok = localize.localize_assess(canonical, lang)
    new_state = base_state.evolve(
        assessment={"total_monthly_min": assessment.total_monthly_min},
        turn=state.turn + 1)
    yield {"type": "done", "turn": ChatTurn(
        token=encode(new_state), action=ACTION_ASSESS,
        reply=result.get("message_ms", narrative_ms), reply_ms=narrative_ms,
        rationale_ms=rationale_ms, result=result, canonical_ms=canonical,
        citations=canonical["citations"], done=interview_done, turn=state.turn + 1,
        lang=lang, translation_ok=tok_ok, trace=trace)}


def run_chat(message: str, token: Optional[str] = None, lang: str = "en") -> ChatTurn:
    """Advance the conversation by one turn (non-streaming JSON path). Consumes
    `run_chat_stream` and returns its terminal ChatTurn, so the streamed `/chat/stream`
    demo path and this `/chat` path run the IDENTICAL pipeline (and the test suite pins
    the same code the demo uses)."""
    turn: Optional[ChatTurn] = None
    for ev in run_chat_stream(message, token, lang):
        if ev.get("type") in ("done", "error"):
            turn = ev["turn"]
    if turn is None:  # defensive — a stream must always end with a terminal event
        raise AgentUnavailable("conversation turn produced no result")
    return turn
