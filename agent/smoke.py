"""End-to-end orchestrator smoke test on a synthetic Malay persona.

Run:  python -m agent.smoke
"""
from __future__ import annotations

import json

from . import orchestrator

PERSONA = (
    "Saya seorang OKU dan tidak boleh bekerja langsung. Saya tiada pendapatan "
    "sendiri. Saya tinggal dengan keluarga, jumlah pendapatan keluarga sekitar "
    "RM1,500 sebulan untuk 4 orang. Saya ada Kad OKU JKM. Saya berumur 35 tahun. "
    "Bantuan bulanan apa yang saya layak?"
)


def run() -> None:
    result = orchestrator.run(PERSONA)

    print("=== STAGES ===")
    for s in result.stages:
        print(f"  [{s['status'].upper():8}] {s['name']:12} {s['summary']}")

    print(f"\n=== PROFILE (extracted) ===\n  {json.dumps(result.profile, ensure_ascii=False)}")

    print(f"\n=== ELIGIBLE ({len(result.eligible)}), floor RM{result.total_monthly_min}/mo ===")
    for e in result.eligible:
        print(f"  + {e['name_ms']} — {e['amount']}  ({e['citation']['locator']})")

    print(f"\n=== GAPS ({len(result.gaps)}) ===")
    for g in result.gaps:
        tag = "NEAR-MISS" if g["near_miss"] else "no"
        print(f"  - [{tag}] {g['name_ms']}: {g['blocking_ms']}")

    print(f"\n=== GROUNDEDNESS === {result.groundedness}")
    print(f"\n=== MESSAGE (Bahasa Melayu) ===\n{result.message_ms}")

    assert result.ok, "pipeline refused unexpectedly"
    ids = {e["program_id"] for e in result.eligible}
    assert "jkm_btb" in ids, f"expected BTB eligible, got {ids}"
    assert result.groundedness["grounded"], "narrative not grounded"
    assert "RM250" in result.message_ms or "250" in result.message_ms, "amount missing"
    print("\nPASS: end-to-end grounded assessment with correct BTB verdict.")


if __name__ == "__main__":
    run()
