"""Azure AI Content Safety — Prompt Shields + Groundedness detection.

Both run on the same AIServices resource that hosts gpt-4o. Two guards:
  - shield_prompt(): detect jailbreak / prompt-injection in user input.
  - detect_groundedness(): verify the LLM narrative is supported by the source
    passages + deterministic verdicts. Ungrounded => the orchestrator refuses and
    routes to a human (Talian Kasih 15999) rather than emit a hallucinated benefit.

Network/feature failures degrade gracefully (returns 'unavailable', never crashes
the assessment) — the deterministic verdicts remain valid regardless.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from ingest import config

SHIELD_API = "2024-09-01"
GROUNDEDNESS_API = "2024-09-15-preview"
_TIMEOUT = 30


def _headers() -> dict[str, str]:
    return {
        "Ocp-Apim-Subscription-Key": config.aoai_key(),
        "Content-Type": "application/json",
    }


@dataclass(frozen=True)
class ShieldResult:
    available: bool
    attack_detected: bool
    detail: str = ""


def shield_prompt(user_prompt: str, documents: Optional[list[str]] = None) -> ShieldResult:
    """Detect prompt-injection / jailbreak attempts in untrusted input."""
    url = f"{config.AOAI_ENDPOINT}/contentsafety/text:shieldPrompt?api-version={SHIELD_API}"
    body = {"userPrompt": user_prompt, "documents": documents or []}
    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return ShieldResult(available=False, attack_detected=False, detail=str(exc)[:200])

    user_analysis = data.get("userPromptAnalysis", {})
    doc_analyses = data.get("documentsAnalysis", [])
    attack = bool(user_analysis.get("attackDetected")) or any(
        d.get("attackDetected") for d in doc_analyses)
    return ShieldResult(available=True, attack_detected=attack)


@dataclass(frozen=True)
class GroundednessResult:
    available: bool
    grounded: bool
    ungrounded_percentage: float = 0.0
    threshold: float = 0.0
    detail: str = ""


# Calibrated: plain-language faithful narratives score well under this; only major
# drift (e.g. the fabricated "RM9000 + free car" case scores 1.0) trips it.
GROUNDEDNESS_REFUSE_THRESHOLD = 0.6


def detect_groundedness(text: str, grounding_sources: list[str], *,
                        refuse_threshold: float = GROUNDEDNESS_REFUSE_THRESHOLD
                        ) -> GroundednessResult:
    """Check that `text` is supported by `grounding_sources` (corpus + verdicts)."""
    sources = [s for s in grounding_sources if s and s.strip()]
    if not text.strip() or not sources:
        return GroundednessResult(available=False, grounded=True, threshold=refuse_threshold,
                                  detail="no text or sources to check")

    url = (f"{config.AOAI_ENDPOINT}/contentsafety/text:detectGroundedness"
           f"?api-version={GROUNDEDNESS_API}")
    body = {
        "domain": "Generic",
        "task": "Summarization",
        "text": text,
        "groundingSources": sources,
        "reasoning": False,
    }
    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return GroundednessResult(available=False, grounded=True,
                                  threshold=refuse_threshold, detail=str(exc)[:200])

    pct = float(data.get("ungroundedPercentage", 0.0))
    return GroundednessResult(available=True, grounded=pct < refuse_threshold,
                              ungrounded_percentage=pct, threshold=refuse_threshold)
