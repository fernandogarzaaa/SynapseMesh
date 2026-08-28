# SynapseMesh

A Redis Streams-backed coordination fabric for multi-agent systems: an async pub/sub
event bus with consumer groups, a distributed task-lock manager, and a cycle/loop
detector that guards agent handoff chains against infinite or unsafe loops. The
service also exposes a small intake/validation endpoint for VLA (vision-language-action)
robotics motor commands.

> **Naming note:** the package is internally named and structured as **SwarmBus** —
> every module docstring in `app/` refers to "the SwarmBus coordination fabric" — and
> that name reflects what's actually implemented. The public-facing name
> "SynapseMesh" and prior README content described a multi-provider LLM gateway with
> semantic model routing; that is **not** what this codebase does. See
> [Not Yet Implemented / Roadmap](#not-yet-implemented--roadmap) below.

---

## What this actually is

SynapseMesh/SwarmBus is a FastAPI service (`app/main.py`) built around three pieces:

- **`app/broker.py` — `StreamBroker`**: a thin async wrapper around Redis Streams
  (`XADD` / `XREADGROUP` / `XACK`) providing topic-based pub/sub with consumer
  groups, so multiple competing agent workers can subscribe to the same topic and
  each event is processed exactly once per group.
- **`app/locker.py` — `DistributedAgentLocker`**: a distributed lock manager for
  task ownership, using `SET NX PX` to acquire a lease and a Lua compare-and-delete
  script to release it only if the releasing agent still owns the key.
- **`app/detector.py` — `DeadlockLoopInterceptor`**: DFS-based cycle detection over
  an agent handoff graph (built from an `execution_history` of `from`/`to` edges),
  combined with duplicate unmodified-payload detection, to reject handoff
  trajectories that would loop forever.

`app/main.py` wires these together behind a FastAPI app and adds one unrelated
endpoint for ingesting and bounding VLA (vision-language-action) robotics motor
commands.

There is **no** LLM provider routing, no Actor-Critic model selection, no PCI/PHI
data classification, no OpenTelemetry `gen_ai.*` telemetry, no policy/budget
evaluation, and no per-model-name idempotency control anywhere in this codebase.
Those were described in an earlier version of this README but were never
implemented.

This service requires a live Redis instance — there is no in-memory/mock fallback
mode. All three components (`StreamBroker`, `DistributedAgentLocker`,
`DeadlockLoopInterceptor`) are constructed against a real `redis.asyncio.Redis`
client at app startup (see `lifespan` in `app/main.py`).

---

## Running locally

```bash
pip install -e ".[dev]"

# Redis is required — no mock/fallback mode
docker run --rm -p 6379:6379 redis:7

uvicorn app.main:app --reload
```

### Environment variables (`app/config.py`)

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string used by the broker, locker, and app lifespan. |
| `MAX_LOOP_DEPTH` | `4` | Max DFS depth the loop detector will traverse before treating a handoff chain as unsafe. |
| `LOCK_TTL_MS` | `10000` | Lease TTL (ms) applied to task locks acquired via `SET NX PX`. |
| `AGENT_BUS_ENV` | `production` | Free-form environment label surfaced on `/healthz` and in startup logs. |

Settings load from the environment and an optional `.env` file (see
`Settings.model_config` in `app/config.py`).

---

## API

| Method & Path | Request body | Description |
| :--- | :--- | :--- |
| `GET /healthz` | — | Liveness check; returns `{"status": "ok", "environment": ...}`. |
| `POST /v1/bus/broadcast` | `{"topic": str, "event": {...}, "execution_history": [...]}` | Validates `execution_history` against the loop interceptor, then publishes `event` to the Redis Stream for `topic` via `XADD`. Returns the committed stream event ID. |
| `POST /v1/bus/claim` | `{"task_id": str, "agent_id": str}` | Attempts to acquire a distributed lock on `task_id` for `agent_id`. Returns whether the lock was acquired. |
| `POST /v1/vla/commands` | JSON body or multipart form (`frame_id`, `drift_score`, `frame_state`, optional file) | Accepts a VLA telemetry/anomaly frame, bounds `drift_score` into `[-1, 1]`, and returns deterministic, bounded `motor_commands`. |

---

## Semantic Model Routing

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

Note: streaming subscription (`StreamBroker.subscribe_topic`) and lock release
(`DistributedAgentLocker.release_task_lock`) exist as library methods but are not
currently wired to an HTTP endpoint.

---

## Testing / CI

```bash
pytest -q
ruff check .
mypy app tests
```

CI runs these same steps on every push/PR to `main` via
`.github/workflows/python-app.yml`.

---

## Not Yet Implemented / Roadmap

Semantic model routing (above) is now implemented — this section previously said
it wasn't, before `POST /v1/route/select` existed. What's still missing from the
broader LLM-gateway feature set an earlier README version described: data
classification (PCI/PHI), policy/budget evaluation, OpenTelemetry `gen_ai.*`
telemetry, and per-model-name idempotency control. None of that scaffolding
exists in `app/`. If it's still wanted, it needs to be designed and built from
scratch.
