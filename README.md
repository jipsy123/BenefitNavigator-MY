# BenefitNavigator Malaysia 🇲🇾

**Find the government benefits you're actually entitled to — explained in plain Bahasa Melayu, with every claim traceable to a gazetted source, and a safety net that refuses to guess.**

Millions of eligible Malaysians never claim benefits they qualify for — the rules are scattered across JKM, PERKESO and LHDN documents, written in dense officialese, and "Am I eligible?" has no single answer. BenefitNavigator takes a citizen's plain-language description of their situation and returns: which benefits they already qualify for (with amounts), which they *almost* qualify for and the one action that would unlock them, and a ready-to-send appeal letter — all grounded in official sources.

Built for the Microsoft Agents League hackathon on **Azure AI Foundry**.

---

## Why this is trustworthy (the core idea)

A benefits assistant that can *hallucinate an entitlement* is worse than no assistant. So the architecture separates the two things LLMs are good and bad at:

- **The LLM never decides eligibility or does arithmetic.** A deterministic Python checker (`compute/`) reads a curated, **citation-backed** rules file (`thresholds.json`) and computes every verdict. The rules encode the *legal income concept* per program (individual vs household income are distinct), with inclusive `≤` boundaries — pinned by unit tests.
- **The LLM only narrates and extracts**, and its narrative passes a **dual safety gate** before a user ever sees it:
  1. a **deterministic amount guard** — every `RMxxx` in the answer must trace to a verdict, the user's stated income, or a gazetted threshold (catches a fabricated "RM9000/month" precisely);
  2. **Azure Content Safety Groundedness** detection against the verdicts + cited passages.
  If either fails, the app **refuses and routes to a human** (Talian Kasih 15999) instead of emitting an unverifiable answer.
- **Prompt Shields** screen every input for jailbreak/injection before processing.
- **Cite-or-refuse:** every eligible/ineligible verdict carries a `.gov.my` source link.

---

## The 5-stage pipeline

```
user text (Bahasa Melayu)
   │
   ▼  Prompt Shields  ──► blocked? refuse
INTAKE      gpt-4o extracts a validated Applicant profile (facts only, no judgement)
   ▼
RETRIEVE    Foundry IQ agentic retrieval — gpt-4o decomposes the query into sub-queries,
            hybrid (BM25 + vector) search + semantic rerank, returns cited extractive passages
   ▼
COMPUTE     deterministic checker → eligibility verdict + amount per program  (the source of truth)
   ▼
GAP         which near-miss benefits unlock with one registration action
   ▼
EXPLAIN     gpt-4o writes plain-Malay narrative ─► amount guard + groundedness gate ─► refuse or emit
```

Every stage emits a trace entry, so the UI can *show* the pipeline working — transparency is a feature.

---

## Azure architecture

| Layer | Service | Role |
|---|---|---|
| Reasoning | **Azure OpenAI gpt-4o** (Foundry) | intake extraction, query planning, narration |
| Knowledge (IQ) | **Azure AI Search (Basic)** + Foundry IQ knowledge base | agentic retrieval, hybrid + semantic, extractive citations |
| Embeddings | **text-embedding-3-large** (3072-dim) | index + query-time vectorization |
| Safety | **Azure AI Content Safety** | Prompt Shields + Groundedness detection |
| Language | **Azure AI Translator** | BM ↔ EN / 中文 / தமிழ் |
| Corpus | **Azure Blob Storage** | source-of-truth `.gov.my` PDFs |

All on one **AIServices** multi-service resource (`swedencentral`) + one Search service. Keys are fetched at runtime via the `az` CLI — never committed.

### Data sources (corpus)
Six machine-readable `.gov.my` documents, chunked with citation-first locators (Akta *Seksyen N*, FAQ *S{n}*, guideline headings):

- JKM Garis Panduan Pengurusan Bantuan Kewangan Persekutuan (2018) — BTB, EPC, BPT, BOT
- PERKESO booklet (2025) — Return-To-Work
- Akta OKU 2008 (Act 685)
- LHDN STR 2026 application FAQ, STR 2026 payment FAQ, SARA 2026 FAQ

---

## Setup

**Prerequisites:** Python 3.9+, Azure CLI logged in (`az login`) with access to the resource group `rg-benefitnav-my`. Provisioning of the Azure resources is recorded in [`infra/azure-resources.md`](infra/azure-resources.md).

