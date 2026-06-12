# Architecture — BenefitNavigator Malaysia

A **multi-agent reasoning system on Azure AI Foundry**, conducted by a FastAPI service, with a deterministic trust core that the agents can reach **only** through an MCP server. The design separates the two things LLMs are good and bad at, and makes the separation *structural*: no agent can decide eligibility or state an amount, because the conductor recomputes the verdicts and a non-bypassable gate refuses anything that doesn't match.

This document expands on the overview in [`../README.md`](../README.md) with three views: the **component diagram**, the **per-turn sequence**, and the **deployment / trust boundary**. The Mermaid sources below render on GitHub; pre-rendered PNGs are in [`diagrams/`](diagrams/) for slides and the submission form.

---

## 1. Component diagram

Six gpt-4o agents own the conversation's *language and flow*; `compute/` owns its *truth*.

```mermaid
flowchart TB
    subgraph CLIENT["🌐 Citizen"]
        UI["Accessible single-page UI · web/<br/>Malay-first · ARIA · 4 languages"]
    end

    subgraph CONDUCTOR["⚙️ Conductor — Container App: benefitnav-api · FastAPI"]
        API["POST /chat · /appeal · /localize<br/>api/app.py"]
        ORCH["orchestrate.run_chat — per-turn driver<br/>'FastAPI conducts, agents execute'"]
        GATE{{"DUAL SAFETY GATE — non-bypassable<br/>1 · amount guard (verify_amounts)<br/>2 · Content Safety groundedness<br/>fail ⇒ refuse → Talian Kasih 15999"}}
        subgraph CORE["🔒 Deterministic trust core · no LLM"]
            COMPUTE["compute.summarise<br/>checker · status · elicit"]
            TH[("thresholds.json<br/>cited, gazetted rules")]
        end
    end

    subgraph FOUNDRY["🧠 Azure AI Foundry — Agent Service · 6× gpt-4o"]
        ROUTER["Orchestrator · router<br/>ask / assess / escalate"]
        INT["Interview"]
        COMM["Communicator"]
        ESC["Escalation"]
        ASSRET["Assessor · Retrieval<br/>(roles run in-process — see §4)"]
    end

    subgraph MCP["🛠️ Trust-core MCP server — Container App: benefitnav-mcp"]
        TOOLS["5 tools · assess · optimize · grill_next · grade · retrieve"]
    end

    subgraph KNOW["📚 Knowledge — Foundry IQ"]
        SEARCH["Azure AI Search · Basic<br/>index benefitnav-corpus<br/>hybrid BM25 + vector + semantic rerank"]
        EMB["text-embedding-3-large · 3072-d"]
        BLOB[("Blob Storage<br/>6 gazetted .gov.my docs")]
    end

    SAFETY["Azure AI Content Safety<br/>Prompt Shields + Groundedness"]
    TRANS["Azure AI Translator<br/>BM ↔ EN · 中文 · தமிழ்"]

    UI -->|"signed state token"| API
    API --> ORCH
    ORCH -->|"① shield input"| SAFETY
    ORCH -->|"② ROUTE"| ROUTER
    ORCH -->|"③ ask"| INT
    ORCH -->|"③ assess"| COMM
    ORCH -->|"③ escalate"| ESC
    ORCH -->|"④ verdicts in-process"| COMPUTE
    COMPUTE --- TH
    ORCH -->|"⑤ retrieve passages in-process"| SEARCH
    INT -.->|"grill_next · HMAC token"| TOOLS
    COMM -.->|"grade"| TOOLS
    ASSRET -.-> TOOLS
    TOOLS --> COMPUTE
    TOOLS -.-> SEARCH
    SEARCH --- EMB
    SEARCH --- BLOB
    ORCH -->|"⑥ gate every narrative"| GATE
    GATE --> SAFETY
    GATE -->|"verified"| TRANS
    TRANS -->|"localized reply + canonical_ms"| UI

    classDef trust fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;
    classDef gate fill:#fff3e0,stroke:#e65100,stroke-width:2px;
    class CORE,COMPUTE,TH trust;
    class GATE gate;
```

| # | Step | Who |
|---|---|---|
| ① | Prompt-Shield the untrusted free text | Content Safety |
| ② | **ROUTE** — pick ask / assess / escalate | Orchestrator agent |
| ③ | **NARRATE** — phrase the question / explanation / hand-off | Interview · Communicator · Escalation |
| ④ | Recompute verdicts + amounts as ground truth | `compute.summarise` (in-process) |
| ⑤ | Retrieve cited `.gov.my` passages for grounding | Foundry IQ / AI Search (in-process) |
| ⑥ | **DUAL GATE** every narrative → refuse or emit | `verify` + Content Safety |

---

## 2. Per-turn sequence

The citizen's facts live *inside* an HMAC-signed state token. An agent can relay the token but cannot alter the facts in it — so even a compromised agent cannot smuggle in a false fact.

