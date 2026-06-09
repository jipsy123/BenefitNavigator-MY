"""Localization: translate an already-verified Malay result into a display language.

Trust model — this runs AFTER the orchestrator's dual safety gate (amount guard +
groundedness) has passed on the *Malay* result. Translation is presentation only:

  - Verdicts/amounts are deterministic and language-neutral; translating text can
    never change who qualifies for what.
  - Numeric RM amounts pass through machine translation unchanged (verified by a
    live probe across en/zh-Hans/ta), so the "every RM traces to a verdict"
    invariant is preserved across languages.
  - As a belt-and-suspenders check we re-assert that invariant on the translated
    text: every RM amount in the output must already exist in the Malay source.
    Translation can only ever *drop* or *keep* amounts, never invent them.

Failure is all-or-nothing and *visible*: if the Azure call fails OR the amount
check trips, we return the canonical Malay result with translation_ok=False so the
UI can show a "showing Bahasa Melayu" notice — never a silent half-translated mix.

All transforms are pure: inputs are never mutated; new dicts/lists are returned.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from . import translate

# Map full-width digits (used by some locales) back to ASCII before comparing.
_FULLWIDTH = {ord("０") + i: ord("0") + i for i in range(10)}
_AMOUNT_RE = re.compile(r"RM\s?\d[\d.,]*", re.IGNORECASE)

StrFn = Callable[[str], str]


def _normalize_amount(token: str) -> str:
    """'RM1,200' / 'RM 1.200' / full-width -> bare digit string '1200'."""
    token = token.translate(_FULLWIDTH)
    return re.sub(r"[^\d]", "", token)


def rm_amounts(text: str) -> set[str]:
    """Set of normalized RM-amounts in `text`. Robust to RM spacing, thousands
    separators, and full-width digits (all observed to survive Azure MT)."""
    return {_normalize_amount(m) for m in _AMOUNT_RE.findall(text or "")}


# --- Pure traversals: apply `fn` to every user-facing Malay string field ------
# collect and apply use the SAME traversal, so the order of fn() calls is
# identical in both passes — that is what makes index-based remapping safe.

def _map_amount(amount: Any, fn: StrFn) -> Any:
    if isinstance(amount, dict) and amount.get("note_ms"):
        return {**amount, "note_ms": fn(amount["note_ms"])}
    return amount


def _map_program(prog: dict, fn: StrFn) -> dict:
    out = {**prog}
    if prog.get("name_ms"):
        out["name_ms"] = fn(prog["name_ms"])
    out["amount"] = _map_amount(prog.get("amount"), fn)
    return out


def _map_gap(gap: dict, fn: StrFn) -> dict:
    out = {**gap}
    if gap.get("name_ms"):
        out["name_ms"] = fn(gap["name_ms"])
    out["amount"] = _map_amount(gap.get("amount"), fn)
    out["blocking_ms"] = [fn(x) for x in gap.get("blocking_ms", [])]
    out["actions_ms"] = [fn(x) for x in gap.get("actions_ms", [])]
    return out


def _map_stage(stage: dict, fn: StrFn) -> dict:
    if stage.get("summary"):
        return {**stage, "summary": fn(stage["summary"])}
    return {**stage}


def _map_assess(result: dict, fn: StrFn) -> dict:
    """Return a new result dict with every display string passed through `fn`.
    Citations (official document titles/locators) are intentionally left as-is."""
    out = {**result}
    if result.get("message_ms"):
        out["message_ms"] = fn(result["message_ms"])
    out["assumptions_ms"] = [fn(a) for a in result.get("assumptions_ms", [])]
    out["eligible"] = [_map_program(e, fn) for e in result.get("eligible", [])]
    out["gaps"] = [_map_gap(g, fn) for g in result.get("gaps", [])]
    out["stages"] = [_map_stage(s, fn) for s in result.get("stages", [])]
    return out


def _map_appeal(letter: dict, fn: StrFn) -> dict:
    out = {**letter}
    for key in ("program_name_ms", "body_ms", "routing_ms"):
        if letter.get(key):
            out[key] = fn(letter[key])
    return out


# --- Generic localize driver --------------------------------------------------

def _localize(payload: dict, lang: str,
              mapper: Callable[[dict, StrFn], dict]) -> tuple[dict, bool]:
    """Two-pass localize: collect strings via one traversal, batch-translate, then
    re-traverse applying results in order. Returns (display_payload, translation_ok).
    Falls back to the Malay payload (ok=False) on transport failure or if the
    translated text contains an RM-amount absent from the source."""
    if lang == "ms" or lang not in translate.SUPPORTED:
        return payload, True

    bucket: list[str] = []
    mapper(payload, lambda s: bucket.append(s) or s)  # collect (output discarded)
    if not bucket:
        return payload, True

    translated, ok = translate.translate_batch(bucket, lang)
    if not ok:
        return payload, False

    # Safety: no RM-amount may appear in the output that wasn't in the source.
    source_amounts = rm_amounts(" ".join(bucket))
    output_amounts = rm_amounts(" ".join(translated))
    if not output_amounts.issubset(source_amounts):
        return payload, False

    it = iter(translated)
    display = mapper(payload, lambda _s: next(it))
    display["lang"] = lang
    return display, True


def localize_assess(result_ms: dict, lang: str) -> tuple[dict, bool]:
    """Localize a verified /assess result. See module docstring for the trust model."""
    return _localize(result_ms, lang, _map_assess)


def localize_appeal(letter_ms: dict, lang: str) -> tuple[dict, bool]:
    """Localize a verified /appeal letter."""
    return _localize(letter_ms, lang, _map_appeal)
