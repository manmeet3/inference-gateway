# Working Doc — LLM Inference Gateway

A running log of what's been built and how it was verified. Newest phase on top.

---

## Phase 2 — Routing and Fallback

**Status:** Complete and verified — routing unit tests pass and the stack was
exercised end-to-end via `docker compose`.

### Goal

Sit a routing layer between `/v1/chat` and multiple backends: classify each
query, pick a provider on cost grounds (cheap/simple → local Ollama,
complex → cloud), and fall through a chain on failure. Add Anthropic and OpenAI
providers behind the same `LLMProvider` interface.

Decisions (agreed with the user): implement **both** Anthropic + OpenAI; models
**`claude-sonnet-5`** and **`gpt-4o`**; **cost-first** routing with fallback
chain Ollama → Anthropic → OpenAI. All providers use httpx (not vendor SDKs) so
they stay uniform behind the interface. Streaming stays out (Phase 5).

### What was built

| Area | File | What it does |
|------|------|--------------|
| Anthropic provider | `gateway/app/providers/anthropic.py` | httpx call to `POST /v1/messages`; system prompt lifted to top-level `system`; maps text + usage to `LLMResponse`. |
| OpenAI provider | `gateway/app/providers/openai.py` | httpx call to `POST /chat/completions`; maps `choices[0].message.content` + usage. |
| Classifier | `gateway/app/routing/classifier.py` | `ComplexityClassifier.classify()` → `"simple"`/`"complex"` from user-message length + keyword signals (threshold configurable). |
| Cost strategy | `gateway/app/routing/strategies.py` | `CostStrategy.select(complexity)` → ordered provider chain (chains from config). |
| Fallback chain | `gateway/app/routing/strategies.py` | `FallbackChain.execute()` — tries each `(provider, model)`, falls through on `ProviderError`, raises `AllProvidersFailedError` if all fail. |
| Router | `gateway/app/routing/router.py` | Named model → owning provider first; else classify + strategy. Filters to registered providers, then runs the chain. |
| Base changes | `gateway/app/providers/base.py` | Added `ProviderError` (stable `reason` code), `provider` field on `LLMResponse`, `name` on providers. |
| Ollama change | `gateway/app/providers/ollama.py` | Now raises `ProviderError` so the chain can route around it. |
| Wiring | `gateway/app/main.py`, `gateway/app/api/routes/chat.py` | Lifespan builds the provider registry (cloud only when keyed) + `Router`; `/v1/chat` routes through it, `model` now optional, log line carries routing metadata. |
| Config | `gateway/app/config.py` | Cloud keys/models/base URLs, `cloud_max_tokens`, classifier threshold — env-only. |
| Tests | `gateway/tests/unit/test_routing.py` | 12 unit tests for classifier / strategy / fallback / router — no LLM calls. |

### How to run

Cloud keys are optional (env-only); leave them blank to run Ollama-only:
```bash
# optional: export ANTHROPIC_API_KEY / OPENAI_API_KEY before bringing it up
docker compose up --build
```

Routing unit tests:
```bash
cd gateway && pip install -r requirements-dev.txt && pytest   # 12 passed
```

### How it was verified

- **Unit tests** — 12/12 pass covering classifier (simple/complex/keyword),
  `CostStrategy` selection, `FallbackChain` (first-success / fall-through /
  all-fail), and `Router` (simple→Ollama, complex→Anthropic, explicit-model,
  skip-unavailable, fall-back-on-error). No network.
- **Startup** — `providers_ready` logged `["ollama"]` with no cloud keys set.
- **Simple query** — classified `simple`, served by Ollama; log line carried
  `classification`, `provider`, `providers_attempted`, `fallback: false`.
- **Complex query** — classified `complex`; with no cloud keys the chain
  (`anthropic → openai → ollama`) filtered to Ollama and it served — graceful
  degradation confirmed.
- **Outage** — stopped Ollama; with nothing left in the chain the endpoint
  returned **HTTP 503** `all_providers_unavailable`.

### Not yet verified against a real backend

A live cloud call (Anthropic/OpenAI) needs a real API key in `.env`. The wire
formats are unit-tested and import-clean, but no real `claude-sonnet-5` /
`gpt-4o` request has been made yet.

### Out of scope for Phase 2 (later phases)

- No caching — `cache_hit` still always `false` (Phase 3)
- No rate limiting / quotas (Phase 4)
- No streaming endpoint (Phase 5)
- No DB writes — request logs still stdout-only, not persisted to PostgreSQL
- No Prometheus metrics (Phase 6)

