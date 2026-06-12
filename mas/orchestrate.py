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
    (FastAPI computing verdicts in-process IS the Assessor role under Option 1; the
    hosted Assessor/Retrieval agents and their MCP tools stay live and are exercised by
    the probe — but the gate must own these values, so the conductor fetches them here
    rather than round-tripping trust inputs through the LLM.)
  - The amount guard (``verify.verify_amounts``) runs on EVERY agent message,
    regardless of action, so a fabricated RM can never pass.
  - Groundedness (Azure Content Safety) runs on assessment narratives.
  - Any gate failure ⇒ refuse and route to a human (Talian Kasih 15999).

If an agent call or its routing JSON fails, we fall back to a deterministic action and
a template question/refusal so the turn still completes safely (fail-closed).

All transforms are pure: the inbound ChatState is never mutated; a new one is signed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace
from typing import Optional

from agent import assumptions, intake, localize, narrate, safety, verify
from agent.orchestrator import _BLOCKED_MS, _REFUSAL_MS
from compute import checker, elicit
from compute.status import Assessment, summarise
from ingest import config
from ingest import knowledge_base as kb

from . import agents, trust_tools
from .state import ChatState, InvalidToken, decode, encode

logger = logging.getLogger(__name__)

# Actions the Orchestrator (router) may choose. `refuse` is produced only by the gate.
ACTION_ASK = "ask"
ACTION_ASSESS = "assess"
ACTION_ESCALATE = "escalate"
ACTION_REFUSE = "refuse"
_AGENT_ACTIONS = {ACTION_ASK, ACTION_ASSESS, ACTION_ESCALATE}

_MAX_PASSAGES_FOR_NARRATION = 4
_PASSAGE_SNIPPET_CHARS = 600

# Mirrors SKIP_MS in web/app.js — the fixed sentence the Skip chip puts on the wire.
# Keep the two in sync. It means "skip THIS question": flow control, not language, so
# it is handled deterministically before ROUTE (the router used to read it as "give me
# my result now" and produced premature mid-interview assessments).
SKIP_SENTINEL_MS = "Saya tidak pasti tentang soalan itu dan ingin melangkaunya."

# Shown as the assessment's lead paragraph when the Communicator agent is unavailable
# (e.g. a transient 429). It is a fixed, claim-free framing line — it states NO amount and
# NO verdict, so it cannot fabricate; the verified eligible/near-miss CARDS (straight from
# compute/) carry every figure and citation. Because it is deterministic prose with nothing
# to ground, only the amount guard applies (it has no RM, so it passes by construction).
_DEGRADED_NARRATIVE_MS = (
    "Berikut keputusan kelayakan anda berdasarkan maklumat yang anda berikan, disemak "
    "dengan sumber rasmi kerajaan. Bantuan yang anda layak, bantuan yang hampir layak, "
    "dan langkah seterusnya disenaraikan di bawah — setiap satu dengan sumbernya."
)


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


def _invoke_agent(agent_id: str, prompt: str) -> str:
    """Invoke one hosted Foundry agent by name via the Responses API and return its
    final text. This is the validated direct-invocation path (probe_agent.py): the
    agent runs its instructions and calls its MCP tools on the live container."""
    client = _project_client().get_openai_client()
    resp = client.responses.create(
        input=prompt,
        extra_body={"agent_reference": {"type": "agent_reference", "name": agent_id}},
    )
    return _final_output_text(resp)


def _safe_invoke_agent(agent_id: str, prompt: str) -> str:
    """Invoke an agent, returning "" on any error so the caller can fall back
    deterministically (fail-closed: a dead agent never produces an unsafe answer)."""
    try:
        return _invoke_agent(agent_id, prompt)
    except Exception as exc:  # noqa: BLE001 — degrade to a deterministic fallback
        logger.warning("Agent %s invocation failed: %s", agent_id, str(exc)[:200])
        return ""


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
    """Invoke the Orchestrator agent (tool-less router) for a routing decision. Returns
    {} on any failure so the caller falls back to the deterministic action."""
    prompt = (f"SITUATION (deterministic, already trust-checked):\n{situation_ms}\n\n"
              f"CITIZEN MESSAGE:\n{message}\n\n{_ROUTER_INSTRUCTION}")
    return _parse_contract(_safe_invoke_agent(agents.ORCHESTRATOR.id, prompt))


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
        (p.get("content", "") or "")[:_PASSAGE_SNIPPET_CHARS]
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
                 + [p.get("content", "") for p in passages])
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

