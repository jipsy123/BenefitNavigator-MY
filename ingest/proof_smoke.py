"""Live smoke: every programme's verdict must resolve to the CORRECT proof passage.

Exercises the real retrieval path against the live index (no LLM). For each programme in
thresholds.json, build its citation, fetch the proving passage, and assert it contains the
clause that actually proves THAT programme — not just any non-empty text.

Why correctness, not just non-empty: the JKM EPC/BTB/BPT clauses are near-duplicates
("Warganegara... Pemegang Kad OKU JKM...") and their semantic rerank scores sit ~0.02 apart
(measured live 2026-06-14). A non-empty check would pass even if EPC resolved to BTB's
clause — displaying the wrong section as proof. The markers below pin each programme to its
own discriminating fact, so a rerank flip fails the smoke instead of shipping a mismatch.

Run:  PYTHONPATH="$PWD" .venv/bin/python -m ingest.proof_smoke
"""
from __future__ import annotations

from compute import checker
from ingest import knowledge_base as kb

# Verbatim facts that distinguish each programme's proof clause (case-insensitive).
_MARKERS = {
    "jkm_epc": ["1,200"],                    # EPC: income <= RM1,200 + works
    "jkm_btb": ["Tidak berupaya bekerja"],   # BTB: unable to work
    "jkm_bpt": ["3,000"],                    # BPT: carer, household income <= RM3,000
    "str_household": ["STR"],
    "str_bujang": ["STR"],
    "sara": ["SARA"],
}


def run() -> None:
    data = checker.load_thresholds()
    failures: list[str] = []
    for pid, spec in data["programs"].items():
        cit = checker._resolve_citation(spec["citation"])
        cit["name_ms"] = spec["name_ms"]
        proof = kb.fetch_passage(cit)
        passage = (proof or {}).get("passage", "")
        markers = _MARKERS.get(pid, [])
        ok = bool(passage) and all(m.lower() in passage.lower() for m in markers)
        print(f"[{'OK ' if ok else 'BAD'}] {pid}: {cit['locator']}")
        print(f"        {passage[:160]}")
        if not ok:
            missing = [m for m in markers if m.lower() not in passage.lower()]
            print(f"        MISSING: {missing or '(empty passage)'}")
            failures.append(pid)
    if failures:
        raise SystemExit(f"FAIL: wrong/empty proof passage for: {failures}")
    print(f"\nPASS: all {len(data['programs'])} programmes resolve to their CORRECT clause.")


if __name__ == "__main__":
    run()