```bash
cd benefitnav
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
az login                     # keys are fetched at runtime via the CLI
```

### Build the index + knowledge base (one-time)
```bash
PYTHONPATH="$PWD" .venv/bin/python -m ingest.build         # chunk → embed → index (400 chunks)
PYTHONPATH="$PWD" .venv/bin/python -m ingest.search_smoke  # Step A: cited retrieval works
PYTHONPATH="$PWD" .venv/bin/python -m ingest.kb_smoke      # Step B: agentic retrieval works
```

### Run
```bash
PYTHONPATH="$PWD" .venv/bin/python -m uvicorn api.app:app --port 8011
# open http://localhost:8011
```

> **Deployed:** the conductor runs in Azure as the `benefitnav-api` Container App (see `infra/azure-resources.md`); `bash infra/deploy-api.sh` builds and ships it. Running locally with the command above is now **dev-only** and optional — the live demo is fully cloud-hosted (Foundry agents + the trust-core MCP app + this conductor), with no laptop in the loop: <https://benefitnav-api.ashyocean-f47e8ddf.swedencentral.azurecontainerapps.io>

---

## Tests

```bash
# fast, deterministic unit tests (no Azure, no cost) — the trust core
PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/ -q

# live end-to-end tests (calls Azure; a few cents)
PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/ -m integration -v
```

The unit suite pins the eligibility rules: individual-vs-household income, inclusive
boundaries, SARA OR-logic, STR category routing, and the anti-fabrication amount guard.

---

## Project structure

```
benefitnav/
├── ingest/          corpus → index → knowledge base (Steps A & B)
│   ├── chunker.py   citation-first chunking (Seksyen / S{n} / headings)
│   ├── index_schema.py  hybrid index (ms.microsoft analyzer + vectorizer + semantic)
│   ├── build.py     chunk → embed → upload
│   └── knowledge_base.py  Foundry IQ agentic retrieval
├── compute/         the deterministic trust core (no LLM)
│   ├── thresholds.json  curated, citation-backed rules
│   ├── profile.py   validated immutable Applicant
│   ├── checker.py   pure eligibility evaluators
│   └── status.py    STATUS-CHECK + GAP analysis
├── agent/           the orchestrated pipeline
│   ├── intake.py · narrate.py · appeal.py
│   ├── safety.py    Prompt Shields + Groundedness
│   ├── verify.py    deterministic amount guard
│   ├── readability.py  SPIKE Malay readability + simplify loop
│   ├── translate.py i18n
│   └── orchestrator.py  the 5-stage pipeline
├── api/app.py       FastAPI: /assess, /appeal, /health
├── web/             accessible single-page UI (BM default, ARIA, 4 languages)
└── tests/           unit (fast) + integration (live)
```

---

## Cost & teardown

Pay-per-token services are trivial; **Azure AI Search Basic bills ~$2.45/day continuously**. Total project budget: $50.

```bash
bash infra/teardown.sh    # deletes the resource group + purges soft-deleted accounts
```

⚠️ **Run teardown after the demo** — the Search meter runs 24/7.

---

## Honest limitations

- **PGK is the one configurable value.** Every threshold is concrete from the corpus *except* the poverty line (PGK), which the gazetted JKM guideline references as "PGK semasa" without a fixed figure (it updates annually). It lives in `thresholds.json` as an agency-set reference value with an explicit note — not dressed up as fact.
- **MVP scope:** JKM disability (BTB/EPC/BPT/BOT) + LHDN STR/SARA. PERKESO RTW and housing (KPKM) are in the corpus for retrieval but not yet in the deterministic checker — adding a program is a `thresholds.json` edit, not code.
- **Synthetic PII only.** Never enter a real MyKad/NRIC; the system uses placeholders and never persists identifiers.
- **Readability is a relative signal.** SPIKE drives a rewrite loop that measurably lowers reading difficulty (e.g. ~12.3 → ~9.7 on a typical narrative), but the Flesch-Kincaid coefficients are English-calibrated and Malay is more agglutinative, so the *absolute* grade is overstated — trust the reduction, not the number.
- **i18n translates the explanation, not the chrome.** The generated assessment narrative is translated to EN / 中文 / Tamil on demand; the UI labels themselves are Malay-first (this is a Malay-first service for Malaysians).
- gpt-4o runs on Azure **GlobalStandard routing** (quota reality on the trial subscription) — there is no data-residency-in-Malaysia claim.
```
