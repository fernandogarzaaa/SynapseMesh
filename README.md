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

The sibling `agent-platform-os` repository's README currently describes
SynapseMesh as providing **"semantic model routing"** across LLM providers. That
capability does not exist in this repository today — there is no LLM client code,
no model selection logic, and no provider-abstraction layer of any kind here.

If semantic model routing (or the broader LLM-gateway feature set previously
described in this README — data classification, policy/budget evaluation,
`gen_ai.*` telemetry, idempotency keyed on model name) is still wanted for this
service, it needs to be designed and built from scratch; none of the scaffolding
for it currently exists in `app/`. Until then, references to SynapseMesh as an LLM
gateway or model router (here or in other repos) should be treated as aspirational,
not accurate.