Full detail: `docs/detailed-architecture.md` → Phase 2.

---

## Phase 1 — Foundation

**Status:** Complete and verified end-to-end via `docker compose`.

### Goal

A runnable FastAPI gateway that accepts a chat request, forwards it to a local
Ollama model, and returns a structured response — with JSON request logging and
a full local dev stack. No routing, caching, auth, or DB writes yet (those are
later phases).

### What was built

| Area | File | What it does |
|------|------|--------------|
| App entrypoint | `gateway/app/main.py` | FastAPI app with a `lifespan` that initializes the DB pool, Redis client, OTel tracer, and Ollama provider on startup and tears them down on shutdown. HTTP middleware binds a `trace_id` per request (from `X-Trace-ID` header or generated). |
| Config | `gateway/app/config.py` | `pydantic-settings` — every value comes from env vars, cached as a singleton. No secrets in code. |
| Provider interface | `gateway/app/providers/base.py` | Abstract `LLMProvider` plus `Message` / `LLMResponse` dataclasses, so backends are swappable. |
| Ollama provider | `gateway/app/providers/ollama.py` | Calls Ollama `POST /api/chat` over httpx; maps a missing model to a clear error. |
| Chat endpoint | `gateway/app/api/routes/chat.py` | `POST /v1/chat` — takes `{model, messages, user_id}`, returns `{response, model_used, latency_ms, tokens_in, tokens_out}`. Logs a structured completion line. |
| Health | `gateway/app/api/routes/health.py` | `GET /health` (liveness) and `GET /ready` (checks Redis). |
| Logging | `gateway/app/observability/logging.py` | structlog → JSON to stdout. |
| Tracing | `gateway/app/observability/tracing.py` | OTel `TracerProvider`, optional OTLP export. |
| DB pool | `gateway/app/db/session.py` | Async SQLAlchemy engine + session factory (pool initialized; no writes yet). |
| Local stack | `docker-compose.yml` | `app` + `postgres` + `redis` + `ollama` + `ollama_init` (pulls `llama3.2` once into a volume). |
| Container | `gateway/Dockerfile` | Python 3.12 slim image running uvicorn. |

### How to run

```bash
docker compose up --build
```

Startup order is enforced by healthchecks and `depends_on`:
postgres + redis healthy → ollama healthy → `ollama_init` pulls `llama3.2` and
exits → app starts.

### How it was tested

All checks passed against the running stack:

1. **Lifespan logs** — app emitted, in order:
   `starting` → `db_pool_ready` → `redis_ready` → `providers_ready` →
   `Application startup complete`.

2. **Health / readiness**
   ```bash
   curl -s http://localhost:8000/health   # {"status":"ok"}
   curl -s http://localhost:8000/ready    # {"status":"ready"}
   ```

3. **Real inference against local Ollama**
   ```bash
   curl -s -X POST http://localhost:8000/v1/chat \
     -H "Content-Type: application/json" \
     -d '{"model":"llama3.2","messages":[{"role":"user","content":"What is 2+2? One word."}],"user_id":"user-123"}'
   ```
   Returned:
   ```json
   {"response":"4.","model_used":"llama3.2","latency_ms":1604.2,"tokens_in":35,"tokens_out":3}
   ```

4. **Structured log line** — each request produces a JSON log containing
   `trace_id`, `user_id`, `model`, `tokens_in`, `tokens_out`, `latency_ms`,
   `cache_hit`, and `provider`.

A more detailed run/validate guide also lives in
`docs/detailed-architecture.md`.

### Issue found and fixed during testing

On first `docker compose up`, the **app container never started**. Root cause
was a broken dependency chain:

```
ollama (unhealthy) → ollama_init (never ran) → app (never started)
```

The ollama healthcheck used `curl`, but the `ollama/ollama` image ships no
`curl` (or `wget`), so every probe failed with `curl: not found` and ollama
stayed permanently unhealthy. Since `ollama_init` waits on ollama being
healthy and `app` waits on `ollama_init` completing, both stayed in `Created`.

**Fix** (commit `c9874ca`): switched the healthcheck to the bundled CLI, which
exists in the image and exercises the API server:

```yaml
healthcheck:
  test: ["CMD", "ollama", "list"]
```

After the fix, ollama went healthy, `ollama_init` exited 0, the app started,
and all the tests above passed.

### Out of scope for Phase 1 (later phases)

- No DB writes — the pool is initialized but nothing is persisted yet
- No caching — `cache_hit` is always `false`
- No routing, fallback, auth, rate limiting, or streaming
- No Prometheus metrics endpoint
