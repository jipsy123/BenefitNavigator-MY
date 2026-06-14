"""The assess payload must attach each fetched proof passage to its citation — on the
per-programme cards AND the deduped top-level citations list — keyed by (doc_name, locator)."""
from compute.profile import Applicant
from compute.status import summarise
from mas import orchestrate


def _applicant():
    return Applicant(citizen=True, has_dependents=True, household_income=2000)


def test_assessment_payload_attaches_passages_to_citations():
    applicant = _applicant()
    assessment = summarise(applicant)
    # One proof per citation the verdicts carry (mirrors what prove() returns), keyed by
    # (doc_name, locator) — built straight off the assessment, no token round-trip needed.
    proofs, seen = [], set()
    for r in list(assessment.eligible) + list(assessment.gaps):
        c = r.citation
        key = (c.get("doc_name"), c.get("locator"))
        if key in seen or not c.get("source_url"):
            continue
        seen.add(key)
        proofs.append({"doc_name": c["doc_name"], "locator": c["locator"],
                       "doc_title": c.get("doc_title"), "source_url": c.get("source_url"),
                       "passage": f"PROOF::{c['locator']}"})

    payload = orchestrate._assessment_payload("naratif", applicant, assessment, {}, proofs)

    assert payload["citations"], "expected at least one citation"
    assert all(c["passage"].startswith("PROOF::") for c in payload["citations"])
    for prog in payload["eligible"]:
        assert prog["citation"]["passage"].startswith("PROOF::")
    for gap in payload["gaps"]:
        if gap["citation"].get("source_url"):
            assert gap["citation"]["passage"].startswith("PROOF::")
