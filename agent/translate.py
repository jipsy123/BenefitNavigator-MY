"""Azure Translator wrapper for multilingual output (BM ↔ EN / ZH / TA).

Uses the same AIServices multi-service resource key with the global Translator
endpoint. Best-effort: if translation is unavailable, the original Malay text is
returned (the assessment is authored in BM and remains valid).
"""
from __future__ import annotations

import requests

from ingest import config

_ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
_REGION = "swedencentral"
SUPPORTED = {"ms": "Bahasa Melayu", "en": "English", "zh-Hans": "中文", "ta": "தமிழ்"}
_TIMEOUT = 20


def translate(text: str, to_lang: str, from_lang: str = "ms") -> str:
    """Translate `text` to `to_lang`. Returns the original text on any failure or
    when target == source."""
    if not text.strip() or to_lang == from_lang or to_lang not in SUPPORTED:
        return text
    params = {"api-version": "3.0", "from": from_lang, "to": to_lang}
    headers = {
        "Ocp-Apim-Subscription-Key": config.aoai_key(),
        "Ocp-Apim-Subscription-Region": _REGION,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(_ENDPOINT, params=params, headers=headers,
                             json=[{"text": text}], timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()[0]["translations"][0]["text"]
    except (requests.RequestException, KeyError, IndexError):
        return text
