# Azure Resources — BenefitNavigator Malaysia

**Provisioned:** 2026-06-09 · **Subscription:** Azure subscription 1 (`REDACTED`) · **Type:** FreeTrial (spending limit ON)

> Spending limit ON = hard-stops at credit exhaustion; it will **not** surprise-bill. Projected total build spend ~$5–20 (OpenAI tokens only).

## Resource group
- **`rg-benefitnav-my`** · metadata region `southeastasia` (contains resources in `swedencentral`)
- Tags: `project=benefitnav-my`, `purpose=hackathon`, `delete-after=2026-06-15`

## AI Foundry resource (OpenAI + Content Safety)
- **Account:** `benefitnav-ai-sc-79c45` · kind `AIServices` · SKU `S0` · region **`swedencentral`**
- **Endpoint:** `https://benefitnav-ai-sc-79c45.cognitiveservices.azure.com/`
- **Key (fetch at runtime — never commit):**
  ```bash
  az cognitiveservices account keys list -g rg-benefitnav-my -n benefitnav-ai-sc-79c45 --query key1 -o tsv
  ```

### Model deployments
| Deployment name | Model | Version | SKU | Capacity (TPM ×1k) |
|---|---|---|---|---|
| `gpt-4o` | gpt-4o | 2024-11-20 | Standard | 30 |
| `text-embedding-3-large` | text-embedding-3-large | 1 | Standard | 50 |

Smoke-tested 2026-06-09: chat → `SMOKE_OK`; embedding → 3072-dim vector. ✅

## Why these choices (FreeTrial quota constraints)
- **gpt-4o, not gpt-4.1:** gpt-4.1 ships **GlobalStandard-only**, and FreeTrial GlobalStandard quota = **0** in every region checked. Standard-tier gpt-4o (50 TPM) is the strongest chat model available for free. To get gpt-4.1 → convert FreeTrial → Pay-As-You-Go.
- **Sweden Central, not Southeast Asia:** SEA had **0 chat quota** (embeddings only). Sweden Central has gpt-4o Standard quota **and** Content Safety Groundedness detection (needed Day 4). GlobalStandard routes globally regardless, so no "in-region" latency story — don't claim one.

## Azure AI Search (Foundry IQ backbone)
- **Service:** `benefitnav-search-79c45` · SKU **Basic** (~$2.45/day meter) · region `swedencentral`
- **Endpoint:** `https://benefitnav-search-79c45.search.windows.net`
- **Agentic retrieval:** `knowledgeRetrieval = free` (free token allocation; Basic SKU required — Free SKU cannot run knowledge agents)
- **Admin key (fetch at runtime):**
  ```bash
  az search admin-key show -g rg-benefitnav-my --service-name benefitnav-search-79c45 --query primaryKey -o tsv
  ```

## Conductor Container App (api.app — FastAPI + dual gate + UI)
- **App:** `benefitnav-api` · environment `benefitnav-mcp-env` · ingress external, target port 8000 · `min-replicas 1` (always-on, bills continuously)
- **URL:** `https://benefitnav-api.ashyocean-f47e8ddf.swedencentral.azurecontainerapps.io` (UI at `/`, API at `/chat`) — deployed 2026-06-12, smoke PASS (the Orchestrator agent responded live, not a fallback)
- **Image:** built from `Dockerfile.api` into ACR `ca7f0629eef3acr`
- **Identity:** system-assigned managed identity, granted **`Azure AI Developer`** on `benefitnav-ai-sc-79c45` (this is what lets it invoke the Foundry agents in-cloud — no `az` in the container)
- **Secrets (injected, never committed):** `BENEFITNAV_AOAI_KEY`, `BENEFITNAV_SEARCH_KEY`, `BENEFITNAV_TOKEN_SECRET` (the last COPIED from `benefitnav-mcp` so signed tokens verify on the trust core)
- **Deploy / redeploy:** `bash infra/deploy-api.sh` (idempotent; ends with a smoke test that fails unless a Foundry agent actually responded)
- **Build note:** the image COPYs `web/` (the static UI); the shared context-root `.dockerignore` must **not** exclude `web/`, or `az acr build -f Dockerfile.api` fails at `COPY web/`.

## Blob Storage (corpus host)
- **Account:** `benefitnavstore79c45` · Standard_LRS · Hot · region `swedencentral`
- **Container:** `corpus` — 6 `.gov.my` PDFs uploaded (source-of-truth for citations)
- **Key (fetch at runtime):**
  ```bash
  az storage account keys list -g rg-benefitnav-my -n benefitnavstore79c45 --query "[0].value" -o tsv
  ```

## Build progress
- [x] **Step A — cited retrieval (Day-1 milestone, PASS 2026-06-09):** index `benefitnav-corpus` built (400 chunks, 3072-dim, `ms.microsoft` analyzer, azureOpenAI vectorizer, semantic config). Malay OKU-allowance query returns top-3 JKM BTB chunks with `.gov.my` citations. Code: `benefitnav/ingest/`.
- [x] **Step B — agentic retrieval (DONE 2026-06-09):** knowledge source `benefitnav-ks` + knowledge base `benefitnav-kb` (gpt-4o planner). `retrieve_passages()` returns cited multi-source extractive passages. Code: `benefitnav/ingest/knowledge_base.py`, test: `ingest.kb_smoke`.
- [x] **COMPUTE + STATUS-CHECK (DONE):** `compute/` — `thresholds.json` (cited rules) + pure `checker.py` + `status.py` (GAP/near-miss). 21 unit tests. LLM never computes.
- [x] **Content Safety dual gate (DONE):** `agent/safety.py` Prompt Shields + Groundedness (threshold 0.6) + `agent/verify.py` deterministic amount guard. Refuse→Talian Kasih 15999. Verified live (injection blocked, RM9000 fabrication caught).
- [x] **Agent orchestration (DONE):** `agent/orchestrator.py` INTAKE→RETRIEVE→COMPUTE→GAP→EXPLAIN with stage trace. `appeal.py` surat rayuan. `readability.py` SPIKE simplify loop (43 tests). `translate.py` BM/EN/ZH/TA. `api/app.py` FastAPI (/assess,/appeal,/health). 64 unit tests pass; live integration tests in `tests/test_integration.py -m integration`.
- [ ] Accessible web UI (`web/`) — in progress.
- [ ] Final integration verification + README/DEMO (README.md, DEMO.md written).

## Teardown
```bash
bash benefitnav/infra/teardown.sh   # deletes the whole RG + purges soft-deleted accounts
```

> `benefitnav-api` lives in `rg-benefitnav-my`, so `teardown.sh` (which deletes the whole RG) removes it too. To stop *only* the conductor's always-on billing without a full teardown: `az containerapp update -n benefitnav-api -g rg-benefitnav-my --min-replicas 0` (note: scale-to-zero adds a cold-start to the first request).
