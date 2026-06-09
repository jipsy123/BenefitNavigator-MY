"""Azure Translator wrapper for multilingual *presentation* (BM -> EN / ZH / TA).

The pipeline reasons and verifies entirely in Bahasa Melayu; translation is a
final presentation step applied to an already-grounded, already-amount-checked
result (see agent/localize.py). This module is the thin transport layer only.

All-or-nothing by design: a batch either fully succeeds or reports failure, so the
caller can fall back to Malay *visibly* rather than emit a half-translated mix.
"""
from __future__ import annotations

import requests

from ingest import config

_ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
_REGION = "swedencentral"
SUPPORTED = {"ms": "Bahasa Melayu", "en": "English", "zh-Hans": "中文", "ta": "தமிழ்"}
_TIMEOUT = 30
# Azure Translator limits: <=1000 array elements and <=50,000 chars per request.
_MAX_ITEMS = 900
_MAX_CHARS = 45_000


def _post_chunk(texts: list[str], to_lang: str, from_lang: str) -> list[str]:
    """Translate one within-limits chunk. Raises on any transport/shape error."""
    params = {"api-version": "3.0", "from": from_lang, "to": to_lang}
    headers = {
        "Ocp-Apim-Subscription-Key": config.aoai_key(),
        "Ocp-Apim-Subscription-Region": _REGION,
        "Content-Type": "application/json",
    }
    resp = requests.post(_ENDPOINT, params=params, headers=headers,
                         json=[{"text": t} for t in texts], timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    return [item["translations"][0]["text"] for item in body]


def _chunks(texts: list[str]) -> list[list[str]]:
    """Split into request-sized chunks respecting both item and char limits."""
    out: list[list[str]] = []
    cur: list[str] = []
    cur_chars = 0
    for t in texts:
        if cur and (len(cur) >= _MAX_ITEMS or cur_chars + len(t) > _MAX_CHARS):
            out.append(cur)
            cur, cur_chars = [], 0
        cur.append(t)
        cur_chars += len(t)
    if cur:
        out.append(cur)
    return out


def translate_batch(texts: list[str], to_lang: str,
                    from_lang: str = "ms") -> tuple[list[str], bool]:
    """Translate many strings in a single round-trip (chunked if large).

    Returns (translations, ok). On target==source or unsupported language this is a
    no-op that returns the originals with ok=True. On ANY failure it returns the
    originals with ok=False — never a partial mix. Empty/whitespace strings are
    passed through untranslated to avoid wasted calls and API edge cases.
    """
    if to_lang == from_lang or to_lang not in SUPPORTED or not texts:
        return list(texts), True

    # Only send strings that actually carry content; pass the rest through verbatim.
    send_idx = [i for i, t in enumerate(texts) if t and t.strip()]
    if not send_idx:
        return list(texts), True

    payload = [texts[i] for i in send_idx]
    try:
        translated: list[str] = []
        for chunk in _chunks(payload):
            translated.extend(_post_chunk(chunk, to_lang, from_lang))
        if len(translated) != len(payload):
            return list(texts), False
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return list(texts), False

    out = list(texts)
    for pos, i in enumerate(send_idx):
        out[i] = translated[pos]
    return out, True


def translate(text: str, to_lang: str, from_lang: str = "ms") -> str:
    """Single-string convenience wrapper. Returns the original on any failure."""
    if not text.strip():
        return text
    out, _ok = translate_batch([text], to_lang, from_lang)
    return out[0]
