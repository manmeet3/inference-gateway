# Detailed Architecture

This project is a production-oriented LLM inference gateway that accepts client requests through FastAPI, validates them with Pydantic, checks Redis-backed cache and rate-limit state, selects an upstream model via a routing layer, and forwards the request to one or more providers using httpx with fallback behavior and structured observability. The API supports both normal and streaming chat endpoints, records request metadata and metrics, and uses Celery plus Redis for asynchronous post-processing such as cost attribution and background logging so the request path stays fast and resilient.

## Request Flow

```
+------------------+
| Client Request   |
+--------+---------+
         |
         v
+------------------+
| FastAPI Route    |
+------------------+
         |
         v
+------------------+
| Pydantic Validate |
+------------------+
         |
         v
+------------------+
| Auth / Rate Limit|
| Redis-backed     |
+------------------+
         |
         v
+------------------+
| Cache Lookup     |
| Exact / Semantic |
+------------------+
         |
         v
+------------------+
| Route Selection  |
| classify + pick  |
+------------------+
         |
         v
+------------------+
| httpx Upstream   |
| Ollama / Claude  |
| / OpenAI         |
+------------------+
         |
         v
+------------------+
| Response / Stream |
+------------------+
         |
         v
+------------------+
| Logging / Metrics|
| Tracing / Cost   |
+------------------+
         |
         v
+------------------+
| Celery + Redis   |
| async jobs       |
+------------------+
```

# Phase 1
## Claude Prompt
Read Claude.md to understand the project. Then build Phase 1 completely before anything else. Phase 1 is:
  1. FastAPI app skeleton with lifespan hooks (db pool, Redis, connection, OTel init on startup/shutdown)
  2. Config via pydantic-settings - all values from env vars
  3. Unified LLMProvider abstract interface + Ollama implementation only
  4. POST /v1/chat endpoint - takes {model, messages, user_id}, returns {response, model_used, latency_ms, tokens_in, tokens_out}
  5. Structured JSON logging on every request with trace_id
  6. docker-compose with app + postgres + redis + ollama

  do not scaffold anything in phase 2 and beyond. Do not create placeholder files for future phases. Build phase 1 so it fully runs - I should be able to docker compose up
  and hit POST /v1/chat against a local Ollama model and get a real response with a real log line. When Phase 1 is complete, stop and tell me what to test and how

## Phase 1: Running and Validating

### Start the stack

```bash
docker compose up --build
```

Startup sequence (all enforced by healthchecks and `depends_on`):
1. `postgres` and `redis` become healthy
2. `ollama` starts and passes its healthcheck
3. `ollama_init` runs `ollama pull llama3.2` — slow on first run (~2 GB download), instant on subsequent runs (cached in the `ollama_data` volume)
4. `app` starts; expect these JSON log lines in order: `db_pool_ready` → `redis_ready` → `providers_ready`

Subsequent runs skip the image rebuild and model pull:
```bash
docker compose up
```

---

### Validate: endpoints

**Liveness** — always 200, no dependencies:
```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

**Readiness** — 200 if Redis is reachable, 503 otherwise:
```bash
curl http://localhost:8000/ready
# {"status":"ready"}
```

**Inference** — the primary path:
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2",
    "messages": [{"role": "user", "content": "What is 2+2? Answer in one word."}],
    "user_id": "user-123"
  }' | python3 -m json.tool
```

Expected response shape:
```json
{
  "response": "Four",
  "model_used": "llama3.2",
  "latency_ms": 1234.56,
  "tokens_in": 18,
  "tokens_out": 3
}
```

---

### Validate: structured log output

Every inference request emits a JSON line to stdout. Check it:
```bash
docker compose logs app --follow
```

A completed request produces a line with all of these fields:
```json
{
  "trace_id": "3f2a1b...",
  "path": "/v1/chat",
  "user_id": "user-123",
  "model": "llama3.2",
  "event": "request_complete",
  "tokens_in": 18,
  "tokens_out": 3,
  "latency_ms": 1234.56,
  "cache_hit": false,
  "provider": "ollama",
  "level": "info",
  "timestamp": "2026-05-25T..."
}
```

Pass your own `X-Trace-ID` to verify it flows through:
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Trace-ID: my-trace-abc" \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"hi"}],"user_id":"u1"}'
# trace_id in the log line will be "my-trace-abc"
```

---

### Validate: error handling

**Unknown model** → 400:
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"does-not-exist","messages":[{"role":"user","content":"hi"}],"user_id":"u1"}'
# {"detail":{"error":"model_not_found","message":"model 'does-not-exist' not found in Ollama"}}
```

**Ollama down** (stop the container to simulate) → 503:
```bash
docker compose stop ollama
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"hi"}],"user_id":"u1"}'
# {"detail":{"error":"provider_unavailable","message":"Ollama unreachable"}}
docker compose start ollama
```

---

### Phase 1 scope boundaries

What is intentionally absent (built in later phases):
- No DB writes — the PostgreSQL pool is initialized but nothing is persisted yet
- No caching — `cache_hit` is always `false`
- No auth or rate limiting
- No routing logic — all requests go directly to Ollama
- No streaming endpoint
- No Prometheus metrics

# Phase 2

## Claude Prompt
Start Phase 2 — Routing and Fallback:
  1. Add Anthropic and OpenAI provider implementations (unified LLMProvider interface, httpx)
  2. RoutingConfig: routing rules in config, not hardcoded
  3. Task complexity classifier — heuristic (token/char count, keyword signals)
  4. CostStrategy: route simple/cheap queries to Ollama, complex to cloud
  5. FallbackChain: if a provider fails or times out, try the next in the chain
  6. Log which provider was selected and why on every request

