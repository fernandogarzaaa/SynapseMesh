# SynapseMesh

An ultra-low overhead, production-ready autonomous multi-model negotiation, routing, and telemetry engine engineered for the 2026 frontier model landscape (`gpt-5.3-codex`, `claude-fable-5`, `claude-mythos-5`). 

SynapseMesh decouples application code from underlying model providers, acting as an intelligent gateway control plane that dynamically negotiates and routes payloads based on **intent complexity, token budgets, and data classification boundaries** in real time.

---

## 🧠 System Architecture Overview

SynapseMesh intercepts raw prompt streams before they leave the application ecosystem. It runs a lightweight **Actor-Critic consensus loop** to determine task complexity and security classification, choosing the absolute optimal model-endpoint path within milliseconds.
[Application Layer]
│
▼ (Unified SDK Payload)
┌────────────────────────────────────────────────────────┐
│                      SYNAPSEMESH                       │
│                                                        │
│  ┌───────────────────────┐    ┌─────────────────────┐  │
│  │ Data Classification   │ ───►  Policy Evaluation  │  │
│  │ (PCI / PHI Scanner)   │    │  (Token Budgets)    │  │
│  └───────────────────────┘    └──────────┬──────────┘  │
│                                          │             │
│                                          ▼             │
│                               ┌─────────────────────┐  │
│                               │ Actor-Critic Router │  │
│                               └──────────┬──────────┘  │
└──────────────────────────────────────────┼─────────────┘
│ (Stream Invariants)
┌───────────────────────┼───────────────────────┐
▼                       ▼                       ▼
[gpt-5.3-codex]           [claude-fable-5]        [Self-Hosted vLLM]
---

## 🔒 Strict Runtime Invariants

Unlike basic API proxies or simple wrapper libraries, SynapseMesh enforces six core operational invariants directly within the execution path:

| Invariant | Execution Mechanism | Production Value |
| :--- | :--- | :--- |
| **Identity Resolution** | Gateway-level token and credential binding prior to routing. | Upstream providers only see outbound deployer signatures; shields natural end-user identity. |
| **Data Classification** | Real-time pattern scanning for PHI, PCI, and internal-restricted text. | Locks out non-compliant endpoints; prevents data leakage to unvetted third parties. |
| **Policy Evaluation** | Evaluates request metrics against per-role and per-route budgets. | Drops calls instantly or flags fallback routes if dynamic cost metrics are exceeded. |
| **Idempotency Control** | Gateway-derived cryptographic key generation for tool execution. | Downstream tool servers easily filter and reject duplicate agent retry loops. |
| **Response Normalization** | Translates multi-provider response and event stream objects. | Callers interact with a single unified envelope; no app refactoring during model churn. |
| **Native Telemetry** | Inline instrumentation built around OpenTelemetry GenAI standards. | Emits standard `gen_ai.` traces, latencies, and token metrics to your DevOps stack. |

---

## 🚀 Key Features

*   **Dynamic Capability-Based Routing:** Intuitively evaluates intent. Straightforward summary tasks are delegated to blazing fast, cost-efficient local models, while edge-case logical deductions or heavy code manipulations are escalated to top-tier frontier reasoners.
*   **OpenTelemetry-Native Observability:** Built-in hooks monitor time-to-first-token (TTFT), error state tracking (such as provider overload responses), and token-type consumption distributions (`gen_ai.client.token.usage`) effortlessly.
*   **Failover & Resiliency Arrays:** Automatically shifts active weights away from degraded or rate-limited endpoints before user-facing applications suffer latency spikes.

> 💡 **Architectural Note:** In an environment where model capabilities and pricing update constantly, hardcoding provider SDKs creates massive infrastructure technical debt. SynapseMesh ensures your application architecture remains fully sovereign, cost-optimized, and resilient against vendor lock-in.

---

## 🧭 Semantic Model Routing

SynapseMesh decides **what** model/provider a task should route to; it does not itself place live calls to any provider. That execution step -- the actual HTTP calls, retries, and provider failover -- is owned by the sibling `async-mcp-gateway` service. SynapseMesh hands that gateway a ranked decision it can walk through on failure.

Routing is pure, deterministic scoring over a static catalog of model capability profiles (`app/catalog.py`) -- no embeddings, no network calls, no live LLM invocations. The catalog ships with a built-in default spanning a few providers and can be overridden via the `MODEL_CATALOG_PATH` environment variable, pointing at a JSON file of the same shape.

### `POST /v1/route/select`

**Request** (`RoutingRequest`):

```json
{
  "task_description": "debug this function, it throws on empty input",
  "required_context_tokens": 20000,
  "requires_vision": false,
  "requires_tool_use": true,
  "latency_preference": "balanced",
  "max_cost_tier": "high"
}
```

**Response** (`RoutingDecision`, `200 OK`):

```json
{
  "selected": { "provider": "anthropic", "model": "claude-mid-balanced", "...": "..." },
  "fallback_chain": [ { "provider": "openai", "model": "gpt-large-frontier", "...": "..." } ],
  "reasoning": "Task classified as [code]; selected anthropic/claude-mid-balanced (matches code, latency=balanced, cost=medium) under latency_preference='balanced'. Beat runner-up openai/gpt-large-frontier (2.75 vs 1.50).",
  "matched_strengths": ["code"]
}
```

The task description is classified against keyword-driven strength categories (`code`, `vision`, `long_context`, `reasoning`, defaulting to `chat`). Candidates are hard-filtered on context window, vision/tool-use support, and cost ceiling, then scored on strength match with latency-preference and cost-tier tiebreakers. When no catalog entry survives the hard filter, the endpoint returns `422 Unprocessable Entity` with a message naming the unmet constraints.

`fallback_chain` carries the next-best ranked candidates (up to three) so `async-mcp-gateway` can walk them in order if the top pick's provider is degraded or rate-limited, without SynapseMesh needing to know anything about live provider health.
