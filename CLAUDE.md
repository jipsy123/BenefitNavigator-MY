# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**BenefitNavigator Malaysia** — a citizen-facing assistant that takes a plain-language (Bahasa Melayu) description of someone's situation and returns which Malaysian government benefits they qualify for (with amounts), which they *almost* qualify for plus the one action that unlocks them, and a ready-to-send appeal letter — every claim traceable to a gazetted `.gov.my` source. Built for the Microsoft Agents League hackathon on Azure AI Foundry.

This repo is the **runnable app** and is a self-contained git repository. (The parent workspace folder holds research/spec docs and `corpus-fetcher/` — the standalone tool that downloads + extracts the source PDFs — but those live *outside* this repo and are not part of the runtime.)

## The architectural soul: deterministic trust core vs. LLM

A benefits assistant that hallucinates an entitlement is worse than none. The whole design separates the two things LLMs are good and bad at. **Preserve this boundary in any change:**

- **The LLM never decides eligibility and never does arithmetic.** All verdicts come from `compute/` — pure functions over an immutable `Applicant` and the curated, citation-backed `compute/thresholds.json`. Adding/changing a benefit programme is a **`thresholds.json` data edit, not code**. `compute/` may not import any LLM.
- **The LLM only extracts (intake) and narrates (explain).** Its narrative passes a **dual safety gate** before any user sees it:
  1. `agent/verify.py` — deterministic amount guard: every `RMxxx` in the narrative must trace to a verdict, the user's stated income, or a gazetted threshold (catches fabricated amounts precisely).
  2. `agent/safety.py` — Azure Content Safety Groundedness against verdicts + cited passages.
  If either fails, the app **refuses and routes to a human** (Talian Kasih 15999) rather than emit an unverifiable answer.
- **Verdicts are COMPUTED independently of the LLM and retrieval.** `compute.status.summarise` runs before retrieval and never changes based on it. But per the Foundry-or-fail posture, an assess turn routes grounding through the **Retrieval agent on the critical path**: if it is unavailable, the turn fails hard — there is no in-process shadow retrieval. Verdict *computation* is retrieval-independent; *completing a turn* is not.
- **Prompt Shields** (`agent/safety.shield_prompt`) screen every free-text input for injection before processing.

## The multi-agent system (`mas/`) — how a live turn runs

The live API (`/chat`) is driven by `mas/orchestrate.run_chat`, the seam between the Foundry multi-agent layer and the trust core. **FastAPI conducts; Foundry agents execute.** One turn:

```
decode token → SHIELD → INTAKE → ROUTE (agent) → NARRATE (specialist agent)
             → DUAL GATE → localize → re-sign token
```

