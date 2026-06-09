"""The orchestrator: INTAKE -> RETRIEVE -> COMPUTE -> GAP -> EXPLAIN, with a
prompt-shield on input and a groundedness gate on output.

Design invariants:
  - Deterministic verdicts (COMPUTE/GAP) are the source of truth and never depend
    on the LLM or on retrieval succeeding.
  - The LLM narrative is only emitted if Content Safety confirms it is grounded;
    otherwise we refuse and route to a human (Talian Kasih 15999).
  - Every stage records a trace entry so the UI can show the pipeline working.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from compute import checker
from compute.status import Assessment, summarise
from ingest import knowledge_base as kb

from . import intake, narrate, safety, verify

TALIAN_KASIH = "Talian Kasih 15999"
_REFUSAL_MS = (
    "Maaf, kami tidak dapat mengesahkan jawapan ini dengan sumber rasmi, jadi kami "
    "tidak akan memberikannya. Sila hubungi " + TALIAN_KASIH + " atau pejabat JKM/LHDN "
    "daerah anda untuk bantuan lanjut."
)
_BLOCKED_MS = (
    "Input anda kelihatan cuba mengubah arahan sistem, jadi ia tidak diproses. "
    "Sila terangkan keadaan anda dengan ayat biasa, atau hubungi " + TALIAN_KASIH + "."
)


@dataclass(frozen=True)
class Stage:
    name: str
    status: str            # "ok" | "skipped" | "error" | "blocked" | "refused"
    summary: str
    data: Any = None


@dataclass(frozen=True)
class PipelineResult:
    ok: bool
    refused: bool
    message_ms: str                       # the narrative, or the refusal/blocked text
    profile: Optional[dict] = None
    assumptions_ms: tuple[str, ...] = ()
    eligible: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    total_monthly_min: int = 0
    citations: list[dict] = field(default_factory=list)
    groundedness: dict = field(default_factory=dict)
    stages: list[dict] = field(default_factory=list)


def _collect_citations(assessment: Assessment, passages: list[dict]) -> list[dict]:
    """Cite the sources the verdicts are *based on* — the curated program rules with
    clean locators — not every passage retrieved for grounding (those carry raw PDF
    heading artifacts and would clutter the citation list)."""
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


def run(user_text: str, *, reasoning: str = "low") -> PipelineResult:
    stages: list[dict] = []

    def record(stage: Stage) -> None:
        stages.append(asdict(stage))

    # 0) Prompt shield on untrusted input -------------------------------------
    shield = safety.shield_prompt(user_text)
    if shield.available and shield.attack_detected:
        record(Stage("SHIELD", "blocked", "Prompt-injection dikesan."))
        return PipelineResult(ok=False, refused=True, message_ms=_BLOCKED_MS,
                              stages=stages, groundedness={"blocked_input": True})
    record(Stage("SHIELD", "ok",
                 "Tiada serangan dikesan." if shield.available else "Shield tidak tersedia."))

    # 1) INTAKE ----------------------------------------------------------------
    intake_result = intake.run_intake(user_text)
    applicant = intake_result.applicant
    record(Stage("INTAKE", "ok", "Profil diekstrak daripada teks.",
                 data={"profile": asdict(applicant),
                       "assumptions_ms": list(intake_result.assumptions_ms)}))

    # 2..5) shared tail: RETRIEVE -> COMPUTE+GAP -> EXPLAIN + dual gate --------
    return _finish(applicant, intake_result.retrieval_query_ms,
                   intake_result.assumptions_ms, stages, reasoning=reasoning)


def run_from_applicant(applicant, *, retrieval_query_ms: str,
                       assumptions_ms: tuple[str, ...] = (),
                       reasoning: str = "low") -> PipelineResult:
    """Assess a profile gathered structurally by the grill (interview) instead of from
    free text. Skips SHIELD + INTAKE on purpose: structured facts carry no injection
    surface, and the opening paragraph was already shielded at /grill/start. Everything
    downstream — verdicts, narrative, and the dual safety gate — is identical to run().
    """
    stages: list[dict] = [
        asdict(Stage("SHIELD", "ok", "Input awal disaring; jawapan susulan berstruktur.")),
        asdict(Stage("INTAKE", "ok", "Profil dikumpul melalui temu bual berpandu.",
                     data={"profile": asdict(applicant),
                           "assumptions_ms": list(assumptions_ms)})),
    ]
    return _finish(applicant, retrieval_query_ms, assumptions_ms, stages,
                   reasoning=reasoning)


def _finish(applicant, retrieval_query_ms: str, assumptions_ms: tuple[str, ...],
            stages: list[dict], *, reasoning: str) -> PipelineResult:
    """RETRIEVE -> COMPUTE+GAP -> EXPLAIN + dual safety gate. Shared by both entry
    points so the assess path stays byte-for-byte identical regardless of how the
    Applicant was obtained."""
    def record(stage: Stage) -> None:
        stages.append(asdict(stage))

    # 2) RETRIEVE (agentic) — resilient: verdicts don't depend on this ---------
    passages: list[dict] = []
    try:
        passages = kb.retrieve_passages(retrieval_query_ms, reasoning=reasoning)
        record(Stage("RETRIEVE", "ok", f"{len(passages)} petikan bersumber diperoleh."))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, keep deterministic core
        record(Stage("RETRIEVE", "error", f"Retrieval gagal: {str(exc)[:120]}"))

    # 3) COMPUTE + 4) GAP (deterministic) --------------------------------------
    assessment = summarise(applicant)
    record(Stage("COMPUTE+GAP", "ok",
                 f"{len(assessment.eligible)} layak; {len(assessment.gaps)} jurang.",
                 data={"eligible": [checker.to_dict(r) for r in assessment.eligible]}))

    # 5) EXPLAIN + dual safety gate --------------------------------------------
    narrative, facts = narrate.run_narrate(assessment, passages, assumptions_ms)

    # Hard guard: no fabricated money amount (deterministic, precise).
    thresholds = checker.load_thresholds()
    allowed = verify.allowed_amounts(applicant, thresholds)
    amounts_ok, fabricated = verify.verify_amounts(narrative, allowed)

    # Soft guard: groundedness % against verdicts + procedural facts + passages.
    grounding = [facts, narrate.PROCEDURAL_FACTS_MS] + [p.get("content", "") for p in passages]
    g = safety.detect_groundedness(narrative, grounding)

    refused = (not amounts_ok) or (g.available and not g.grounded)
    groundedness = {"available": g.available, "grounded": g.grounded,
                    "ungrounded_percentage": g.ungrounded_percentage,
                    "threshold": g.threshold, "amounts_ok": amounts_ok,
                    "fabricated_amounts": fabricated}
    if refused:
        reason = (f"jumlah RM tidak bersumber {fabricated}" if not amounts_ok
                  else f"naratif {g.ungrounded_percentage:.0%} tidak bersumber")
        record(Stage("EXPLAIN", "refused", f"Ditolak ({reason}); dihala ke manusia."))
        message = _REFUSAL_MS
    else:
        status = "ok" if g.available else "ok (gate tidak tersedia)"
        record(Stage("EXPLAIN", status,
                     f"Disahkan: jumlah RM bersumber, naratif {g.ungrounded_percentage:.0%} tidak bersumber."))
        message = narrative

    return PipelineResult(
        ok=not refused,
        refused=refused,
        message_ms=message,
        profile=asdict(applicant),
        assumptions_ms=assumptions_ms,
        eligible=[checker.to_dict(r) for r in assessment.eligible],
        gaps=[{"program_id": gp.program_id, "name_ms": gp.name_ms, "agency": gp.agency,
               "amount": gp.amount, "near_miss": gp.near_miss,
               "blocking_ms": list(gp.blocking_ms), "actions_ms": list(gp.actions_ms),
               "citation": gp.citation} for gp in assessment.gaps],
        total_monthly_min=assessment.total_monthly_min,
        citations=_collect_citations(assessment, passages),
        groundedness=groundedness,
        stages=stages,
    )