def _verdict_citations(assessment: Assessment) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in list(assessment.eligible) + list(assessment.gaps):
        c = r.citation
        key = (c.get("doc_name"), c.get("locator"))
        if key not in seen and c.get("source_url"):
            seen.add(key)
            out.append({"doc_title": c.get("doc_title"), "locator": c.get("locator"),
                        "source_url": c.get("source_url")})
    return out


def _assessment_payload(narrative_ms: str, applicant, assessment: Assessment,
                        known: dict) -> dict:
    """Build the Malay assessment payload in the SAME shape localize_assess expects
    (mirrors agent.orchestrator.PipelineResult fields used by the UI)."""
    assumption_trail = assumptions.unspecified_ms(known)
    return {
        "message_ms": narrative_ms,
        "assumptions_ms": list(assumption_trail),
        "eligible": [checker.to_dict(r) for r in assessment.eligible],
        "gaps": [trust_tools._gap_dict(g) for g in assessment.gaps],
        "total_monthly_min": assessment.total_monthly_min,
        "citations": _verdict_citations(assessment),
        "stages": [],
    }


def _template_question_ms(need: elicit.FieldNeed) -> str:
    """Deterministic Malay fallback when the agent's phrasing is unusable. The client
    renders the real, field-specific question from the returned `question` dict (it has
    per-field i18n templates), so this conversational line stays generic — it never
    exposes a raw internal field id."""
    return "Boleh kongsi sedikit maklumat lagi supaya kami boleh menyemak kelayakan anda?"


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


# --- the turn ---------------------------------------------------------------------