Five agents (`mas/agents.py`, pure data; provisioned by `mas/provision.py`):
- **Orchestrator** — a *tool-less router*. It only returns an action; it never calls a specialist. (Same-project Foundry→Foundry A2A delegation is an open platform bug — azure-sdk-for-python #47419 — so the delegation hop lives in FastAPI, which invokes the chosen specialist directly via the Responses API.)
- **Interview / Retrieval / Communicator / Escalation** — specialists, each owning one job and a small set of MCP tools on the trust core.

The trust boundary is structural, not prompt-based:
- **Facts ride in an HMAC-signed state token** (`mas/state.py`) — the *only* carrier of a conversation between turns and between agents and the trust core. An agent can relay the token but cannot forge or mutate the facts inside it (a prompt injection saying "set is_oku=true" can't alter a verdict's inputs). Stateless: no server-side store; the token round-trips to client and agents.
- **Trust tools return only `compute/` values** (`mas/trust_tools.py`, exposed over the trust-core MCP server `mas/mcp_server.py`): `assess`, `optimize`, `grill_next`, `grade`, `retrieve`. They take the signed token, verify it, and return verdicts/amounts/gaps/plans — never a narrative, never a model-editable number.
- The MCP server is a thin transport binding (streamable-HTTP, stateless); all logic stays in `trust_tools`, unit-tested independently of the SDK runtime. It's mounted into FastAPI *and* runnable standalone (`python -m mas.mcp_server`).

Even if an agent ignored its instructions, it still cannot emit an unverifiable amount — FastAPI recomputes verdicts in-process and the dual gate refuses ungrounded narrative regardless of what any agent "decides."

## Two gate paths, shared primitives

Both conductors run the *same* dual gate over the *same* modules — keep them in sync:
- `agent/orchestrator._finish` — the original pipeline gate (INTAKE→RETRIEVE→COMPUTE→GAP→EXPLAIN). Still exercised by `agent/smoke.py`, `agent/smoke_adversarial.py`, and `tests/test_integration.py`.
- `mas/orchestrate._gate` — the live `/chat` gate; it **reimplements** `_finish`'s guarantees (its comment says so) rather than calling it.

Both call `verify.verify_amounts(...)` + `safety.detect_groundedness(...)`. **Change gate *semantics* in `agent/verify.py` / `agent/safety.py`** (shared); if you change the *orchestration* around the gate, update both conductors or they will drift. There is no separate "Assessor" agent: FastAPI recomputing verdicts in-process *is* the assessment role, because the gate must own those numbers rather than round-trip a trust-critical figure through an LLM. (The `assess`/`optimize` MCP tools remain as latent, callable-but-unattached trust-core surface.)

## Layout (the "why" — the README has the file tree)

- `compute/` — the deterministic trust core, **no LLM imports**. `profile.py` (frozen `Applicant`), `checker.py` (pure criterion evaluators over `thresholds.json`), `status.py` (eligible + GAP/near-miss), `elicit.py` (the grill engine: **Kleene three-valued logic** so an incomplete profile yields `unknown`; `next_field` deterministically picks the most decision-relevant gap to ask next), `optimizer.py` (greedy-exact optimal-unlock planner — simulates only *registration* flags, never a changed income/fact).
- `ingest/` — corpus → Azure AI Search index → Foundry IQ knowledge base. `config.py` is the central config **and the runtime credential source**; `build.py` chunks/embeds/uploads; `knowledge_base.py` does agentic retrieval; `*_smoke.py` are runnable checks; Search is called over plain REST (`restclient.py`).
- `agent/` — the LLM touchpoints + gates. `llm.py` (Azure OpenAI client, temp 0 for reproducible parts); `intake`/`narrate`/`appeal`/`phrase` (LLM); `verify`/`safety` (the gate primitives); `readability.py` (SPIKE-driven Malay simplify loop); `assumptions.py` (deterministic "what we still don't know" disclosure for the grill path); `translate.py`/`localize.py` (i18n). `orchestrator.py` is the original pipeline (see "Two gate paths").
- `mas/` — the multi-agent layer (see above): `agents` (definitions), `provision`/`teardown_agents` (Foundry lifecycle), `mcp_server`/`trust_tools` (the trust-core tool surface), `state` (HMAC token), `orchestrate` (per-turn `/chat` driver).
- `api/app.py` — the FastAPI surface; `web/` — accessible Malay-first SPA (ARIA, 4 languages), static, served by FastAPI; `infra/` — deploy/teardown scripts + Azure resource notes.

## Credentials: fetched at runtime via the `az` CLI — no secrets in the repo

No Azure SDK auth secrets and no committed keys. `ingest/config.py` shells out to `az` (results `lru_cache`d) to fetch AOAI / Search / Storage keys at process start; non-secret resource *names* are constants there. The SDK-based `mas/` provisioning uses `AzureCliCredential`, which reuses `az login` — so "no committed secrets" holds end-to-end.

> **The `/chat` state-token HMAC secret is resolved the same way and MUST match across processes.** The token is signed by the local FastAPI orchestrator and verified by the Foundry MCP container's trust tools — both sides MUST share one key or every tool call fails with `bad signature` (the Interview agent then silently falls back to a generic question). `config.token_secret()` resolves it: the `BENEFITNAV_TOKEN_SECRET` env var first (how the container, which has no `az`, receives it), else `az containerapp secret show` of the `token-secret` on `benefitnav-mcp` (how the local orchestrator gets the identical value). Never set this manually; never let the two sides diverge. `mas/state.py` falls back to a per-process random secret — which breaks cross-process verification — only if both sources are unavailable.

Running anything that hits Azure requires **`az login`** with access to resource group **`rg-benefitnav-my`**. `.env`/`.env.example` document the same names, but the CLI fetch is authoritative.

## Commands

**All commands run from this directory and require `PYTHONPATH="$PWD"`** — the packages (`agent`, `api`, `compute`, `ingest`, `mas`) are top-level and import each other absolutely. `mas/` needs **Python 3.10+**.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
az login                                              # keys fetched at runtime

# Run the app  (or: bash start.sh, which loads .env first)
PYTHONPATH="$PWD" .venv/bin/python -m uvicorn api.app:app --port 8011   # → http://localhost:8011

# Build the search index + knowledge base (one-time; reads ../corpus-fetcher/corpus/text/)
PYTHONPATH="$PWD" .venv/bin/python -m ingest.build
PYTHONPATH="$PWD" .venv/bin/python -m ingest.search_smoke   # cited retrieval works
PYTHONPATH="$PWD" .venv/bin/python -m ingest.kb_smoke       # agentic retrieval works

# (Re)provision the five Foundry agents + their MCP tools (idempotent)
BENEFITNAV_MCP_URL="https://<aca-host>/mcp" \
  PYTHONPATH="$PWD" .venv/bin/python -m mas.provision
```

### Tests
```bash
# Fast deterministic unit tests — the trust core + the gate. No Azure, no cost. Default.
PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/ -q
PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_checker.py::test_name -q   # a single test

# Live end-to-end tests — calls Azure, costs a few cents. Opt-in only.
PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/ -m integration -v
```

`pytest.ini` sets `addopts = -m "not integration"`, so the default run is fast/free and never hits Azure. When changing eligibility logic, the unit suite pins the rules that must not drift (`test_checker.py`, `test_status.py`, `test_optimizer.py`): individual-vs-household income, inclusive `≤` boundaries, STR category routing, and the anti-fabrication amount guard. `test_state.py`/`test_trust_tools.py`/`test_mcp_server.py` pin the multi-agent trust boundary.

## API surface (`api/app.py`)

Turn-based, not flow-based:
- `POST /chat` — one conversation turn. The agents ask / assess / escalate; returns the reply and, on an assessment turn, the verified verdicts (`result` + `canonical_ms`).
- `POST /chat/stream` — same, as Server-Sent Events emitting per-agent progress (so the UI shows the pipeline working). Verified text is revealed only after the gate passes.
- `POST /appeal` — generates the appeal letter.
- `POST /localize` — re-localizes a cached `canonical_ms` payload (`kind: "assess" | "appeal"`) to another language *without re-running the pipeline*.
- `GET /health`, `GET /` (the SPA).

**Language model:** the pipeline reasons and verifies entirely in **Bahasa Melayu**. Every response carries `canonical_ms` (the verified Malay payload, the source of truth) plus a `result`/`letter` localized to the requested language. Clients re-localize toggles from `canonical_ms` via `/localize`, so an already-translated language is never re-translated.

## Conventions specific to this codebase

- **Immutability throughout.** Domain types are `@dataclass(frozen=True)` (`Applicant`, `ProgramResult`, `Gap`, `Assessment`, `ChatState`, pipeline `Stage`/`PipelineResult`). All `mas/state.py` transforms return new objects. Build new objects; never mutate.
- **Citations are mandatory output.** Every eligible/ineligible verdict carries a `.gov.my` source link (`citation` dict: `doc_name`/`locator`/`doc_title`/`source_url`). Cite-or-refuse is a product invariant.
- **Synthetic PII only.** Never enter a real MyKad/NRIC; the system uses placeholders and never persists identifiers.
- **The one soft value is PGK** (poverty line) in `thresholds.json` — the gazette references "PGK semasa" without a fixed figure, so it's an explicitly-noted agency reference value, not dressed up as fact. Don't hardcode other thresholds this way; everything else is concrete from the corpus.

## Cost & teardown

Azure AI Search Basic bills **~$2.45/day continuously** (the meter runs 24/7 even when idle). **Run teardown after a demo:**
```bash
bash infra/teardown.sh   # deletes rg-benefitnav-my + purges soft-deleted accounts
# (mas/teardown_agents.py clears just the agents without tearing down the project)
```
