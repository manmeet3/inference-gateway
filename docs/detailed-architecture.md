# Detailed Architecture

This project is a production-oriented LLM inference gateway that accepts client requests through FastAPI, validates them with Pydantic, checks Redis-backed cache and rate-limit state, selects an upstream model via a routing layer, and forwards the request to one or more providers using httpx with fallback behavior and structured observability. The API supports both normal and streaming chat endpoints, records request metadata and metrics, and uses Celery plus Redis for asynchronous post-processing such as cost attribution and background logging so the request path stays fast and resilient.

## Request Flow

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