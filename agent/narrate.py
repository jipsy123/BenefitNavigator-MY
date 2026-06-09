"""EXPLAIN stage — turn deterministic verdicts + cited passages into plain Malay.

The model may only restate the verdicts in (A) and facts from the passages in (B);
it can never change an amount or verdict, nor invent a benefit. The facts block it
receives is also reused verbatim as a grounding source for the groundedness gate.
"""
from __future__ import annotations

from compute.status import Assessment

from . import llm, readability

# Target reading grade for the citizen-facing narrative (low-literacy audience).
_READABILITY_TARGET = 9.0

_SIMPLIFY_SYSTEM = """Tulis semula teks Bahasa Melayu ini supaya LEBIH MUDAH dibaca oleh orang awam tahap rendah:
ayat pendek, perkataan harian, elak istilah sukar.
KEKALKAN setiap fakta, jumlah RM, nama bantuan, dan rujukan sumber dengan tepat.
Jangan tambah maklumat baharu, jangan buang sebarang jumlah atau sumber.
JANGAN guna pemformatan markdown (tiada #, *, **, ---) dan JANGAN guna emoji.
Hasilkan teks biasa (plain text) Bahasa Melayu sahaja."""

# Legitimate, source-independent procedural facts the narrative is allowed to state.
# Reused as a grounding source so genuine "how to apply" guidance isn't flagged.
PROCEDURAL_FACTS_MS = (
    "Bantuan JKM (BTB, EPC, BPT) dimohon di pejabat JKM daerah. "
    "STR dan SARA dimohon melalui portal rasmi MyHASiL (LHDN). "
    "Pendaftaran eKasih dibuat melalui pejabat JKM atau KEMAS. "
    "Kad OKU didaftarkan di pejabat JKM. "
    "Untuk bantuan segera atau kaunseling, hubungi Talian Kasih 15999. "
    "Pemohon boleh menghubungi atau melawati pejabat agensi berkaitan untuk panduan lanjut."
)

_NARRATE_SYSTEM = """Anda ialah peringkat PENERANGAN bagi pembantu bantuan kerajaan untuk rakyat Malaysia.
Tulis dalam Bahasa Melayu mudah (perkataan harian), nada mesra tetapi ringkas.

Anda diberi (A) FAKTA kelayakan MUKTAMAD, (B) PETIKAN SUMBER rasmi, dan (C) FAKTA PROSEDUR yang dibenarkan.

PERATURAN KETAT (untuk mengelak maklumat palsu):
- Guna HANYA maklumat daripada (A), (B), dan (C). Jangan sekali-kali reka bantuan, jumlah RM, syarat, atau prosedur.
- Jangan ubah sebarang jumlah (RM) atau keputusan kelayakan daripada (A). Setiap jumlah RM mesti datang daripada (A).
- JANGAN guna pemformatan markdown (tiada #, *, ---) dan JANGAN guna emoji. Tulis perenggan biasa sahaja.
- Bagi setiap bantuan, sebut sumbernya secara ringkas dalam kurungan, contoh: (Sumber: Garis Panduan JKM, 6.3 BTB).
- Susun jawapan dalam tiga bahagian pendek: (1) Bantuan yang anda sudah layak dan jumlahnya; (2) Bantuan yang hampir layak dan langkah tepat untuk melayakkannya; (3) satu ayat langkah seterusnya.
- Jangan tambah nasihat prosedur yang tiada dalam (C). Jika fakta tidak cukup, minta pemohon hubungi agensi.
- Jangan minta atau ulang nombor MyKad.

Hasilkan teks biasa (plain text) dalam Bahasa Melayu, ringkas dan tepat."""


def build_facts_text(assessment: Assessment) -> str:
    """Deterministic ground-truth block: the LLM's only allowed source for verdicts."""
    lines: list[str] = []
    lines.append("LAYAK:")
    if assessment.eligible:
        for r in assessment.eligible:
            amount = r.amount
            if amount.get("type") == "fixed":
                amt = f"RM{amount['monthly_myr']} sebulan"
            else:
                amt = f"RM{amount.get('monthly_myr_min')}–RM{amount.get('monthly_myr_max')} sebulan"
            cite = f"{r.citation.get('doc_title')}, {r.citation['locator']}"
            lines.append(f"- {r.name_ms} ({r.agency}) — {amt}. (Sumber: {cite})")
    else:
        lines.append("- (tiada bantuan yang layak sepenuhnya berdasarkan maklumat semasa)")

    lines.append("\nHAMPIR LAYAK / TIDAK LAYAK:")
    for g in assessment.gaps:
        tag = "HAMPIR LAYAK" if g.near_miss else "TIDAK LAYAK"
        blockers = "; ".join(g.blocking_ms) or "—"
        actions = " ".join(g.actions_ms) if g.actions_ms else "Hubungi agensi untuk semakan."
        cite = f"{g.citation.get('doc_title')}, {g.citation['locator']}"
        lines.append(f"- [{tag}] {g.name_ms} ({g.agency}) — belum penuhi: {blockers}. "
                     f"Tindakan: {actions} (Sumber: {cite})")
    return "\n".join(lines)


def _passages_block(passages: list[dict], limit: int = 8) -> str:
    rows = []
    for i, p in enumerate(passages[:limit], 1):
        content = " ".join((p.get("content") or "").split())[:500]
        rows.append(f"[{i}] ({p.get('doc_title')}, {p.get('locator')}) {content}")
    return "\n".join(rows) if rows else "(tiada petikan)"


def run_narrate(assessment: Assessment, passages: list[dict],
                assumptions: tuple[str, ...]) -> tuple[str, str]:
    """Return (narrative_ms, facts_text). facts_text is reused for groundedness."""
    facts = build_facts_text(assessment)
    user = (f"(A) FAKTA MUKTAMAD:\n{facts}\n\n"
            f"(B) PETIKAN SUMBER:\n{_passages_block(passages)}\n\n"
            f"(C) FAKTA PROSEDUR DIBENARKAN:\n{PROCEDURAL_FACTS_MS}\n\n"
            f"Andaian intake: {', '.join(assumptions) if assumptions else 'tiada'}")
    narrative = llm.chat_text(_NARRATE_SYSTEM, user, temperature=0.2, max_tokens=1100)

    # SPIKE: simplify until it reads at/under the target grade (no-op if already simple).
    narrative, _ = readability.simplify(
        narrative,
        lambda text, grade: llm.chat_text(_SIMPLIFY_SYSTEM, text, temperature=0.2, max_tokens=1100),
        target_grade=_READABILITY_TARGET,
        max_rounds=2,
    )
    return narrative, facts
