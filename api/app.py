"""BenefitNavigator Malaysia — HTTP API.

Endpoints:
  GET  /health           liveness
  POST /assess           run the 5-stage pipeline on a free-text Malay description
  POST /appeal           draft a surat rayuan for one program
  GET  /                 serve the accessible single-page UI
Synthetic PII only — never send a real NRIC/MyKad.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import appeal, orchestrator, translate

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
_MAX_TEXT = 4000

app = FastAPI(title="BenefitNavigator Malaysia", version="1.0")


class AssessRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    lang: str = "ms"


class AppealRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    program_id: str = Field(min_length=1, max_length=64)
    lang: str = "ms"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "languages": list(translate.SUPPORTED)}


@app.post("/assess")
def assess(req: AssessRequest) -> dict:
    result = orchestrator.run(req.text)
    data = asdict(result)
    if req.lang != "ms":
        data["message_ms"] = translate.translate(result.message_ms, req.lang)
        data["lang"] = req.lang
    return data


@app.post("/appeal")
def draft_appeal(req: AppealRequest) -> dict:
    try:
        letter = appeal.draft(req.text, req.program_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = asdict(letter)
    if req.lang != "ms":
        data["body_ms"] = translate.translate(letter.body_ms, req.lang)
        data["routing_ms"] = translate.translate(letter.routing_ms, req.lang)
    return data


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
