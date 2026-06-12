# Deploy the Conductor to Azure (no local processes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the BenefitNavigator conductor (`api.app:app` — the FastAPI surface, the `/chat` orchestration, and the non-bypassable dual safety gate) as its own Azure Container App, so nothing runs on a laptop, by migrating the one runtime Foundry credential to a managed identity.

**Architecture:** Two changes, in order. **(1) Credential migration:** the only auth that breaks inside a container is the Foundry Agent Service client (`AzureCliCredential`). Swap *only the runtime path* to `DefaultAzureCredential` via a single tested `config.azure_credential()` helper — its internal chain uses your `az login` locally and the Container App's system-assigned managed identity in Azure, with no code change between them. **(2) Deploy:** add a conductor Dockerfile + an idempotent deploy script that creates the `benefitnav-api` Container App in the *existing* `benefitnav-mcp-env`, injects the AOAI/Search keys and the **copied** HMAC token secret as Container Apps secrets, grants the app's identity `Azure AI Developer`, and runs a smoke test that asserts the agent path *actually fired* (because this system fails closed **silently** — a broken-auth deploy still returns HTTP 200 with deterministic fallbacks).

**The trust boundary is unchanged:** the dual gate stays deterministic Python inside the conductor. We are moving *where code runs* (laptop → Azure), never *who enforces truth* (still code, never an LLM agent).

**Tech Stack:** Python 3.12, FastAPI + uvicorn, Azure Container Apps, Azure AI Foundry (`azure-ai-projects==2.2.0`, `azure-ai-agents==1.2.0b6`), `azure-identity` `DefaultAzureCredential`, MCR Azure Linux base image, pytest. All commands run from `benefitnav/` with `PYTHONPATH="$PWD"`; tests use `.venv/bin/python` (3.12 — the system `python3` is 3.9 and cannot run this codebase).

---

## Scope

This plan is **one deployable outcome**: the conductor runs in Azure. It deliberately **excludes** "route the verdict/retrieval through the MCP tools so the agents visibly do the assessment" (Win 2 from the discussion). Rationale:

- Once the conductor is deployed, the in-process `summarise()` call **already runs in Azure** — the user's "no local processes" goal is fully met by this plan alone.
- Routing the verdict through the Assessor agent adds an LLM round-trip + latency + a 429 risk per turn **for an identical answer**, and the gate must *still* recompute `summarise()` locally for a value it controls. It is a demo-visibility change, not a correctness one.

If you later want the agents visibly in the assessment loop, write a **separate plan** for it. Its hard constraint: the gate's *enforcement* ("refuse if the check fails") must remain deterministic control flow — never delegated to a gpt-4o agent.

**Deliberate asymmetry (do not "fix" this):** `mas/provision.py` and `mas/teardown_agents.py` keep `AzureCliCredential` — they run from your laptop where the signed-in principal is explicit and known-good. Only `mas/orchestrate.py:_project_client` runs inside the container and must change. Switching the provisioning scripts to `DefaultAzureCredential` would add identity-selection risk (its chain may pick a different principal than `az`) for zero deploy benefit.

---

## Confirmed facts (read from Azure on 2026-06-12 — not guesses)

| Thing | Value |
|---|---|
| Subscription | `REDACTED` (Azure subscription 1) |
| Resource group | `rg-benefitnav-my` |
| Container Apps environment to reuse | `benefitnav-mcp-env` |
| ACR (existing) | `ca7f0629eef3acr` → `ca7f0629eef3acr.azurecr.io` |
| AIServices account (OpenAI + Content Safety + Foundry project) | `benefitnav-ai-sc-79c45` |
| AIServices resource id | `/subscriptions/REDACTED/resourceGroups/rg-benefitnav-my/providers/Microsoft.CognitiveServices/accounts/benefitnav-ai-sc-79c45` |
| Search service | `benefitnav-search-79c45` |
| Existing trust-core app (token-secret source) | `benefitnav-mcp` |
| **RBAC role that grants agent invocation** | **`Azure AI Developer`** (the role the working local principal holds on the AIServices account) |
| Mirror: identity / ingress port / min replicas | `SystemAssigned` / `8000` / `1` |
| New app name | `benefitnav-api` |

