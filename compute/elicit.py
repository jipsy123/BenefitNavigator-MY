"""Elicitation engine — the deterministic core of the grill (interview) feature.

Given the facts established so far, decide which *single* unanswered fact could still
change an eligibility verdict, and stop the moment none can. This is the deterministic
version of an interviewer's "do I have enough?" judgement: it reasons in three-valued
(Kleene) logic — true / false / UNKNOWN — over the same criteria the checker uses, so
the grill never invents domain knowledge and the LLM never decides what to ask.

Trust properties:
  - Pure functions, no LLM, no I/O beyond loading the curated thresholds.
  - `Applicant` cannot represent "unknown" (its fields have favourable defaults), so the
    grill carries a separate `known: dict` of established facts; only `to_applicant`
    materialises it for the existing pipeline, at which point validation re-applies.
  - Money fields are non-skippable: a skipped income would default to 0.0 and could
    manufacture a false ELIGIBLE — exactly what the downstream safety gate exists to
    stop. Booleans default conservatively (skip -> a GAP with a registration action).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from .checker import load_thresholds
from .profile import Applicant, from_dict

# Three-valued result: True | False | None(=UNKNOWN).
Tri = Optional[bool]


# --- Kleene three-valued logic ---------------------------------------------------

def kleene_and(values: Iterable[Tri]) -> Tri:
    """AND over true/false/unknown. One False wins; else unknown if any unknown."""
    seen_unknown = False
    for v in values:
        if v is False:
            return False
        if v is None:
            seen_unknown = True
    return None if seen_unknown else True


def kleene_or(values: Iterable[Tri]) -> Tri:
    """OR over true/false/unknown. One True wins; else unknown if any unknown."""
    seen_unknown = False
    for v in values:
        if v is True:
            return True
        if v is None:
            seen_unknown = True
    return None if seen_unknown else False


def kleene_not(value: Tri) -> Tri:
    return None if value is None else (not value)


def _flag(known: Mapping[str, Any], field: str) -> Tri:
    """A boolean field's three-valued reading: unknown until present in `known`."""
    if field not in known:
        return None
    return bool(known[field])


def _resolve_threshold(crit: dict, refs: dict) -> float:
    if "ref" in crit:
        return float(refs[crit["ref"]]["value"])
    return float(crit["threshold"])


# --- per-criterion evaluation (mirrors checker._EVALUATORS, three-valued) ---------

def eval_criterion(known: Mapping[str, Any], crit: dict, refs: dict) -> Tri:
    """Evaluate one criterion against partial facts. None means 'not yet decidable'."""
    t = crit["type"]
    if t == "citizen":
        return _flag(known, "citizen")
    if t == "age_gte":
        age = known.get("age")
        return None if age is None else age >= crit["value"]
    if t == "flag_true":
        return _flag(known, crit["flag"])
    if t == "marital_in":
        m = known.get("marital_status")
        return None if m is None else m in crit["values"]
    if t == "household_category":            # married OR has_dependents
        m = known.get("marital_status")
        married = None if m is None else (m == "married")
        return kleene_or([married, _flag(known, "has_dependents")])
    if t == "bujang_category":               # NOT married AND no dependents
        m = known.get("marital_status")
        not_married = None if m is None else (m != "married")
        return kleene_and([not_married, kleene_not(_flag(known, "has_dependents"))])
    if t == "individual_income_lte":
        v = known.get("individual_income")
        return None if v is None else v <= _resolve_threshold(crit, refs)
    if t == "household_income_lte":
        v = known.get("household_income")
        return None if v is None else v <= _resolve_threshold(crit, refs)
    if t == "str_bujang_age":                # age>=21 OR (age>=18 AND OKU AND Kad OKU)
        age = known.get("age")
        ge21 = None if age is None else age >= 21
        ge18 = None if age is None else age >= 18
        young_oku = kleene_and([ge18, _flag(known, "is_oku"), _flag(known, "has_kad_oku")])
        return kleene_or([ge21, young_oku])
    raise ValueError(f"unknown criterion type: {t!r}")


def criterion_fields(crit: dict) -> frozenset[str]:
    """The Applicant fields a criterion reads — the questions that could resolve it."""
    t = crit["type"]
    if t == "citizen":
        return frozenset({"citizen"})
    if t == "age_gte":
        return frozenset({"age"})
    if t == "flag_true":
        return frozenset({crit["flag"]})
    if t == "marital_in":
        return frozenset({"marital_status"})
    if t in ("household_category", "bujang_category"):
        return frozenset({"marital_status", "has_dependents"})
    if t == "individual_income_lte":
        return frozenset({"individual_income"})
    if t == "household_income_lte":
        return frozenset({"household_income"})
    if t == "str_bujang_age":
        return frozenset({"age", "is_oku", "has_kad_oku"})
    raise ValueError(f"unknown criterion type: {t!r}")


