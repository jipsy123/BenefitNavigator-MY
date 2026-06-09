"""Deterministic eligibility checker — the trust core.

Pure functions over an immutable Applicant + the curated thresholds.json. The LLM
never runs this code and never does the arithmetic; it only narrates these verdicts.
Each criterion is evaluated independently and carries its own pass/fail + (for
registration gates) an actionable next step, so GAP analysis is exact, not inferred.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ingest import sources

from .profile import Applicant

THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.json"


@dataclass(frozen=True)
class CriterionResult:
    label_ms: str
    category: str                      # "income" | "registration" | "fact"
    passed: bool
    gap_action_ms: Optional[str] = None


@dataclass(frozen=True)
class ProgramResult:
    program_id: str
    name_ms: str
    agency: str
    eligible: bool
    amount: dict
    criteria: tuple[CriterionResult, ...]
    citation: dict                     # {doc_name, locator, doc_title, source_url}

    @property
    def met(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.passed]

    @property
    def unmet(self) -> list[CriterionResult]:
        return [c for c in self.criteria if not c.passed]


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --- criterion evaluators: (applicant, criterion, refs) -> bool -----------------

def _resolve_threshold(crit: dict, refs: dict) -> float:
    if "ref" in crit:
        return float(refs[crit["ref"]]["value"])
    return float(crit["threshold"])


_EVALUATORS: dict[str, Callable[[Applicant, dict, dict], bool]] = {
    "citizen": lambda a, c, refs: a.citizen,
    "age_gte": lambda a, c, refs: a.age >= c["value"],
    "flag_true": lambda a, c, refs: bool(getattr(a, c["flag"])),
    "marital_in": lambda a, c, refs: a.marital_status in c["values"],
    # STR Isi Rumah = married OR a single-parent with dependents.
    "household_category": lambda a, c, refs: a.marital_status == "married" or a.has_dependents,
    # STR Bujang = no spouse AND no dependents.
    "bujang_category": lambda a, c, refs: a.marital_status != "married" and not a.has_dependents,
    # "dan ke bawah" => inclusive (<=)
    "individual_income_lte": lambda a, c, refs: a.individual_income <= _resolve_threshold(c, refs),
    "household_income_lte": lambda a, c, refs: a.household_income <= _resolve_threshold(c, refs),
    # STR Bujang: >=21, OR >=18 if a JKM-registered OKU
    "str_bujang_age": lambda a, c, refs: a.age >= 21 or (a.age >= 18 and a.is_oku and a.has_kad_oku),
}


def _evaluate_criterion(applicant: Applicant, crit: dict, refs: dict) -> CriterionResult:
    evaluator = _EVALUATORS.get(crit["type"])
    if evaluator is None:
        raise ValueError(f"unknown criterion type: {crit['type']!r}")
    return CriterionResult(
        label_ms=crit["label_ms"],
        category=crit["category"],
        passed=bool(evaluator(applicant, crit, refs)),
        gap_action_ms=crit.get("gap_action_ms"),
    )


def _resolve_citation(citation: dict) -> dict:
    doc = sources.DOCS.get(citation["doc_name"], {})
    return {
        "doc_name": citation["doc_name"],
        "locator": citation["locator"],
        "doc_title": doc.get("title"),
        "source_url": doc.get("source_url", ""),
    }


def evaluate_program(applicant: Applicant, program_id: str, spec: dict,
                     refs: dict) -> ProgramResult:
    crits = tuple(_evaluate_criterion(applicant, c, refs) for c in spec["criteria"])
    if spec.get("logic", "all") == "any":
        eligible = any(c.passed for c in crits)
    else:
        eligible = all(c.passed for c in crits)
    return ProgramResult(
        program_id=program_id,
        name_ms=spec["name_ms"],
        agency=spec["agency"],
        eligible=eligible,
        amount=spec["amount"],
        criteria=crits,
        citation=_resolve_citation(spec["citation"]),
    )


def assess(applicant: Applicant, thresholds: Optional[dict] = None) -> list[ProgramResult]:
    """Evaluate every program. Eligible programs sort first (then by agency/name)."""
    data = thresholds or load_thresholds()
    refs = data["reference_values"]
    results = [evaluate_program(applicant, pid, spec, refs)
               for pid, spec in data["programs"].items()]
    results.sort(key=lambda r: (not r.eligible, r.agency, r.name_ms))
    return results


def to_dict(result: ProgramResult) -> dict[str, Any]:
    """JSON-serialisable view for the API / agent context."""
    return {
        "program_id": result.program_id,
        "name_ms": result.name_ms,
        "agency": result.agency,
        "eligible": result.eligible,
        "amount": result.amount,
        "citation": result.citation,
        "met": [c.label_ms for c in result.met],
        "unmet": [{"label_ms": c.label_ms, "category": c.category,
                   "gap_action_ms": c.gap_action_ms} for c in result.unmet],
    }