**Pinned runtime dependency versions** (read from the working `.venv`):
`azure-ai-projects==2.2.0`, `azure-ai-agents==1.2.0b6`, `azure-identity==1.25.3`, `openai==2.41.1`, `fastapi==0.136.3`, `uvicorn==0.49.0`, `requests==2.34.2`.

---

## File Structure

| File | New/Modified | Responsibility |
|---|---|---|
| `ingest/config.py` | Modified | Add `azure_credential()` — the single source of the Foundry runtime credential (`DefaultAzureCredential`, lazily imported). |
| `mas/orchestrate.py` | Modified (`_project_client`, ~L115-119) | Build `AIProjectClient` with `config.azure_credential()` instead of `AzureCliCredential`. |
| `tests/test_credential.py` | New | Pin: `azure_credential()` returns a `DefaultAzureCredential`; `_project_client` is built with it. |
| `infra/conductor.requirements.txt` | New | Pinned dependency set for the conductor image (full SDKs; **no** `mcp`). |
| `Dockerfile.api` | New | Conductor image: installs conductor deps, copies `compute/ agent/ ingest/ mas/ api/ web/`, runs `api.app:app`. |
| `infra/smoke_deployed.py` | New | Post-deploy smoke that asserts the **agent path ran** (trace `ROUTE.status == "ok"`), defeating the silent-fallback trap. Stdlib only. |
| `infra/deploy-api.sh` | New | Idempotent build + deploy: ACR build, copy secrets (incl. token-secret), create/update app, grant `Azure AI Developer`, run smoke. |
| `infra/azure-resources.md` | Modified | Record `benefitnav-api` in the inventory + teardown note. |
| `README.md` | Modified | One line: the conductor is deployed; local `start.sh` is now optional dev-only. |

---

## Task 1: Add the `config.azure_credential()` helper

**Files:**
- Create: `tests/test_credential.py`
- Modify: `ingest/config.py` (append a function after `token_secret`, ~after line 141)

- [ ] **Step 1: Write the failing test**

Create `tests/test_credential.py`:

```python
"""Tests for the Foundry runtime credential.

This is the ONLY auth that must work BOTH locally (your `az login`) and inside the
deployed conductor container (the Container App's system-assigned managed identity).
`DefaultAzureCredential` covers both with one chain, so we pin that contract here —
and pin that `mas.orchestrate._project_client` is built with it — without ever making
a network call (constructing the credential does not authenticate).
"""
from __future__ import annotations

from ingest import config


def test_azure_credential_returns_default_credential():
    from azure.identity import DefaultAzureCredential

    cred = config.azure_credential()
    assert isinstance(cred, DefaultAzureCredential)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_credential.py -q`
Expected: FAIL — `AttributeError: module 'ingest.config' has no attribute 'azure_credential'`.

- [ ] **Step 3: Implement the helper**

Append to `ingest/config.py` (after the `token_secret()` function, at the end of the file):

```python
def azure_credential():
    """Azure credential for Foundry Agent Service (data-plane) calls.

    `DefaultAzureCredential` works in BOTH environments with no code change:
      - locally it picks up your `az login` session (Azure CLI is in its chain);
      - in the deployed conductor container it picks up the Container App's
        system-assigned managed identity.
    Imported lazily so the deterministic trust core (compute/) never needs the
    azure-identity SDK merely to import this module.
    """
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_credential.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add ingest/config.py tests/test_credential.py
git commit -m "feat: add config.azure_credential() (DefaultAzureCredential) for Foundry auth"
```

---

## Task 2: Use the helper in `mas/orchestrate._project_client`

