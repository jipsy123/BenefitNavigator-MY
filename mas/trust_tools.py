"""The deterministic trust core, exposed as agent-callable tool functions.

These are the pure functions the MCP server (mas/mcp_server.py) registers as tools
for the hosted agents. Every one of them:

  - takes the HMAC-signed state token (never raw facts from the model), verifies it,
    and reads the real facts from inside — so an agent cannot alter a verdict's inputs;
  - returns ONLY values produced by `compute/` (verdicts, amounts, gaps, plans) — never
    a narrative and never a model-editable number;
  - is pure and side-effect-free, so it runs identically whether called in-process by a
    test or over MCP by a Foundry-hosted agent.

`assess` deliberately returns deterministic verdicts WITHOUT a narrative: narration is
the Communicator agent's job and the dual safety gate is FastAPI's. The tool surface is
the same trust boundary the rest of the system uses, applied to agent tool calls.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from agent import readability                       # pure text scorer, no LLM/Azure
from compute import checker, elicit, optimizer
from compute.status import Assessment, summarise

from .state import ChatState, decode

READABLE_MAX_GRADE = 6.0


def _applicant_and_state(state_token: str):
    """Verify the token and materialise the validated Applicant it carries."""
    st: ChatState = decode(state_token)                      # raises InvalidToken on tamper
    known = elicit.with_presumed(st.facts, st.presumed)
    return elicit.to_applicant(known), st


# --- serialisers (match the shapes the legacy /assess + /grill already emit) -----

def _gap_dict(gap) -> dict:
    return {"program_id": gap.program_id, "name_ms": gap.name_ms, "agency": gap.agency,
            "amount": gap.amount, "near_miss": gap.near_miss,
            "blocking_ms": list(gap.blocking_ms), "actions_ms": list(gap.actions_ms),
            "citation": gap.citation}


def _verdict_citations(assessment: Assessment) -> list[dict]:
    """Cite the gazetted sources the verdicts rest on (deduped) — same rule the
    orchestrator uses, minus retrieval passages (those are the Retrieval agent's)."""
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


def _need_dict(need: Optional[elicit.FieldNeed]) -> Optional[dict]:
    """Serialise the engine's chosen question. No `question_text`: phrasing is the
    Interview agent's job; the engine only decides WHICH field, deterministically."""
    if need is None:
        return None
    return {"field": need.field, "answer_kind": need.answer_kind,
            "skippable": need.skippable, "choices": list(need.choices),
            "programs": [{"program_id": p.program_id, "name_ms": p.name_ms,
                          "amount": p.amount} for p in need.programs]}


# --- the tools -------------------------------------------------------------------

def assess(state_token: str) -> dict:
    """Deterministic eligibility verdicts + gaps for the profile in the token.

    Returns compute output only — eligible programmes (with amounts + citations),
    near-miss/blocked gaps, the guaranteed monthly floor, and the verdict citations.
    No narrative, no retrieval, no gate: those belong to other agents / FastAPI.
    """
    applicant, _ = _applicant_and_state(state_token)
    a = summarise(applicant)
    return {
        "eligible": [checker.to_dict(r) for r in a.eligible],
        "gaps": [_gap_dict(g) for g in a.gaps],
        "total_monthly_min": a.total_monthly_min,
        "citations": _verdict_citations(a),
    }


def optimize(state_token: str) -> dict:
    """The deterministic optimal-unlock plan: the ordered registration steps that add
    the most RM/month, each with the marginal gain and the programme citations it
    unlocks. Pure Python, so every figure passes the amount guard by construction."""
    applicant, _ = _applicant_and_state(state_token)
    return asdict(optimizer.plan(applicant))


def grill_next(state_token: str) -> dict:
    """The single highest-leverage unanswered question (or done), chosen by the
    deterministic interview engine. The Interview agent phrases it; it never picks it."""
    st = decode(state_token)
    known = elicit.with_presumed(st.facts, st.presumed)
    need = elicit.next_field(known, st.asked)
    return {"done": need is None, "progress": elicit.progress(known, st.asked),
            "question": _need_dict(need)}


def grade(text: str) -> dict:
    """Reading-grade of a Malay narrative (lower = easier). Used by the Communicator's
    simplify loop. Text is not trust-critical, so this takes plain text, not a token."""
    g = readability.grade_level(text or "")
    return {"grade": g, "readable": g <= READABLE_MAX_GRADE,
            "target_grade": READABLE_MAX_GRADE}