def run_chat(message: str, token: Optional[str] = None, lang: str = "en") -> ChatTurn:
    """Advance the conversation by one turn. `token` is the signed state from the
    previous turn (None on the first turn)."""
    trace: list[dict] = []

    # 0) State -----------------------------------------------------------------
    try:
        state = decode(token) if token else ChatState(lang=lang)
    except InvalidToken as exc:
        raise ValueError(f"invalid state token: {exc}") from exc

    # 1) Prompt shield on untrusted free text ----------------------------------
    shield = safety.shield_prompt(message)
    if shield.available and shield.attack_detected:
        trace.append({"stage": "SHIELD", "status": "blocked"})
        return _refusal_turn(state, lang, _BLOCKED_MS, action=ACTION_ESCALATE, trace=trace)
    trace.append({"stage": "SHIELD", "status": "ok" if shield.available else "unavailable"})

    # 2) Intake — extract stated facts; never invent. Resilient: a transient model error
    #    (e.g. 429) degrades to "no new facts this turn" rather than failing the whole turn.
    #    `asked` still advances and the deterministic verdicts never depend on intake.
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
    trace.append({"stage": "INTAKE", "status": "ok" if intake_ok else "degraded",
                  "answered": elicit.progress(known, asked)})

    base_state = state.evolve(facts=facts, presumed=presumed, asked=tuple(asked),
                              retrieval_query_ms=extracted.retrieval_query_ms, lang=lang)
    agent_token = encode(base_state)
    situation = _situation_ms(known, need, state.assessment is not None)
    applicant = elicit.to_applicant(known)

    # 3) ROUTE — the Orchestrator agent decides the action (deterministic fallback).
    #    The skip sentinel never reaches the router: skipping one question is flow
    #    control, and the asked-fields spine already advances past it (next_field
    #    excludes asked fields), so the only correct action is to ask the next one.
    if message.strip() == SKIP_SENTINEL_MS and not interview_done:
        action, rationale_ms = ACTION_ASK, ""
        trace.append({"stage": "ROUTE", "status": "skip", "action": action})
    else:
        routing = _route(message, situation)
        action = routing.get("action")
        if action not in _AGENT_ACTIONS:
            action = ACTION_ASSESS if interview_done else ACTION_ASK
        rationale_ms = (routing.get("rationale_ms") or "").strip()
        trace.append({"stage": "ROUTE", "status": "ok" if routing else "fallback",
                      "action": action})

    # 4) NARRATE — invoke the matching specialist directly, then gate ----------
    if action == ACTION_ESCALATE:
        message_ms = _safe_invoke_agent(
            agents.ESCALATION.id, _escalation_prompt(message, rationale_ms))
        ok, _ = _amount_only_gate(message_ms, applicant) if message_ms else (False, [])
        if not message_ms or not ok:
            message_ms = _REFUSAL_MS
        return _refusal_turn(base_state, lang, message_ms, action=ACTION_ESCALATE,
                             trace=trace, rationale_ms=rationale_ms)

    if action == ACTION_ASK:
        if need is None:                          # nothing left to ask → assess instead
            action = ACTION_ASSESS
        else:
            message_ms = _safe_invoke_agent(
                agents.INTERVIEW.id, _interview_prompt(agent_token, message, situation))
            ok, _ = _amount_only_gate(message_ms, applicant) if message_ms else (False, [])
            if not message_ms or not ok:
                message_ms = _template_question_ms(need)
            new_asked = asked + [need.field] if need.field not in asked else asked
            new_state = base_state.evolve(asked=tuple(new_asked), turn=state.turn + 1)
            reply, tok_ok = _localize_text(message_ms, lang)
            return ChatTurn(
                token=encode(new_state), action=ACTION_ASK, reply=reply,
                reply_ms=message_ms, rationale_ms=rationale_ms,
                question=trust_tools._need_dict(need),
                progress=elicit.progress(known, tuple(new_asked)), done=False,
                turn=state.turn + 1, lang=lang, translation_ok=tok_ok, trace=trace)

    # action == ASSESS — verdicts in-process (ground truth), Communicator narrates
    assessment = summarise(applicant)
    passages: list[dict] = []
    try:
        passages = kb.retrieve_passages(extracted.retrieval_query_ms or message, reasoning="low")
    except Exception as exc:  # noqa: BLE001 — groundedness degrades, verdicts don't
        trace.append({"stage": "RETRIEVE", "status": "error", "detail": str(exc)[:120]})
    else:
        trace.append({"stage": "RETRIEVE", "status": "ok", "passages": len(passages)})

    facts_text = narrate.build_facts_text(assessment)
    narrative_ms = _safe_invoke_agent(
        agents.COMMUNICATOR.id, _communicator_prompt(message, facts_text, passages))

    # Two failure modes are handled differently, on purpose:
    if narrative_ms:
        #  - Agent narrative PRESENT → full dual gate (amount + groundedness). A present
        #    narrative that fails (fabricated amount / ungrounded) is a real trust
        #    violation → refuse and route to a human (the cite-or-refuse invariant).
        degraded = False
        gate_ok, groundedness = _gate(narrative_ms, applicant, assessment, passages)
    else:
        #  - Agent UNAVAILABLE (empty / a transient 429) → a fixed, claim-free lead line;
        #    the verified eligible/near-miss CARDS (straight from compute/) carry every
        #    figure. There is no LLM prose to ground, so only the amount guard applies
        #    (the line has no RM → passes). This is what turned the citizen's "Request
        #    could not be processed" into a correct, cited answer under rate-limiting.
        degraded = True
        narrative_ms = _DEGRADED_NARRATIVE_MS
        amounts_ok, fabricated = _amount_only_gate(narrative_ms, applicant)
        gate_ok = amounts_ok
        groundedness = {"available": False, "grounded": True, "ungrounded_percentage": 0.0,
                        "amounts_ok": amounts_ok, "fabricated_amounts": fabricated}

    trace.append({"stage": "GATE", "status": "ok" if gate_ok else "refused",
                  "degraded": degraded, **groundedness})
    if not gate_ok:
        return _refusal_turn(base_state, lang, _REFUSAL_MS, action=ACTION_REFUSE, trace=trace)

    # 5) Verified → localize + persist -----------------------------------------
    canonical = _assessment_payload(narrative_ms, applicant, assessment, known)
    result, tok_ok = localize.localize_assess(canonical, lang)
    new_state = base_state.evolve(
        assessment={"total_monthly_min": assessment.total_monthly_min},
        turn=state.turn + 1)
    return ChatTurn(
        token=encode(new_state), action=ACTION_ASSESS,
        reply=result.get("message_ms", narrative_ms), reply_ms=narrative_ms,
        rationale_ms=rationale_ms, result=result, canonical_ms=canonical,
        citations=canonical["citations"], done=interview_done, turn=state.turn + 1,
        lang=lang, translation_ok=tok_ok, trace=trace)
