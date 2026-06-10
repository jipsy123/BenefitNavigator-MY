"""Azure OpenAI (gpt-4o) chat client for the orchestrator.

The model only ever *narrates* deterministic verdicts and *extracts* structured
intake — it never computes eligibility. Temperature defaults to 0 for the parts
that must be reproducible.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from openai import AzureOpenAI

from ingest import config


@lru_cache(maxsize=1)
def client() -> AzureOpenAI:
    # Bound the timeout/retries so a slow call fails fast instead of hanging the
    # pipeline (the SDK default is 600s with multiple retries).
    return AzureOpenAI(
        azure_endpoint=config.AOAI_ENDPOINT,
        api_key=config.aoai_key(),
        api_version=config.AOAI_API_VERSION,
        timeout=60.0,
        max_retries=2,
    )


def chat_json(system: str, user: str, *, temperature: float = 0.0,
              max_tokens: int = 1500) -> dict[str, Any]:
    """Chat completion constrained to a JSON object response."""
    response = client().chat.completions.create(
        model=config.AOAI_CHAT_DEPLOYMENT,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content or "{}")


def chat_text(system: str, user: str, *, temperature: float = 0.2,
              max_tokens: int = 1200, timeout: float | None = None) -> str:
    """`timeout` (seconds) overrides the client default for latency-sensitive calls
    (e.g. per-turn grill phrasing, where slow means fall back to a template)."""
    cli = client() if timeout is None else client().with_options(
        timeout=timeout, max_retries=0)
    response = cli.chat.completions.create(
        model=config.AOAI_CHAT_DEPLOYMENT,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()
