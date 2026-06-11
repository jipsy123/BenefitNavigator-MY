"""BenefitNavigator Malaysia — HTTP API (multi-agent /chat surface).

Endpoints:
  GET  /health    liveness + supported languages
  POST /chat       advance one conversation turn through the Foundry multi-agent
                   layer (Orchestrator → A2A specialists → trust-core MCP tools),
                   with the NON-BYPASSABLE dual safety gate enforced here in FastAPI
  POST /appeal     draft a surat rayuan for one program, localized (standalone)
  POST /localize   re-localize an already-verified Malay payload into a new language
                   (instant language toggles — no re-run, no re-translation)
  GET  /           serve the accessible single-page UI

Architecture: the LLM agents orchestrate and narrate; they never decide eligibility
or invent amounts. Verdicts/amounts come from compute/ (recomputed in-process here as
ground truth), and every agent narrative passes the amount guard + Content Safety
groundedness gate before a user sees it — else we refuse and route to Talian Kasih
15999. See mas/orchestrate.py for the per-turn trust flow.

Language model: the pipeline reasons and verifies entirely in Bahasa Melayu; every
response carries `canonical_ms` (the verified Malay payload — the source of truth)
plus the display text localized into the requested language and a `translation_ok`
flag. Clients re-localize toggles from `canonical_ms` so an already-translated
language is never re-translated. Synthetic PII only — never send a real NRIC/MyKad.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import appeal, localize, translate
from mas import orchestrate

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
_MAX_TEXT = 4000
_MAX_TOKEN = 32_768            # matches mas.state._MAX_TOKEN_BYTES
_MAX_PAYLOAD_CHARS = 200_000  # generous cap for a single result/letter payload

app = FastAPI(title="BenefitNavigator Malaysia", version="2.0")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=_MAX_TEXT)
    # Signed state token from the previous turn; absent/empty starts a new conversation.
    token: Optional[str] = Field(default=None, max_length=_MAX_TOKEN)
    lang: str = "en"


class AppealRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    program_id: str = Field(min_length=1, max_length=64)
    lang: str = "en"


class LocalizeRequest(BaseModel):
    kind: Literal["assess", "appeal"]
    payload: dict
    lang: str


def _check_lang(lang: str) -> None:
    if lang not in translate.SUPPORTED:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang!r}")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "languages": list(translate.SUPPORTED)}


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    """One conversation turn. The agents ask / assess / escalate; the trust spine +
    dual gate here guarantee no fabricated amount or ungrounded claim reaches the
    user. Returns the new state `token` (carry it back next turn) plus the localized
    reply and, on an assessment turn, the verified verdicts (`result` + `canonical_ms`)."""
    _check_lang(req.lang)
    try:
        turn = orchestrate.run_chat(req.message, req.token, req.lang)
    except ValueError as exc:                       # invalid/expired state token
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asdict(turn)


@app.post("/appeal")
def draft_appeal(req: AppealRequest) -> dict:
    """Draft a surat rayuan for one near-miss programme, localized. Standalone of the
    chat flow; the letter is grounded in the citizen's stated facts + the programme's
    cited criteria."""
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
    supplies the Malay payload it cached from a /chat assessment or /appeal."""
    _check_lang(req.lang)
    if len(str(req.payload)) > _MAX_PAYLOAD_CHARS:
        raise HTTPException(status_code=413, detail="Payload too large.")
    if req.kind == "assess":
        display, ok = localize.localize_assess(req.payload, req.lang)
        return {"lang": req.lang, "translation_ok": ok, "result": display}
    display, ok = localize.localize_appeal(req.payload, req.lang)
    return {"lang": req.lang, "translation_ok": ok, "letter": display}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
