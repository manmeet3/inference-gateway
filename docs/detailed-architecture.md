# Detailed Architecture & Design Specification

## 1. System Overview & Request Flow

The LLM Inference Gateway is an enterprise-grade API gateway and proxy layer sitting between application clients and upstream LLM providers (local Ollama instances and cloud APIs like Anthropic Claude and OpenAI).

```
                        +----------------------------+
                        |       Client Request       |
                        +--------------+-------------+
                                       |
                                       v
                        +----------------------------+
                        |  FastAPI App & Middleware  |
                        |  - Trace ID Context Injection
                        |  - Pydantic Validation     |
                        +--------------+-------------+
                                       |
                                       v
                    +------------------------------------+
                    |  Auth & Rate Limiter (Phase 4)     |
                    |  - Redis Sliding Window / Quotas   |
                    +------------------+-----------------+
                                       |
                                       v
                    +------------------------------------+
                    |  Cache Subsystem (Phase 3)         |
                    |  1. Exact Cache (SHA-256 Hash)     |
                    |  2. Semantic Cache (MiniLM Cosine) |
                    +------------------+-----------------+
                           | (Miss)           | (Hit: return cached)
                           v                  +---------------------+
                    +--------------------+                          |
                    |   Routing Engine   |                          |
                    |  - Model Ownership |                          |
                    |  - Complexity Clf. |                          |
                    |  - Cost Strategy   |                          |
                    +----------+---------+                          |
                               |                                    |
                               v                                    |
                    +--------------------+                          |
                    |   Fallback Chain   |                          |
                    |  - Candidate Filter|                          |
                    |  - Sequential Try  |                          |
                    +----------+---------+                          |
                               |                                    |
                               v                                    |
         +------------------------------------------------+         |
         |         Upstream Providers (httpx)             |         |
         |  - Ollama (llama3.2 local)                     |         |
         |  - Anthropic (claude-sonnet-5 cloud)           |         |
         |  - OpenAI (gpt-4o cloud)                       |         |
         +---------------------+--------------------------+         |
                               |                                    |
                               +-----------------+------------------+
                                                 |
                                                 v
                                  +------------------------------+
                                  | Response / SSE Stream (Ph 5) |
                                  +--------------+---------------+
                                                 |
                                                 v
                                  +------------------------------+
                                  | Observability & Telemetry    |
                                  | - structlog JSON to stdout   |
                                  | - Prometheus Metrics (Ph 6)  |
                                  | - OpenTelemetry Spans        |
                                  +--------------+---------------+
                                                 |
                                                 v
                                  +------------------------------+
                                  | Async Celery + Redis (Ph 7)  |
                                  | - DB Log Persistence (PG)    |
                                  | - Cost Attribution Jobs      |
                                  +------------------------------+
```

---

## 2. Core Subsystems & Components

### 2.1 API & Application Lifecycle (`app/main.py`, `app/api/`)
* **Lifespan Management**:
  * On startup: Initializes async DB connection pool via SQLAlchemy, connects and pings Redis via `redis.asyncio`, configures OpenTelemetry tracer provider, and instantiates registered providers.
  * Cloud providers (`AnthropicProvider`, `OpenAIProvider`) are registered only if their respective API keys are present in the environment; `OllamaProvider` is always registered.
  * On shutdown: Gracefully closes all provider HTTP clients, disconnects Redis, and disposes of the database connection pool.
* **Context Middleware**: Binds `trace_id` (from `X-Trace-ID` header or generated UUID4) and `path` to `structlog.contextvars` for automatic injection into all downstream log lines.
* **Endpoints**:
  * `POST /v1/chat`: Main inference endpoint supporting optional `model` override, classification, routing, and fallback.
  * `POST /v1/chat/stream`: (Phase 5) SSE streaming endpoint.
  * `GET /health`: Liveness probe (returns `{"status": "ok"}`).
  * `GET /ready`: Readiness probe (verifies active Redis connectivity; returns 503 if unreachable).
  * `GET /admin/usage/{user_id}`: (Phase 4) User token quota and usage statistics.

### 2.2 Provider Abstraction Layer (`app/providers/`)
* **`LLMProvider` Interface (`base.py`)**: Abstract base class enforcing `complete()`, `stream()`, and `close()` methods.
* **Standardized Data Contracts**:
  * `Message(role: str, content: str)`
  * `LLMResponse(content: str, model: str, tokens_in: int, tokens_out: int, provider: str)`
  * `ProviderError(reason: str, message: str)`: Standardized error wrapper with uniform reason codes:
    * `upstream_timeout`: Provider timed out.
    * `provider_unavailable`: Network / connection failure.
    * `model_not_found`: Model ID unrecognized by upstream.
    * `upstream_error`: 4xx / 5xx HTTP response from upstream provider.
* **Implementations**:
  * `OllamaProvider` (`ollama.py`): Connects to local Ollama `/api/chat` via `httpx.AsyncClient`.
  * `AnthropicProvider` (`anthropic.py`): Invokes Anthropic `/v1/messages` using `httpx` with `x-api-key` and `anthropic-version`. Extracts top-level system messages.
  * `OpenAIProvider` (`openai.py`): Invokes OpenAI `/chat/completions` using `httpx` with `Authorization: Bearer <key>`.

### 2.3 Routing & Fallback Engine (`app/routing/`)
* **`ComplexityClassifier` (`classifier.py`)**:
  * Analyzes prompt text across all `user` messages.
  * Flags as `"complex"` if total character length >= `classifier_complex_char_threshold` (default 400 chars) OR if text contains reasoning keywords (`analyze`, `explain`, `debug`, `refactor`, `prove`, `design`, `architecture`, `optimize`, `algorithm`, `implement`, `compare`, `evaluate`, `step by step`, `write code`).
  * Otherwise flags as `"simple"`.
