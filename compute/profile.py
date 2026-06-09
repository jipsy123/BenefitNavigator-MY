"""Applicant profile — the validated, immutable input to the deterministic checker.

This is a system boundary: every field is validated here so the checker downstream
can trust its inputs. Synthetic data only — never persist a real NRIC/MyKad.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

MARITAL_STATUSES = ("single", "married", "widowed", "divorced")
EKASIH_CATEGORIES = (None, "miskin_tegar", "miskin")


@dataclass(frozen=True)
class Applicant:
    """An immutable snapshot of one applicant's circumstances.

    Income fields are RM/month. `individual_income` is the applicant's OWN income;
    `household_income` is the whole household's — these are distinct eligibility
    concepts (see thresholds.json) and must never be conflated.
    """
    citizen: bool = True
    age: int = 0
    marital_status: str = "single"

    # Disability
    is_oku: bool = False           # has a disability (the underlying fact)
    has_kad_oku: bool = False      # holds a registered JKM Kad OKU (registration)
    unable_to_work: bool = False
    is_working: bool = False
    is_carer: bool = False         # full-time carer of a bedridden OKU/patient
    has_dependents: bool = False   # has child(ren)/dependents (STR Isi Rumah vs Bujang)

    # Income (RM/month)
    individual_income: float = 0.0
    household_income: float = 0.0
    household_size: int = 1

    # Cash-transfer registration state
    str_approved: bool = False
    ekasih_listed: bool = False
    ekasih_category: Any = None

    def with_changes(self, **changes: Any) -> "Applicant":
        """Return a new Applicant with changes applied (immutability helper)."""
        return validate(replace(self, **changes))


def validate(applicant: Applicant) -> Applicant:
    """Fail fast on out-of-range / inconsistent input. Returns the same object."""
    if applicant.age < 0 or applicant.age > 130:
        raise ValueError(f"age out of range: {applicant.age}")
    if applicant.marital_status not in MARITAL_STATUSES:
        raise ValueError(f"unknown marital_status: {applicant.marital_status!r}")
    if applicant.individual_income < 0:
        raise ValueError("individual_income must be >= 0")
    if applicant.household_income < 0:
        raise ValueError("household_income must be >= 0")
    if applicant.household_size < 1:
        raise ValueError("household_size must be >= 1")
    if applicant.ekasih_category not in EKASIH_CATEGORIES:
        raise ValueError(f"unknown ekasih_category: {applicant.ekasih_category!r}")
    if applicant.individual_income > applicant.household_income + 1e-6:
        raise ValueError("individual_income cannot exceed household_income")
    return applicant


def from_dict(data: dict[str, Any]) -> Applicant:
    """Build a validated Applicant from untrusted (e.g. API/JSON) input.

    Unknown keys are rejected so typos in the intake surface immediately rather
    than silently defaulting (which could flip an eligibility verdict).
    """
    allowed = {f for f in Applicant.__dataclass_fields__}  # type: ignore[attr-defined]
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown profile field(s): {sorted(unknown)}")
    # ekasih_listed implies a category; default to 'miskin' if listed without one.
    merged = dict(data)
    if merged.get("ekasih_listed") and not merged.get("ekasih_category"):
        merged["ekasih_category"] = "miskin"
    return validate(Applicant(**merged))