```mermaid
sequenceDiagram
    actor C as Citizen
    participant API as Conductor · benefitnav-api
    participant CS as Content Safety
    participant O as Orchestrator agent
    participant S as Specialist agent
    participant MCP as MCP trust core · benefitnav-mcp
    participant K as Foundry IQ · AI Search

    C->>API: POST /chat {message, token, lang}
    API->>CS: Prompt Shield — injection?
    CS-->>API: clean
    API->>API: intake — extract stated facts (never invent)
    API->>O: ROUTE — ask / assess / escalate?
    O-->>API: {action}
    alt action = ask
        API->>S: Interview — phrase the next question
        S->>MCP: grill_next(state_token)
        MCP-->>S: most decision-relevant field
        S-->>API: warm Malay question
    else action = assess
        API->>API: compute.summarise() — verdicts + amounts (ground truth)
        API->>K: retrieve cited .gov.my passages
        K-->>API: extractive citations
        API->>S: Communicator — narrate from verdicts only
        S-->>API: plain-Malay draft
    else action = escalate
        API->>S: Escalation — hand off to a human
        S-->>API: Talian Kasih + district office
    end
    API->>API: DUAL GATE — amount guard + groundedness
    Note over API: fabricated RM or ungrounded ⇒ refuse → Talian Kasih 15999
    API->>CS: groundedness(narrative, verdicts + passages)
    CS-->>API: grounded ✓
    API-->>C: verified reply (localized) + canonical_ms + new token
```

---

## 3. Deployment & trust boundary

Everything runs in Azure (`rg-benefitnav-my`, `swedencentral`). The **trust boundary** (green) is the only place eligibility and amounts are decided; the LLM layer (blue) is on the *outside* of it and is checked on the way out.

```mermaid
flowchart LR
    subgraph RG["Azure · rg-benefitnav-my · swedencentral"]
        direction TB
        subgraph ACA["Azure Container Apps"]
            API["benefitnav-api<br/>FastAPI conductor + dual gate + UI<br/>managed identity · Azure AI Developer"]
            MCPC["benefitnav-mcp<br/>trust-core MCP server · 5 tools"]
        end
        subgraph AISVC["AIServices · benefitnav-ai-sc-79c45"]
            AGENTS["Foundry Agent Service · 6× gpt-4o"]
            CS["Content Safety<br/>Prompt Shields + Groundedness"]
            EMB["text-embedding-3-large"]
        end
        SEARCH["Azure AI Search · Basic<br/>benefitnav-corpus + benefitnav-kb"]
        BLOB[("Blob · benefitnavstore79c45<br/>6 .gov.my docs")]
    end

    API -->|"managed identity (no keys)"| AGENTS
    API -->|"compute.summarise / retrieve · in-process"| SEARCH
    API -->|"groundedness + shields"| CS
    AGENTS -.->|"MCP tools · HMAC token"| MCPC
    AGENTS --> SEARCH
    MCPC --> SEARCH
    SEARCH --- EMB
    SEARCH --- BLOB

    classDef trust fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;
    class MCPC trust;
```

**Credentials.** No keys in the repo. The conductor authenticates to Foundry with its Container App **system-assigned managed identity** (granted `Azure AI Developer` on the AIServices account) — the same `DefaultAzureCredential` code path works locally via `az login`. Search/AOAI keys and the shared HMAC `token-secret` are injected as **Container Apps secrets**. The token-secret is identical on both apps so the conductor's signature verifies on the MCP side.

---

## 4. Why "FastAPI conducts, agents execute" (Option 1)

The intended Foundry topology is an Orchestrator agent that delegates to specialists over **A2A**. Same-project Foundry→Foundry A2A is currently an **open platform bug** ([azure-sdk-for-python #47419](https://github.com/Azure/azure-sdk-for-python/issues/47419)): the agent-card-path validation rejects every delegation, regardless of configuration.

So the delegation hop moved into the conductor. The Orchestrator is a **tool-less router** — it still owns the LLM judgment that matters (ask vs assess vs escalate) — and FastAPI invokes the chosen specialist directly via the Responses API (the path proven in `mas/orchestrate._invoke_agent`). The system stays genuinely multi-agent on Foundry; only the network hop changed.

Two specialists' **roles run in-process** in the conductor:

| Role | Hosted agent (provisioned) | What the conductor runs | Why in-process |
|---|---|---|---|
| Assessor | `assessor` → `assess`, `optimize` | `compute.summarise(applicant)` | the **dual gate must own the verdict values** it checks the narrative against — round-tripping them through an LLM would put a trust-critical value on the wrong side of the gate |
| Retrieval | `retrieval` → `retrieve` | `kb.retrieve_passages(query)` | the gate grounds the narrative against the *same* passages, so the conductor fetches them directly |

Both hosted agents and their MCP tools remain provisioned and callable, and the **same trust-core functions** (`compute.summarise`, `kb.retrieve_passages`) back both the in-process path and the MCP tools — so the deterministic guarantee is identical whichever way they're reached.

---

## 5. The dual safety gate

Every agent narrative passes two checks in FastAPI before the citizen sees it (`mas/orchestrate._gate`):

1. **Amount guard (hard, always).** `verify.verify_amounts` — every `RMxxx` in the text must trace to a verdict amount, the citizen's stated income, a gazetted threshold, or the guaranteed monthly floor. A fabricated figure trips it precisely.
2. **Groundedness (soft).** Azure AI Content Safety checks the narrative against the verdicts + the whitelisted procedural facts (how/where to apply) + the cited passages.

If either trips, the turn **refuses and routes to a human** (Talian Kasih 15999). Two failure modes are handled differently on purpose:

- A narrative that is **present but unverifiable** (fabricated amount / ungrounded) is a real trust violation → **refuse**.
- A **missing** narrative (e.g. the Communicator 429s) → degrade to the trust core's own claim-free summary; the verified eligible/near-miss cards from `compute/` carry every figure and citation, so the citizen still gets a correct, cited answer.

The deterministic verdicts never depend on the LLM or on retrieval succeeding — `RETRIEVE` is best-effort and wrapped in try/except, and `COMPUTE`/`GAP` run regardless.