def program_status(known: Mapping[str, Any], spec: dict, refs: dict) -> Tri:
    """ELIGIBLE(True) / INELIGIBLE(False) / UNDECIDED(None) given the facts so far."""
    vals = [eval_criterion(known, c, refs) for c in spec["criteria"]]
    if spec.get("logic", "all") == "any":
        return kleene_or(vals)
    return kleene_and(vals)


# --- field metadata: how the client renders each question ------------------------

@dataclass(frozen=True)
class FieldMeta:
    kind: str                         # "boolean" | "integer" | "money" | "choice"
    skippable: bool                   # money is False — see module docstring
    choices: tuple[str, ...] = ()


# Only fields that appear in at least one criterion are ever asked. `household_size`,
# `ekasih_category` gate nothing, so they are intentionally absent. Insertion order is
# the stable tiebreak for equally-ranked questions (FIELD_ORDER below).
FIELD_META: dict[str, FieldMeta] = {
    "citizen":           FieldMeta("boolean", True),
    "age":               FieldMeta("integer", True),
    "marital_status":    FieldMeta("choice", True,
                                   ("single", "married", "widowed", "divorced")),
    "is_oku":            FieldMeta("boolean", True),
    "has_kad_oku":       FieldMeta("boolean", True),
    "unable_to_work":    FieldMeta("boolean", True),
    "is_working":        FieldMeta("boolean", True),
    "is_carer":          FieldMeta("boolean", True),
    "has_dependents":    FieldMeta("boolean", True),
    "individual_income": FieldMeta("money", False),
    "household_income":  FieldMeta("money", False),
    "str_approved":      FieldMeta("boolean", True),
    "ekasih_listed":     FieldMeta("boolean", True),
}
FIELD_ORDER: list[str] = list(FIELD_META)


@dataclass(frozen=True)
class ProgramRef:
    """A programme a pending question could help unlock — drives the 'why we ask' chip."""
    program_id: str
    name_ms: str
    amount: dict


@dataclass(frozen=True)
class FieldNeed:
    field: str
    answer_kind: str
    skippable: bool
    choices: tuple[str, ...]
    programs: tuple[ProgramRef, ...]   # undecided programmes this field could resolve


def _amount_max(amount: dict) -> float:
    if amount.get("type") == "fixed":
        return float(amount.get("monthly_myr", 0))
    return float(amount.get("monthly_myr_max", amount.get("monthly_myr_min", 0)))


def next_field(known: Mapping[str, Any], asked: Iterable[str],
               thresholds: Optional[dict] = None) -> Optional[FieldNeed]:
    """The single highest-leverage unanswered question, or None when the grill is done.

    'Done' = no programme is UNDECIDED on any not-yet-asked field, i.e. no question
    could change a verdict. Ranking maximises found benefits: a field that resolves
    more undecided programmes (then more RM, then earliest stable order) wins.
    """
    data = thresholds or load_thresholds()
    refs = data["reference_values"]
    programs = data["programs"]
    asked_set = set(asked)

    helpers: dict[str, set[str]] = {}
    for pid, spec in programs.items():
        if program_status(known, spec, refs) is not None:
            continue                                   # decided -> generates no questions
        for crit in spec["criteria"]:
            if eval_criterion(known, crit, refs) is not None:
                continue                               # this criterion already decided
            for field in criterion_fields(crit):
                if field in known or field in asked_set:
                    continue
                helpers.setdefault(field, set()).add(pid)

    if not helpers:
        return None

    def rank(field: str) -> tuple:
        pids = helpers[field]
        max_amount = max(_amount_max(programs[p]["amount"]) for p in pids)
        return (len(pids), max_amount, -FIELD_ORDER.index(field))

    best = max(helpers, key=rank)
    pids = sorted(helpers[best], key=lambda p: -_amount_max(programs[p]["amount"]))
    refs_out = tuple(
        ProgramRef(p, programs[p]["name_ms"], programs[p]["amount"]) for p in pids)
    meta = FIELD_META[best]
    return FieldNeed(field=best, answer_kind=meta.kind, skippable=meta.skippable,
                     choices=meta.choices, programs=refs_out)