Decisions locked with the user: implement **both** Anthropic and OpenAI;
models **`claude-sonnet-5`** and **`gpt-4o`**; **cost-first** routing —
simple→Ollama, complex→cloud, fallback chain Ollama → Anthropic → OpenAI.
All providers use httpx (not vendor SDKs) so they stay uniform behind the
`LLMProvider` interface. Streaming stays out (Phase 5).

## What was built

### New components

| Component | File | Responsibility |
|-----------|------|----------------|
| Anthropic provider | `app/providers/anthropic.py` | Calls `POST /v1/messages` via httpx. System prompt is lifted to the top-level `system` field; response text/usage mapped to `LLMResponse`. |
| OpenAI provider | `app/providers/openai.py` | Calls `POST /chat/completions` via httpx. Maps `choices[0].message.content` and `usage` to `LLMResponse`. |
| Complexity classifier | `app/routing/classifier.py` | `ComplexityClassifier.classify(messages)` → `"simple"` or `"complex"` from user-message length + keyword signals. Threshold is configurable. |
| Cost strategy | `app/routing/strategies.py` | `CostStrategy.select(complexity)` → ordered list of provider names to try. Chains come from config. |
| Fallback chain | `app/routing/strategies.py` | `FallbackChain.execute(candidates, messages)` — tries each `(provider, model)` in order, falling through on `ProviderError`; returns the first success plus which providers were attempted and the fallback reason. Raises `AllProvidersFailedError` if none succeed. |
| Router | `app/routing/router.py` | Orchestrates: if a `model` is named, route to its owning provider first; otherwise classify and use the cost strategy. Filters candidates to registered providers, then runs the fallback chain. |

### Changed from Phase 1

- `app/providers/base.py` — added `ProviderError` (carries a stable `reason`
  code), a `provider` field on `LLMResponse`, and a `name` attribute on
  providers.
- `app/providers/ollama.py` — now raises `ProviderError` (instead of
  `ValueError` / bare httpx errors) so the fallback chain can catch and route
  around it.
- `app/config.py` — cloud API keys, model IDs, base URLs, `cloud_max_tokens`,
  and the classifier threshold — all from env vars.
- `app/main.py` — the lifespan builds a provider registry (Ollama always;
  Anthropic/OpenAI only when their key is set) and assembles the `Router`.
- `app/api/routes/chat.py` — routes through the `Router`; `model` is now
  **optional** (omit it to let the router classify and choose); the completion
  log line now carries `provider`, `model_used`, `classification`,
  `providers_attempted`, `fallback`, and `fallback_reason`.

### Request flow (Phase 2)

```
POST /v1/chat
   │
   ▼
Router.route(messages, requested_model)
   │
   ├─ model named?  ── yes ─▶ owning provider first, then fallback order
   │                  no  ─▶ ComplexityClassifier → CostStrategy chain
   ▼
filter candidates to registered providers (cloud absent when unkeyed)
   ▼
FallbackChain: try (provider, model) in order, fall through on ProviderError
   ▼
first success → response + {provider, classification, fallback_reason}
all fail → AllProvidersFailedError → HTTP 503
```

## Configuring cloud providers

Keys come from env vars only — never in code. Leave a key blank to disable that
provider; routing degrades gracefully to whatever is configured (Ollama-only is
a valid setup).

```bash
# .env (local) or exported before `docker compose up`
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

`docker-compose.yml` passes `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` through from
the host environment (blank if unset). Model IDs default to `claude-sonnet-5`
and `gpt-4o` and are overridable via `ANTHROPIC_MODEL` / `OPENAI_MODEL`.

## How it was tested

### Routing unit tests (no LLM calls)

The routing layer is tested in isolation with in-memory fake providers — no
network, no real models — satisfying the design constraint that routing be
unit-testable.

```bash
cd gateway
pip install -r requirements-dev.txt
pytest
```

Coverage (12 tests, all passing): classifier simple/complex/keyword cases;
`CostStrategy` chain selection; `FallbackChain` first-success, fall-through, and
all-fail; and `Router` behavior for simple→Ollama, complex→Anthropic, explicit
model → owning provider, skipping unavailable providers, and falling back on a
provider error.

### End-to-end validation

Start the stack (Ollama-only works with no cloud keys):
```bash
docker compose up --build
```

**Simple query → local Ollama** (classified `simple`):
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is 2+2?"}],"user_id":"u1"}'
```
Log line shows `classification: "simple"`, `provider: "ollama"`, `fallback: false`.

**Complex query → cloud** (classified `complex`; needs a cloud key set, else it
degrades to Ollama via the fallback chain):
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Analyze and explain the trade-offs of microservices vs a monolith in detail."}],"user_id":"u1"}'
```
With a cloud key set: `classification: "complex"`, `provider: "anthropic"`.
Without one: the complex chain (`anthropic → openai → ollama`) filters to the
registered providers and Ollama serves it — graceful degradation.

**Explicit model override** — route straight to a provider:
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}],"user_id":"u1"}'
```

**Fallback on outage** — stop the primary and watch the chain route around it:
```bash
docker compose stop ollama
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hi"}],"user_id":"u1"}'
# With a cloud key set: served by the next provider in the chain,
#   log shows fallback: true, fallback_reason: "provider_unavailable".
# With no cloud keys: 503 all_providers_unavailable (nothing left to try).
docker compose start ollama
```

## Phase 2 scope boundaries

Still intentionally absent (later phases):
- No caching — `cache_hit` is always `false` (Phase 3)
- No rate limiting or quotas (Phase 4)
- No streaming endpoint (Phase 5)
- No DB writes — request logs still go to stdout only, not PostgreSQL
- No Prometheus metrics (Phase 6)