**Files:**
- Modify: `mas/orchestrate.py` (`_project_client`, lines 114-119)
- Modify: `tests/test_credential.py` (append a second test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_credential.py`:

```python
def test_project_client_is_built_with_azure_credential(monkeypatch):
    """_project_client must build AIProjectClient with config.azure_credential(),
    not a hard-coded AzureCliCredential — that is what makes the deployed container's
    managed identity work."""
    from mas import orchestrate

    captured: dict = {}

    class FakeAIProjectClient:
        def __init__(self, *, endpoint=None, credential=None):
            captured["endpoint"] = endpoint
            captured["credential"] = credential

    # _project_client does `from azure.ai.projects import AIProjectClient` at call
    # time, so patching the attribute on the module swaps the class it resolves.
    monkeypatch.setattr("azure.ai.projects.AIProjectClient", FakeAIProjectClient)
    sentinel = object()
    monkeypatch.setattr(config, "azure_credential", lambda: sentinel)

    orchestrate._project_client.cache_clear()
    try:
        orchestrate._project_client()
    finally:
        orchestrate._project_client.cache_clear()

    assert captured["endpoint"] == config.FOUNDRY_PROJECT_ENDPOINT
    assert captured["credential"] is sentinel
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_credential.py::test_project_client_is_built_with_azure_credential -q`
Expected: FAIL — `assert captured["credential"] is sentinel` fails (current code passes a real `AzureCliCredential`, ignoring `config.azure_credential`).

- [ ] **Step 3: Apply the implementation change**

In `mas/orchestrate.py`, replace the existing `_project_client` (lines 114-119):

```python
@lru_cache(maxsize=1)
def _project_client():
    """AIProjectClient bound to the Foundry project (az-CLI auth; no secrets)."""
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential
    return AIProjectClient(endpoint=config.FOUNDRY_PROJECT_ENDPOINT,
                           credential=AzureCliCredential())
```

with:

```python
@lru_cache(maxsize=1)
def _project_client():
    """AIProjectClient bound to the Foundry project.

    Uses config.azure_credential() (DefaultAzureCredential) so the SAME code path
    works locally via `az login` AND inside the deployed conductor via the Container
    App's system-assigned managed identity — no code change between environments."""
    from azure.ai.projects import AIProjectClient
    return AIProjectClient(endpoint=config.FOUNDRY_PROJECT_ENDPOINT,
                           credential=config.azure_credential())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_credential.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full unit suite (no regressions, never hits Azure)**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/ -q`
Expected: PASS — all existing tests green (the suite is `-m "not integration"` by default, so no Azure calls). If anything fails, STOP and fix before continuing.

- [ ] **Step 6: Commit**

```bash
git add mas/orchestrate.py tests/test_credential.py
git commit -m "refactor: build Foundry client with managed-identity-capable credential"
```

---

## Task 3: Conductor dependency manifest

**Files:**
- Create: `infra/conductor.requirements.txt`

- [ ] **Step 1: Create the pinned manifest**

Create `infra/conductor.requirements.txt`:

```
# Runtime dependencies for the conductor image (Dockerfile.api → api.app:app).
# Pinned to the versions proven in the dev .venv on 2026-06-12. The `mcp` package is
# intentionally ABSENT: importing api.app pulls in no mcp/azure SDK at load time, and
# the conductor never serves MCP tools (that is the separate benefitnav-mcp app).
# The Azure SDKs ARE needed at runtime: orchestrate._project_client imports them
# lazily when it invokes a Foundry agent.
openai==2.41.1
requests==2.34.2
fastapi==0.136.3
uvicorn[standard]==0.49.0
azure-identity==1.25.3
azure-ai-projects==2.2.0
azure-ai-agents==1.2.0b6
```

- [ ] **Step 2: Sanity-check the file lists the runtime imports**

Run:
```bash
grep -E '^(openai|fastapi|uvicorn|azure-identity|azure-ai-projects)' infra/conductor.requirements.txt
```
Expected: five matching lines printed (openai, fastapi, uvicorn, azure-identity, azure-ai-projects).

- [ ] **Step 3: Commit**

```bash
git add infra/conductor.requirements.txt
git commit -m "build: add pinned conductor (FastAPI) image requirements"
```

---

## Task 4: Conductor Dockerfile

**Files:**
- Create: `Dockerfile.api`

- [ ] **Step 1: Create the Dockerfile**

Create `Dockerfile.api` (in `benefitnav/`, next to the existing MCP `Dockerfile`):

```dockerfile
# Conductor image — the FastAPI surface (api.app:app): the /chat orchestration loop,
# the NON-BYPASSABLE dual safety gate, and the static web UI. Deployed to Azure
# Container Apps as `benefitnav-api`, alongside the trust-core `benefitnav-mcp` app.
#
# Secrets are injected at runtime as Container Apps secrets (never baked in):
#   BENEFITNAV_AOAI_KEY, BENEFITNAV_SEARCH_KEY, BENEFITNAV_TOKEN_SECRET.
# Foundry Agent Service auth uses the Container App's managed identity via
# DefaultAzureCredential (config.azure_credential) — no key, no `az` in the image.
# MCR Azure Linux base (not docker.io/python) avoids Docker Hub pull-rate throttling
# on ACR's shared build runners.
FROM mcr.microsoft.com/azurelinux/base/python:3.12

WORKDIR /app

# Deps first for layer caching.
COPY infra/conductor.requirements.txt ./requirements.txt
RUN python3 -m ensurepip --upgrade 2>/dev/null || true \
    && python3 -m pip install --no-cache-dir -r requirements.txt

# Only what the conductor imports: the trust core, the LLM touchpoints, the ingest
# config/retrieval, the multi-agent layer, the API surface, and the static UI.
# corpus-fetcher/, tests/ and the MCP server's standalone deps are intentionally absent.
COPY compute/ ./compute/
COPY agent/ ./agent/
COPY ingest/ ./ingest/
COPY mas/ ./mas/
COPY api/ ./api/
COPY web/ ./web/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

# Honour $PORT so the platform can override it; bind 0.0.0.0 for the container.
CMD ["sh", "-c", "python3 -m uvicorn api.app:app --host 0.0.0.0 --port ${PORT}"]
```

- [ ] **Step 2: Verify the Dockerfile builds**

If Docker is running locally:
Run: `docker build -f Dockerfile.api -t benefitnav-api:test .`
Expected: SUCCESS — final line `=> => naming to docker.io/library/benefitnav-api:test`.

If Docker is **not** available locally, skip this step — the build is verified for real by `az acr build` in Task 7 Step 2, which fails loudly on any Dockerfile error. Note in your task log that local build was skipped.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.api
git commit -m "build: add conductor (api.app) Dockerfile for Container Apps"
```

---

## Task 5: Anti-silent-failure smoke script

**Files:**
- Create: `infra/smoke_deployed.py`

> **Why this exists:** the conductor fails closed *silently*. If the managed identity can't invoke Foundry agents, `mas.orchestrate._safe_invoke_agent` returns `""` and `run_chat` serves `_template_question_ms` / `_DEGRADED_NARRATIVE_MS` — **a 200 response with plausible content**. So `curl /health` and "got 200" both pass on a deploy where no agent ever fired. The only reliable signal is the per-turn `trace`: `ROUTE.status == "ok"` means the Orchestrator agent responded (the managed-identity → Foundry path is live); `"fallback"` means it didn't.

- [ ] **Step 1: Create the smoke script**

Create `infra/smoke_deployed.py`:

```python
"""Post-deploy smoke: prove the deployed conductor's AGENT PATH actually ran.

The system fails closed SILENTLY — a broken managed identity still returns HTTP 200
with deterministic fallback text — so a 200 is NOT success. We send one /chat turn and
assert the trace shows the Orchestrator agent responded (ROUTE.status == "ok", not
"fallback"). Retries cover RBAC role-assignment propagation (1-5 min). Stdlib only, so
the host's system python runs it without the project venv.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

ASK_MESSAGE = "Saya seorang ibu tunggal dengan dua anak dan tiada pendapatan tetap."
MAX_ATTEMPTS = 7
RETRY_SECONDS = 45


def _chat(base_url: str) -> dict:
    body = json.dumps({"message": ASK_MESSAGE, "lang": "ms"}).encode()
    req = urllib.request.Request(
        f"{base_url}/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def main(base_url: str) -> int:
    base_url = base_url.rstrip("/")
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = _chat(base_url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  attempt {attempt}: request error ({exc}); retrying in {RETRY_SECONDS}s")
            time.sleep(RETRY_SECONDS)
            continue
        trace = {s.get("stage"): s for s in data.get("trace", [])}
        route = trace.get("ROUTE", {})
        last = route
        if route.get("status") == "ok":
            print(f"SMOKE PASS — Orchestrator agent responded (action={route.get('action')!r}). "
                  "Managed identity -> Foundry agent path is LIVE.")
            return 0
        print(f"  attempt {attempt}: ROUTE status={route.get('status')!r} "
              "(fallback => agent did NOT fire, likely RBAC not propagated yet); "
              f"retrying in {RETRY_SECONDS}s")
        time.sleep(RETRY_SECONDS)

    print("SMOKE FAIL — ROUTE never reached 'ok' (last="
          f"{last}). The deploy returns 200 but runs entirely on DETERMINISTIC "
          "FALLBACKS: the managed identity cannot invoke Foundry agents. Verify the "
          "'Azure AI Developer' role assignment on benefitnav-ai-sc-79c45 and the app's "
          "identity.principalId.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: smoke_deployed.py <base_url>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
```

- [ ] **Step 2: Verify the script is syntactically valid**

Run: `python3 -m py_compile infra/smoke_deployed.py && echo "OK"`
Expected: `OK` (no syntax errors; runs on the host's system python).

- [ ] **Step 3: Commit**

```bash
git add infra/smoke_deployed.py
git commit -m "test: add post-deploy smoke asserting the agent path ran (not a silent fallback)"
```

---

## Task 6: Deploy script

**Files:**
- Create: `infra/deploy-api.sh`

- [ ] **Step 1: Create the deploy script**

Create `infra/deploy-api.sh`:

```bash
#!/usr/bin/env bash
# Build + deploy the BenefitNavigator conductor (api.app) as the `benefitnav-api`
# Container App, alongside the existing `benefitnav-mcp` trust core. After this runs,
# nothing the demo depends on runs on a laptop.
#
# No secret is committed: keys are READ from Azure at deploy time and injected as
# Container Apps secrets. The HMAC token secret is COPIED verbatim from benefitnav-mcp
# so the conductor's signed tokens verify on the trust-core container — regenerating it
# would break every grill_next/assess signature and silently degrade the Interview agent.
#
# Idempotent: re-running updates the image + secrets in place.
set -euo pipefail

RG="rg-benefitnav-my"
ENVIRONMENT="benefitnav-mcp-env"
ACR="ca7f0629eef3acr"
APP="benefitnav-api"
MCP_APP="benefitnav-mcp"
AOAI_ACCOUNT="benefitnav-ai-sc-79c45"
SEARCH_SERVICE="benefitnav-search-79c45"
ROLE="Azure AI Developer"
PORT=8000

cd "$(dirname "${BASH_SOURCE[0]}")/.."   # -> benefitnav/
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
IMAGE="${ACR}.azurecr.io/${APP}:${TAG}"

echo "==> [1/6] Building $IMAGE via ACR cloud build (verifies Dockerfile.api)"
az acr build -r "$ACR" -t "${APP}:${TAG}" -f Dockerfile.api .

echo "==> [2/6] Reading runtime secrets from Azure (values never printed)"
AOAI_KEY="$(az cognitiveservices account keys list -g "$RG" -n "$AOAI_ACCOUNT" --query key1 -o tsv)"
SEARCH_KEY="$(az search admin-key show -g "$RG" --service-name "$SEARCH_SERVICE" --query primaryKey -o tsv)"
TOKEN_SECRET="$(az containerapp secret show -g "$RG" -n "$MCP_APP" --secret-name token-secret --query value -o tsv)"
[ -n "$TOKEN_SECRET" ] || { echo "FATAL: empty token-secret copied from $MCP_APP" >&2; exit 1; }

echo "==> [3/6] Ensuring ACR admin creds for the registry pull"
az acr update -n "$ACR" --admin-enabled true >/dev/null
ACR_USER="$(az acr credential show -n "$ACR" --query username -o tsv)"
ACR_PASS="$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)"

if az containerapp show -n "$APP" -g "$RG" >/dev/null 2>&1; then
  echo "==> [4/6] $APP exists — updating secrets + image in place"
  az containerapp secret set -n "$APP" -g "$RG" \
    --secrets aoai-key="$AOAI_KEY" search-key="$SEARCH_KEY" token-secret="$TOKEN_SECRET" >/dev/null
  az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" \
    --set-env-vars BENEFITNAV_AOAI_KEY=secretref:aoai-key \
                   BENEFITNAV_SEARCH_KEY=secretref:search-key \
                   BENEFITNAV_TOKEN_SECRET=secretref:token-secret >/dev/null
else
  echo "==> [4/6] Creating $APP in $ENVIRONMENT"
  az containerapp create \
    --name "$APP" --resource-group "$RG" --environment "$ENVIRONMENT" \
    --image "$IMAGE" \
    --registry-server "${ACR}.azurecr.io" \
    --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --target-port "$PORT" --ingress external \
    --min-replicas 1 --max-replicas 1 \
    --system-assigned \
    --secrets aoai-key="$AOAI_KEY" search-key="$SEARCH_KEY" token-secret="$TOKEN_SECRET" \
    --env-vars BENEFITNAV_AOAI_KEY=secretref:aoai-key \
               BENEFITNAV_SEARCH_KEY=secretref:search-key \
               BENEFITNAV_TOKEN_SECRET=secretref:token-secret >/dev/null
fi

echo "==> [5/6] Granting the app's managed identity '$ROLE' on $AOAI_ACCOUNT"
MI_OID="$(az containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)"
[ -n "$MI_OID" ] || { echo "FATAL: app has no managed identity principalId" >&2; exit 1; }
AISERVICES_ID="$(az cognitiveservices account show -g "$RG" -n "$AOAI_ACCOUNT" --query id -o tsv)"
az role assignment create \
  --assignee-object-id "$MI_OID" --assignee-principal-type ServicePrincipal \
  --role "$ROLE" --scope "$AISERVICES_ID" >/dev/null 2>&1 \
  || echo "    (role assignment already present — continuing)"

FQDN="$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
echo "==> [6/6] Smoke test (asserts the AGENT path ran, retries for RBAC propagation)"
echo "    Deployed at: https://${FQDN}"
python3 infra/smoke_deployed.py "https://${FQDN}"
echo "==> DONE. App: https://${FQDN}  (UI at / , API at /chat)"
```

- [ ] **Step 2: Make it executable and syntax-check it**

Run:
```bash
chmod +x infra/deploy-api.sh
bash -n infra/deploy-api.sh && echo "SYNTAX OK"
```
Expected: `SYNTAX OK` (no parse errors). This does **not** deploy anything yet.

- [ ] **Step 3: Commit**

```bash
git add infra/deploy-api.sh
git commit -m "build: add idempotent deploy script for the benefitnav-api conductor"
```

---

## Task 7: Execute the deployment

> This is the live, money-spending step. It requires `az login` with access to `rg-benefitnav-my`. It creates an always-on (`min-replicas 1`) Container App that bills continuously — tear it down after the demo (Task 8 notes how).

- [ ] **Step 1: Confirm Azure context**

Run: `az account show --query name -o tsv`
Expected: `Azure subscription 1`. If not, run `az account set --subscription REDACTED`.

- [ ] **Step 2: Run the deploy**

Run: `bash infra/deploy-api.sh`
Expected, in order:
- `[1/6]` ACR build ends with a pushed image tag (no Dockerfile error).
- `[4/6]` creates (or updates) `benefitnav-api`.
- `[5/6]` grants the role (or reports it already exists).
- `[6/6]` prints the FQDN and runs the smoke.
- Final smoke line: `SMOKE PASS — Orchestrator agent responded (...). Managed identity -> Foundry agent path is LIVE.`

- [ ] **Step 3: If smoke FAILS (`ROUTE never reached 'ok'`)**

The deploy is live but running on fallbacks — the managed identity cannot invoke Foundry. Diagnose:
```bash
MI_OID="$(az containerapp show -n benefitnav-api -g rg-benefitnav-my --query identity.principalId -o tsv)"
az role assignment list --assignee "$MI_OID" \
  --scope "$(az cognitiveservices account show -g rg-benefitnav-my -n benefitnav-ai-sc-79c45 --query id -o tsv)" \
  --query "[].roleDefinitionName" -o tsv
```
Expected: `Azure AI Developer`. If empty, the role didn't assign — re-run `infra/deploy-api.sh` (idempotent) and wait; RBAC propagation can take up to ~5 min. Re-run the smoke alone with:
`python3 infra/smoke_deployed.py "https://$(az containerapp show -n benefitnav-api -g rg-benefitnav-my --query properties.configuration.ingress.fqdn -o tsv)"`

- [ ] **Step 4: Manually verify the UI loads from Azure**

Run:
```bash
FQDN="$(az containerapp show -n benefitnav-api -g rg-benefitnav-my --query properties.configuration.ingress.fqdn -o tsv)"
curl -s -o /dev/null -w "%{http_code}\n" "https://${FQDN}/"
```
Expected: `200`. Open `https://${FQDN}/` in a browser and confirm the Malay UI renders (this is a manual visual check — you do your own UI verification).

---

## Task 8: Document the deployed conductor + teardown

**Files:**
- Modify: `infra/azure-resources.md`
- Modify: `README.md`

- [ ] **Step 1: Record the new app in the resource inventory**

In `infra/azure-resources.md`, add this block immediately after the `## Azure AI Search (Foundry IQ backbone)` section (i.e., before `## Blob Storage`):

```markdown
## Conductor Container App (api.app — FastAPI + dual gate + UI)
- **App:** `benefitnav-api` · environment `benefitnav-mcp-env` · ingress external, target port 8000 · `min-replicas 1` (always-on, bills continuously)
- **Image:** built from `Dockerfile.api` into ACR `ca7f0629eef3acr`
- **Identity:** system-assigned managed identity, granted **`Azure AI Developer`** on `benefitnav-ai-sc-79c45` (this is what lets it invoke the Foundry agents in-cloud — no `az` in the container)
- **Secrets (injected, never committed):** `BENEFITNAV_AOAI_KEY`, `BENEFITNAV_SEARCH_KEY`, `BENEFITNAV_TOKEN_SECRET` (the last COPIED from `benefitnav-mcp` so signed tokens verify on the trust core)
- **Deploy / redeploy:** `bash infra/deploy-api.sh` (idempotent; ends with a smoke test that fails unless a Foundry agent actually responded)
```

- [ ] **Step 2: Update the teardown note**

In `infra/azure-resources.md`, under `## Teardown`, append this line after the existing `bash benefitnav/infra/teardown.sh ...` block:

```markdown
> `benefitnav-api` lives in `rg-benefitnav-my`, so `teardown.sh` (which deletes the whole RG) removes it too. To stop *only* the conductor's always-on billing without a full teardown: `az containerapp update -n benefitnav-api -g rg-benefitnav-my --min-replicas 0` (note: scale-to-zero adds a cold-start to the first request).
```

- [ ] **Step 3: Note the deployment in the README**

In `README.md`, find the "Run the app" / local-run instructions (the `start.sh` / uvicorn section) and add this note directly above or below them:

```markdown
> **Deployed:** the conductor runs in Azure as the `benefitnav-api` Container App (see `infra/azure-resources.md`); `bash infra/deploy-api.sh` builds and ships it. Running locally with `start.sh` is now **dev-only** and optional — the live demo is fully cloud-hosted (Foundry agents + the trust-core MCP app + this conductor), with no laptop in the loop.
```

- [ ] **Step 4: Commit**

```bash
git add infra/azure-resources.md README.md
git commit -m "docs: record benefitnav-api conductor deployment + teardown"
```

---

## Self-Review (completed during authoring)

**1. Spec coverage** — every requirement from the discussion maps to a task:
- "Don't run local processes" → Tasks 4-7 deploy the conductor to Azure (the in-process `summarise()` now runs in-cloud).
- "Foundry as tools" reconciliation → addressed in the **Scope** section (trust core is already MCP tools; deploy makes the conductor cloud-hosted; routing verdicts through agents is an explicitly deferred follow-up with the gate-stays-deterministic constraint stated).
- The one real code change (container-incompatible Foundry auth) → Tasks 1-2.
- The advisor's silent-failure trap → Task 5 + Task 7 Step 2/3 (assert `trace.ROUTE.status == "ok"`, not HTTP 200).
- Token-secret-must-be-copied → Task 6 deploy script Step 1 (copied from `benefitnav-mcp`, with an empty-value guard).
- RBAC role read, not guessed → `Azure AI Developer` (confirmed facts table).

**2. Placeholder scan** — no "TBD"/"add error handling"/"similar to Task N". Every code/script step contains complete content. RBAC role, resource names, ports, and dependency versions are concrete confirmed values.

**3. Type/name consistency** — `config.azure_credential()`, `mas.orchestrate._project_client`, app `benefitnav-api`, image `benefitnav-api:<tag>`, secrets `aoai-key`/`search-key`/`token-secret`, env vars `BENEFITNAV_AOAI_KEY`/`BENEFITNAV_SEARCH_KEY`/`BENEFITNAV_TOKEN_SECRET`, files `Dockerfile.api`/`infra/conductor.requirements.txt`/`infra/deploy-api.sh`/`infra/smoke_deployed.py`/`tests/test_credential.py` — used identically across all tasks.

**Safety invariant guard:** the deterministic trust core, the dual gate, `verify.py`, `safety.py`, `thresholds.json`, and `_finish` are untouched. The full unit suite (Task 2 Step 5) must stay green — it pins the anti-fabrication rules that must not drift.
