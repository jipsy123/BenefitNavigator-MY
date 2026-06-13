# BenefitNavigator Malaysia — 5-minute demo script

**Goal:** show that an AI benefits assistant can be *trustworthy* — grounded, deterministic where it matters, and safe enough to put in front of vulnerable citizens.

## Setup (before you present)
```bash
cd benefitnav
PYTHONPATH="$PWD" .venv/bin/python -m uvicorn api.app:app --port 8011
# open http://localhost:8011
```
Have a terminal ready for the unit-test moment.

---

## Beat 1 — The problem (30s)
> "Millions of eligible Malaysians never claim benefits they're entitled to. The rules live in dense JKM, PERKESO and LHDN PDFs. Ask 'what am I eligible for?' and there's no single answer. We built an assistant that gives that answer — in plain Malay, from official sources, and that refuses to guess."

## Beat 2 — A real query (60s)
In the UI, click the **OKU persona**:
> *"Saya OKU dan tidak boleh bekerja. Saya ada Kad OKU JKM, tiada pendapatan sendiri, keluarga RM1,500/bulan, 4 orang. Umur 35."*

Click **Semak Kelayakan**. While it runs, narrate the **pipeline trace** lighting up:
> "Watch the pipeline: input is screened by Prompt Shields, gpt-4o extracts a structured profile, Foundry IQ does *agentic retrieval* — it decomposed my question into sub-queries across JKM, Akta OKU and PERKESO — then the deterministic checker computes eligibility."

Result lands:
- **"Anda mungkin layak ~RM300/bulan."**
- **BTB RM250/bulan** (eligible) + **STR Bujang RM50/bulan**, each with a **clickable .gov.my citation**.
- A **near-miss**: SARA — "register eKasih to unlock it."

## Beat 3 — Why you can trust it (90s) ← the core
Point at the **green "Disahkan dengan sumber rasmi" badge**:
> "The LLM never decided this. A deterministic Python checker did the math from a citation-backed rules file. And before you saw a single word, the narrative passed two gates."

Switch to the terminal and show the guard catching a hallucination:
```bash
PYTHONPATH="$PWD" .venv/bin/python -m agent.smoke_adversarial
```
> "Injection input — blocked. A narrative that fabricates 'RM9000/month + free car' — the amount guard catches RM9000 as untraceable, and Content Safety scores it ungrounded. The app refuses and routes to Talian Kasih 15999 rather than lie to a citizen."

Then the trust core, instantly:
```bash
PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/ -q
```
> "21 deterministic tests pin the actual legal rules — including that BTB checks the person's *own* income, not household, so we never wrongly disqualify an unemployed OKU in a working family."

## Beat 4 — The whole journey (45s)
Back in the UI, on the SARA near-miss click **Draf Surat Rayuan**:
> "It drafts a formal appeal letter in Malay, quoting the exact rule, with the correct submission route — placeholders for personal details, never fabricated."

Toggle the **language selector to English / 中文 / தமிழ்**:
> "Same grounded answer, four languages — because the people who most need this don't all read Malay officialese."

## Beat 5 — Close (15s)
> "Built on Azure AI Foundry: gpt-4o, Foundry IQ agentic retrieval, Content Safety, Translator. Adding a new benefit is a JSON edit, not a rewrite. Trustworthy by construction."

---

## If asked
- **"Where do the numbers come from?"** Every threshold is concrete from the gazetted corpus, except the poverty line (PGK) which the source itself references as "current" — so it's an explicit agency-configured value, not a guess.
- **"What if retrieval fails?"** Verdicts are computed independently of retrieval (`compute.summarise` runs first), but under fail-hard, the assess turn ends with `action="error"` if the Retrieval agent is unavailable — we don't substitute a locally-fetched answer. The deterministic verdict cards still reach the citizen as part of the error context.
- **"Latency?"** ~10–20s per assessment (agentic retrieval + groundedness check). The trust guarantees are worth the seconds.