def progress(known: Mapping[str, Any], asked: Iterable[str],
             thresholds: Optional[dict] = None) -> dict:
    """Counts for the live progress signal shown during the grill."""
    data = thresholds or load_thresholds()
    refs = data["reference_values"]
    programs = data["programs"]
    decided = sum(1 for spec in programs.values()
                  if program_status(known, spec, refs) is not None)
    total = len(programs)
    return {"total": total, "decided": decided,
            "undecided": total - decided, "asked": len(set(asked))}


# --- API-boundary helpers --------------------------------------------------------

_TRUE = {"true", "yes", "1", "y"}
_FALSE = {"false", "no", "0", "n"}


def coerce_value(field: str, raw: Any) -> Any:
    """Validate + coerce a structured answer to the field's type. Fail loudly."""
    meta = FIELD_META.get(field)
    if meta is None:
        raise ValueError(f"not an askable field: {field!r}")
    if meta.kind == "boolean":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
        raise ValueError(f"not a boolean answer: {raw!r}")
    if meta.kind == "integer":
        try:
            v = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"not an integer: {raw!r}") from exc
        if v < 0:
            raise ValueError("value must be >= 0")
        return v
    if meta.kind == "money":
        try:
            v = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"not a number: {raw!r}") from exc
        if v < 0:
            raise ValueError("amount must be >= 0")
        return v
    # choice
    s = str(raw)
    if s not in meta.choices:
        raise ValueError(f"{s!r} not one of {meta.choices}")
    return s


def _coerce_field(field: str, raw: Any) -> Any:
    """Coerce ANY Applicant field (askable or not) to its proper type, or raise."""
    if field in FIELD_META:
        return coerce_value(field, raw)
    if field == "household_size":
        try:
            v = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"household_size not an integer: {raw!r}") from exc
        if v < 1:
            raise ValueError("household_size must be >= 1")
        return v
    if field == "ekasih_category":
        if raw in (None, "miskin", "miskin_tegar"):
            return raw
        raise ValueError(f"unknown ekasih_category: {raw!r}")
    raise ValueError(f"unknown field: {field!r}")


def sanitize_facts(raw: Mapping[str, Any]) -> dict:
    """Validate untrusted facts at the API boundary: drop unknown keys and coerce
    each value to its field's type, so the engine never does arithmetic on a string.
    The client echoes `facts` every turn — this is the boundary that makes that safe."""
    allowed = set(Applicant.__dataclass_fields__)  # type: ignore[attr-defined]
    out: dict = {}
    for key, value in (raw or {}).items():
        if key not in allowed:
            continue
        out[key] = _coerce_field(key, value)
    return out


_REASON_MAX = 200


def sanitize_presumptions(raw: Mapping[str, Any], facts: Mapping[str, Any]) -> dict:
    """Validate untrusted presumed facts (LLM-proposed, client-echoed soft facts).

    Presumptions are optional hints, so anything suspect is *dropped*, never raised:
    a lost presumption degrades to "ask the question normally". Hard rules:
      - only askable fields (FIELD_META) — presumptions exist to suppress questions;
      - never money fields — a presumed income could manufacture a false ELIGIBLE;
      - stated facts always win — a presumption cannot shadow what the user said.
    """
    out: dict = {}
    for field, entry in (raw or {}).items():
        meta = FIELD_META.get(field)
        if meta is None or meta.kind == "money" or field in facts:
            continue
        if not isinstance(entry, Mapping):
            continue
        try:
            value = coerce_value(field, entry.get("value"))
        except ValueError:
            continue
        reason = str(entry.get("reason_ms", ""))[:_REASON_MAX]
        out[field] = {"value": value, "reason_ms": reason}
    return out


def with_presumed(facts: Mapping[str, Any], presumed: Mapping[str, Any]) -> dict:
    """The engine's view of the world: presumed values filled in, stated facts on top."""
    merged = {field: entry["value"] for field, entry in presumed.items()}
    merged.update(facts)
    return merged


def to_applicant(facts: Mapping[str, Any]) -> Applicant:
    """Materialise established facts into a validated Applicant for the pipeline.

    Unknown keys are dropped; the individual>household income reconciliation mirrors
    intake so a partial profile doesn't fail validation. Unanswered fields fall back to
    Applicant's (conservative for booleans) defaults.
    """
    allowed = set(Applicant.__dataclass_fields__)  # type: ignore[attr-defined]
    clean = {k: v for k, v in facts.items() if k in allowed and v is not None}
    indiv = float(clean.get("individual_income", 0) or 0)
    house = float(clean.get("household_income", 0) or 0)
    if indiv > house:
        clean["household_income"] = indiv
    return from_dict(clean)
