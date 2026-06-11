"""Azure Translator wrapper for multilingual *presentation* (BM -> EN / ZH / TA).

The pipeline reasons and verifies entirely in Bahasa Melayu; translation is a
final presentation step applied to an already-grounded, already-amount-checked
result (see agent/localize.py). This module is the thin transport layer only.

All-or-nothing by design: a batch either fully succeeds or reports failure, so the
caller can fall back to Malay *visibly* rather than emit a half-translated mix.
"""
from __future__ import annotations

import html
import re

import requests

from ingest import config

_ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
_REGION = "swedencentral"
SUPPORTED = {"ms": "Bahasa Melayu", "en": "English", "zh-Hans": "中文", "ta": "தமிழ்"}
_TIMEOUT = 30
# Azure Translator limits: <=1000 array elements and <=50,000 chars per request.
_MAX_ITEMS = 900
_MAX_CHARS = 45_000

# Proper nouns that must survive translation verbatim — identifiers with no English
# equivalent: the crisis hotline NAME (which must stay dialable/recognisable) and the
# government system/document names. Without protection the Translator mangles them
# (observed: "Talian Kasih" -> "Línea Kasih"). We send these wrapped in Azure
# Translator's `class="notranslate"` markup (textType=html), which it keeps verbatim.
#
# Agency CODES (JKM, LHDN) are deliberately NOT protected: in prose their English
# expansions are acceptable ("IRB" for LHDN), the agency *tags* on result cards aren't
# translated at all, and protecting a bare acronym mid-sentence makes the Translator
# abut it against the next word ("JKMoffice"). "Talian Kasih" is always followed by the
# number 15999, so it never suffers that spacing artifact.
# Longest-first so multi-word terms match before any substring (e.g. eKasih vs Kasih).
_PROTECTED_TERMS = (
    "Talian Kasih", "eKasih", "MyKad", "MyKID",
)
_PROTECT_RE = re.compile("|".join(re.escape(t) for t in
                                  sorted(_PROTECTED_TERMS, key=len, reverse=True)))
# Match Azure's returned notranslate spans (it may reorder/extend attributes) + <br>.
_SPAN_RE = re.compile(r'<span[^>]*\bnotranslate\b[^>]*>(.*?)</span>', re.DOTALL | re.IGNORECASE)
_BR_RE = re.compile(r'<br\s*/?>', re.IGNORECASE)


def _protect(text: str) -> str:
    """Escape the text as HTML, keep newlines as <br>, and wrap protected proper nouns
    in notranslate spans — the form Azure Translator honours with textType=html."""
    escaped = html.escape(text, quote=False)                 # & < >  (terms are ASCII-safe)
    escaped = escaped.replace("\n", "<br>")
    return _PROTECT_RE.sub(lambda m: f'<span class="notranslate">{m.group(0)}</span>', escaped)


def _restore(text: str) -> str:
    """Reverse :func:`_protect` on a translated string: unwrap notranslate spans, turn
    <br> back into newlines, and unescape HTML entities to recover plain text."""
    text = _SPAN_RE.sub(lambda m: m.group(1), text)
    text = re.sub(r'</?span[^>]*>', '', text)                # strip any stray span tags
    text = _BR_RE.sub("\n", text)
    return html.unescape(text)


def _post_chunk(texts: list[str], to_lang: str, from_lang: str) -> list[str]:
    """Translate one within-limits chunk. Raises on any transport/shape error.

    Sent as HTML (textType=html) so proper nouns wrapped by :func:`_protect` are kept
    verbatim; the markup is stripped back out by :func:`_restore` on the way home."""
    params = {"api-version": "3.0", "from": from_lang, "to": to_lang, "textType": "html"}
    headers = {
        "Ocp-Apim-Subscription-Key": config.aoai_key(),
        "Ocp-Apim-Subscription-Region": _REGION,
        "Content-Type": "application/json",
    }
    resp = requests.post(_ENDPOINT, params=params, headers=headers,
                         json=[{"text": _protect(t)} for t in texts], timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    return [_restore(item["translations"][0]["text"]) for item in body]


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