* **`CostStrategy` (`strategies.py`)**:
  * Maps complexity classification to ordered provider preference chains:
    * `"simple"`: `["ollama", "anthropic", "openai"]`
    * `"complex"`: `["anthropic", "openai", "ollama"]`
* **`FallbackChain` (`strategies.py`)**:
  * Iterates through candidate `(provider_name, model)` tuples.
  * Attempts execution; on `ProviderError`, logs a warning and steps to the next candidate.
  * Returns first successful `LLMResponse`, list of `providers_attempted`, and `fallback_reason`.
  * Raises `AllProvidersFailedError` if every candidate fails.
* **`Router` (`router.py`)**:
  * If `model` is specified by client: maps model to its owning provider, then falls back through `fallback_order`.
  * If `model` is omitted: classifies query complexity and obtains chain from `CostStrategy`.
  * Filters candidates down to currently registered/available providers.
  * Executes the fallback chain.

### 2.4 Caching Subsystem (`app/cache/` — Phase 3 Specification)
* **Two-Layer Architecture**:
  1. **Exact Cache (`exact.py`)**:
     * Key: SHA-256 hash of normalized `(model, messages)`.
     * Storage: Redis string with TTL.
     * Lookup latency: ~1-3ms.
  2. **Semantic Cache (`semantic.py`)**:
     * Embedding Model: Local `sentence-transformers` (`all-MiniLM-L6-v2`) generating 384-dimensional dense vectors.
     * Similarity Metric: Cosine similarity >= `0.92` (configurable).
     * Storage: Redis with vector index or fast indexed cosine search over active cache embeddings.
     * Lookup latency: ~10-25ms (zero external network cost).
* **Cache Manager (`manager.py`)**:
  * Orchestrates `exact -> semantic -> upstream inference`.
  * Writes successful non-cached responses to both exact and semantic stores.
  * Gracefully catches Redis errors: if Redis is unavailable, cache lookup is skipped and the request proceeds to routing.

### 2.5 Storage & Async Processing (`app/db/`, `app/workers/` — Phases 4 & 7)
* **PostgreSQL (RDS)**:
  * Persistent storage for request audits, cost ledger, token consumption per user, and latency metrics.
  * Async access via SQLAlchemy + `asyncpg` with connection pooling (`pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`).
* **Celery + Redis**:
  * Offloads request log persistence and token cost calculations from the critical request path to asynchronous worker tasks (`cost_attribution.py`).

### 2.6 Observability (`app/observability/`)
* **Structured Logging (`logging.py`)**:
  * Configured via `structlog` formatting JSON lines to standard output.
  * Contextual variables bound per request: `trace_id`, `path`, `user_id`, `requested_model`.
  * Completion log emits: `event="request_complete"`, `provider`, `model_used`, `classification`, `providers_attempted`, `fallback`, `fallback_reason`, `tokens_in`, `tokens_out`, `latency_ms`, `cache_hit`.
* **Tracing (`tracing.py`)**:
  * OpenTelemetry `TracerProvider` configured with service name and optional OTLP gRPC exporter.
  * Automatic FastAPI request instrumentation.

---

## 3. Operational Runbook & Verification (Phases 1 & 2)

### 3.1 Local Stack Startup

```bash
# Optional: Set cloud API keys before starting
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

# Build and start all services (app, postgres, redis, ollama, ollama_init)
docker compose up --build
```

Startup sequence:
1. `postgres` & `redis` start and pass healthchecks.
2. `ollama` starts and passes CLI-based healthcheck (`ollama list`).
3. `ollama_init` pulls `llama3.2` once into persistent volume `ollama_data`.
4. `app` starts, emitting `db_pool_ready` -> `redis_ready` -> `providers_ready`.

---

### 3.2 Verification Test Suite

#### 1. Unit Tests (Isolated Mock Routing)
```bash
cd gateway
pytest
```
*Exercises 12 unit tests validating classifier heuristics, cost strategy selection, fallback chain fallthrough on `ProviderError`, and router candidate filtering without network calls.*

#### 2. Health & Readiness
```bash
# Liveness
curl -s http://localhost:8000/health
# {"status":"ok"}

# Readiness (Redis ping check)
curl -s http://localhost:8000/ready
# {"status":"ready"}
```

#### 3. Automatic Simple Query Routing (Ollama)
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "user_id": "user-123"
  }'
```
*Expected: Classified as `"simple"`, routed to `ollama` (`llama3.2`), `fallback: false`.*

#### 4. Automatic Complex Query Routing (Cloud / Fallback)
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Analyze and explain the architectural trade-offs of microservices vs monoliths."}],
    "user_id": "user-123"
  }'
```
*Expected:*
- *With `ANTHROPIC_API_KEY` set: Classified as `"complex"`, routed to `anthropic` (`claude-sonnet-5`).*
- *Without cloud keys: Chain automatically filters to registered providers and falls back gracefully to `ollama`.*

#### 5. Explicit Model Request
```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "user_id": "user-123"
  }'
```
*Expected: Routes directly to OpenAI provider owning `gpt-4o`.*

#### 6. Provider Outage & Fallback
```bash
# Stop Ollama container
docker compose stop ollama

# Send request
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "user_id": "user-123"
  }'
```
*Expected:*
- *With cloud keys: Falls back to next candidate in chain (`fallback: true`, `fallback_reason: "provider_unavailable"`).*
- *Without cloud keys: Returns HTTP 503 `{"error": "all_providers_unavailable"}`.*
- *Restart Ollama:* `docker compose start ollama`