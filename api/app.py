"""BenefitNavigator Malaysia — HTTP API.

Endpoints:
  GET  /health           liveness + supported languages
  POST /assess           run the 5-stage pipeline (always in Malay), return the
                         verified Malay result localized into the requested language
  POST /appeal           draft a surat rayuan for one program, localized
  POST /localize         re-localize an already-computed Malay result/letter into a
                         new language (used for instant language toggles — no re-run)
  GET  /                 serve the accessible single-page UI

Language model: the pipeline reasons and verifies entirely in Bahasa Melayu; every
response carries `canonical_ms` (the verified Malay payload) plus `result`/`letter`
in the requested display language and a `translation_ok` flag. Clients localize
toggles from `canonical_ms` so we never translate an already-translated language.
Synthetic PII only — never send a real NRIC/MyKad.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import appeal, intake, localize, orchestrator, phrase, safety, translate
from compute import elicit

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
_MAX_TEXT = 4000
_MAX_PAYLOAD_CHARS = 200_000  # generous cap for a single result/letter payload

app = FastAPI(title="BenefitNavigator Malaysia", version="1.1")


class AssessRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    lang: str = "en"


class AppealRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    program_id: str = Field(min_length=1, max_length=64)
    lang: str = "en"


class LocalizeRequest(BaseModel):
    kind: Literal["assess", "appeal"]
    payload: dict
    lang: str


# --- grill (adaptive interview) request models -----------------------------------

class GrillStartRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    lang: str = "en"


class GrillNextRequest(BaseModel):
    facts: dict = Field(default_factory=dict)
    presumed: dict = Field(default_factory=dict)
    asked: list[str] = Field(default_factory=list)
    # No field = recompute only (e.g. after a presumption chip is dismissed).
    field: Optional[str] = Field(default=None, min_length=1, max_length=64)
    value: Any = None
    skip: bool = False
    # Optional context for display-only question phrasing (template fallback if absent).
    text: str = Field(default="", max_length=_MAX_TEXT)
    lang: str = "en"


class GrillAssessRequest(BaseModel):
    facts: dict = Field(default_factory=dict)
    presumed: dict = Field(default_factory=dict)
    retrieval_query_ms: str = Field(default="", max_length=_MAX_TEXT)
    assumptions_ms: list[str] = Field(default_factory=list)
    lang: str = "en"


def _check_lang(lang: str) -> None:
    if lang not in translate.SUPPORTED:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported language: {lang!r}")


def _question(need: Optional[elicit.FieldNeed], *, user_text: str = "",
              known: Optional[dict] = None, lang: str = "en") -> Optional[dict]:
    """Serialise the engine's next question for the client. `question_text` is the
    optional contextual phrasing; when None the client renders its static i18n
    template keyed by `field` — phrasing can never block or break the grill."""
    if need is None:
        return None
    question_text = None
    if user_text:
        question_text = phrase.phrase_question(need.field, user_text,
                                               known or {}, lang)
    return {
        "field": need.field,
        "answer_kind": need.answer_kind,
        "skippable": need.skippable,
        "choices": list(need.choices),
        "question_text": question_text,
        # programmes this answer could unlock — drives the 'why we're asking' chip.
        "programs": [{"program_id": p.program_id, "name_ms": p.name_ms,
                      "amount": p.amount} for p in need.programs],
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "languages": list(translate.SUPPORTED)}


@app.post("/assess")
def assess(req: AssessRequest) -> dict:
    _check_lang(req.lang)
    result = orchestrator.run(req.text)
    canonical = asdict(result)
    display, ok = localize.localize_assess(canonical, req.lang)
    return {"lang": req.lang, "translation_ok": ok,
            "result": display, "canonical_ms": canonical}


@app.post("/appeal")
def draft_appeal(req: AppealRequest) -> dict:
    _check_lang(req.lang)
    try:
        letter = appeal.draft(req.text, req.program_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    canonical = asdict(letter)
    display, ok = localize.localize_appeal(canonical, req.lang)
    return {"lang": req.lang, "translation_ok": ok,
            "letter": display, "canonical_ms": canonical}


@app.post("/localize")
def relocalize(req: LocalizeRequest) -> dict:
    """Re-localize a canonical Malay payload into `lang`. Stateless: the client
    supplies the Malay payload it cached from /assess or /appeal."""
    _check_lang(req.lang)
    if len(str(req.payload)) > _MAX_PAYLOAD_CHARS:
        raise HTTPException(status_code=413, detail="Payload too large.")
    if req.kind == "assess":
        display, ok = localize.localize_assess(req.payload, req.lang)
        return {"lang": req.lang, "translation_ok": ok, "result": display}
    display, ok = localize.localize_appeal(req.payload, req.lang)
    return {"lang": req.lang, "translation_ok": ok, "letter": display}


@app.post("/grill/start")
def grill_start(req: GrillStartRequest) -> dict:
    """Open the interview: shield the free-text paragraph, extract whatever facts it
    states, then let the deterministic engine pick the first decision-relevant gap."""
    _check_lang(req.lang)
    shield = safety.shield_prompt(req.text)
    if shield.available and shield.attack_detected:
        return {"ok": False, "blocked": True}

    intake_result = intake.run_intake(req.text)
    facts = elicit.sanitize_facts(intake_result.facts)
    presumed = elicit.sanitize_presumptions(intake_result.presumed, facts)
    asked: list[str] = []
    known = elicit.with_presumed(facts, presumed)
    need = elicit.next_field(known, asked)
    return {
        "ok": True, "blocked": False, "facts": facts, "presumed": presumed,
        "asked": asked,
        "assumptions_ms": list(intake_result.assumptions_ms),
        "retrieval_query_ms": intake_result.retrieval_query_ms,
        "question": _question(need, user_text=req.text, known=known, lang=req.lang),
        "done": need is None,
        "progress": elicit.progress(known, asked),
    }


def _check_grill_sizes(presumed: dict, asked: list) -> None:
    """Bound client-echoed collections before any per-item work (DoS guard).
    Legitimate clients never exceed the askable field count."""
    cap = 4 * len(elicit.FIELD_META)
    if len(presumed) > cap or len(asked) > cap:
        raise ValueError("payload too large")


@app.post("/grill/next")
def grill_next(req: GrillNextRequest) -> dict:
    """Apply one structured answer (deterministic, no LLM) and return the next gap."""
    _check_lang(req.lang)
    try:
        _check_grill_sizes(req.presumed, req.asked)
        facts = elicit.sanitize_facts(req.facts)
        asked = list(dict.fromkeys(req.asked))            # dedupe, keep order
        if req.field is not None:
            if req.field not in elicit.FIELD_META:
                raise ValueError(f"not an askable field: {req.field!r}")
            if req.skip:
                if not elicit.FIELD_META[req.field].skippable:
                    raise ValueError(f"{req.field!r} cannot be skipped")
            else:
                facts = {**facts, req.field: elicit.coerce_value(req.field, req.value)}
            if req.field not in asked:
                asked = asked + [req.field]
        presumed = elicit.sanitize_presumptions(req.presumed, facts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    known = elicit.with_presumed(facts, presumed)
    need = elicit.next_field(known, asked)
    return {"facts": facts, "presumed": presumed, "asked": asked,
            "question": _question(need, user_text=req.text, known=known,
                                  lang=req.lang),
            "done": need is None,
            "progress": elicit.progress(known, asked)}


@app.post("/grill/assess")
def grill_assess(req: GrillAssessRequest) -> dict:
    """Run the gathered profile through the SAME pipeline as /assess (skipping the
    free-text intake), then localize. Identical response shape to /assess."""
    _check_lang(req.lang)
    try:
        _check_grill_sizes(req.presumed, [])
        facts = elicit.sanitize_facts(req.facts)
        presumed = elicit.sanitize_presumptions(req.presumed, facts)
        applicant = elicit.to_applicant(elicit.with_presumed(facts, presumed))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Surviving presumptions are openly part of the verdict's assumption trail.
    assumptions = tuple(req.assumptions_ms) + tuple(
        entry["reason_ms"] for entry in presumed.values() if entry["reason_ms"])
    query = req.retrieval_query_ms or "kelayakan bantuan kerajaan Malaysia"
    result = orchestrator.run_from_applicant(
        applicant, retrieval_query_ms=query, assumptions_ms=assumptions)
    canonical = asdict(result)
    display, ok = localize.localize_assess(canonical, req.lang)
    return {"lang": req.lang, "translation_ok": ok,
            "result": display, "canonical_ms": canonical}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
