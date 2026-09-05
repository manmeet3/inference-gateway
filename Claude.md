# LLM Inference Gateway

## Project Overview
A production-grade LLM Inference Gateway — an API layer that sits between application code and multiple LLM backends (local Ollama models, Anthropic Claude, OpenAI). It handles routing, fallback, rate limiting, semantic caching, streaming, and observability.

This is infrastructure-first: the goal is a system that is reliable, cost-aware, and debuggable in production, designed for deployment on AWS EKS with zero secrets in code.

## Current Project Status
- **Phase 1 (Foundation)**: Complete & Verified (FastAPI, Ollama provider, health endpoints, structlog JSON logging, Docker Compose dev stack).
- **Phase 2 (Routing & Fallback)**: Complete & Verified (Anthropic + OpenAI httpx providers, complexity classifier, cost strategy, fallback chain, graceful degradation).
- **Phase 3 (Caching)**: Complete & Verified (Redis exact cache + Sentence-Transformers semantic cache with cosine similarity & exception shielding).
- **Phase 4 (Rate Limiting & Quotas)**: Next Up.
- **Phase 5 (Streaming)**: Planned.
- **Phase 6 (Observability & Metrics)**: Planned.
- **Phase 7 (EKS Deploy & Infrastructure)**: Planned.


---

## Tech Stack

### Core Application
- **Language**: Python 3.12
- **Framework**: FastAPI (async, SSE streaming support)
- **Data Validation & Config**: Pydantic v2 & `pydantic-settings`
- **Async HTTP Client**: `httpx` (uniform calls to upstream LLMs without vendor SDKs)
- **Task Queue**: Celery + Redis (for async request logging persistence and cost attribution jobs)

### LLM Backends
- **Local**: Ollama (`llama3.2` default, `qwen2.5`) via REST API
- **Cloud**: Anthropic Claude (`claude-sonnet-5` default), OpenAI (`gpt-4o` default)
- **Abstraction**: Unified `LLMProvider` abstract interface with standardized `Message`, `LLMResponse`, and `ProviderError` types

### Caching (Phase 3)
- **Redis** (AWS ElastiCache in production) — two layers:
  - **Exact Cache**: Hash of `(model + prompt)` -> cached response with TTL
  - **Semantic Cache**: Local `sentence-transformers` embeddings (`all-MiniLM-L6-v2`), cosine similarity search against cached query vectors (threshold >= `0.92`, configurable)

### Storage & State
- **PostgreSQL** (AWS RDS in production): Request logs, cost attribution, user quota state, model performance metrics
- **Redis**: Rate limit counters (sliding window), fast-path user quota state, cache storage

### Observability
- **Structured Logging**: `structlog` -> JSON to stdout with `trace_id`, `user_id`, `model_used`, `classification`, `provider`, `providers_attempted`, `fallback`, `fallback_reason`, `tokens_in`, `tokens_out`, `latency_ms`, `cache_hit`
- **Metrics**: Prometheus client exposing `/metrics` (latency p50/p95/p99 histograms, token usage counters, fallback & cache hit rates)
- **Distributed Tracing**: OpenTelemetry SDK -> OTLP export to Grafana Tempo / OpenTelemetry Collector
- **Dashboards**: Grafana (latency p50/p95/p99, cost per model per user, cache hit rate, error rate, fallback rate)

### Infrastructure
- **Container Runtime**: Docker (multi-stage non-root container)
- **Orchestration**: Kubernetes on AWS EKS (Deployment, Service, Ingress, HPA)
- **IaC**: Terraform (EKS cluster, RDS, ElastiCache, IAM least-privilege roles)
- **Secrets**: AWS Secrets Manager + External Secrets Operator in K8s
- **Local Dev**: `docker-compose.yml` (`app` + `postgres` + `redis` + `ollama` + `ollama_init`)

---

## Directory Structure

```
gateway/
├── app/
│   ├── main.py                  # FastAPI app, lifespan hooks, middleware
│   ├── config.py                # Settings via pydantic-settings, env vars
│   ├── api/
│   │   ├── routes/
│   │   │   ├── chat.py          # POST /v1/chat — main inference endpoint
│   │   │   ├── stream.py        # POST /v1/chat/stream — SSE streaming (Phase 5)
│   │   │   ├── health.py        # GET /health, GET /ready
│   │   │   └── admin.py         # GET /admin/usage, /admin/costs (Phase 4)
│   │   └── middleware/
│   │       ├── auth.py          # API key validation & user extraction (Phase 4)
│   │       ├── rate_limit.py    # Sliding window rate limiter via Redis (Phase 4)
│   │       └── tracing.py       # OTel trace context injection
│   ├── providers/
│   │   ├── base.py              # Abstract LLMProvider interface & ProviderError
│   │   ├── ollama.py            # Local Ollama client (httpx)
│   │   ├── anthropic.py         # Anthropic Claude client (httpx)
│   │   └── openai.py            # OpenAI client (httpx)
│   ├── routing/
│   │   ├── router.py            # Core Router — model ownership & fallback orchestration
│   │   ├── strategies.py        # CostStrategy, FallbackChain, AllProvidersFailedError
│   │   └── classifier.py        # ComplexityClassifier (heuristics: length + keywords)
│   ├── cache/                   # (Phase 3)
│   │   ├── exact.py             # Hash-based exact cache
│   │   ├── semantic.py          # Embedding similarity cache (MiniLM-L6-v2)
│   │   └── manager.py           # Cache read/write orchestration
│   ├── observability/
│   │   ├── logging.py           # structlog JSON configuration
│   │   ├── metrics.py           # Prometheus counters/histograms (Phase 6)
│   │   └── tracing.py           # OpenTelemetry tracer setup
│   ├── db/
│   │   ├── models.py            # SQLAlchemy ORM models (Phase 7)
│   │   ├── session.py           # Async session & engine pool factory
│   │   └── migrations/          # Alembic migrations (Phase 7)
│   └── workers/
│       └── cost_attribution.py  # Celery task: async cost logging (Phase 7)
├── infra/                       # (Phase 7)
│   ├── terraform/               # EKS, RDS, ElastiCache, IAM
│   └── k8s/                     # Deployment, Service, Ingress, HPA, External Secrets
├── tests/
│   ├── unit/                    # Unit tests (test_routing.py, etc.)
│   ├── integration/             # End-to-end integration tests
│   └── load/                    # k6 load test scripts
├── docker-compose.yml           # Local dev: app + postgres + redis + ollama + ollama_init
├── Dockerfile                   # Gateway Dockerfile
└── .github/workflows/
    └── deploy.yaml              # CI/CD: test → build → push ECR → deploy EKS
```

---

## Key Design Constraints
- **Zero Hardcoded Secrets**: All provider API keys come from environment variables only.
- **Post-Hoc Auditability**: Every request is logged with full context to reconstruct cost and debug failures.
- **Isolated Routing Tests**: Routing and fallback logic is unit-testable in memory with no network or LLM dependencies.
- **Unified Logic Across Transports**: Streaming and non-streaming share the exact same routing, rate-limiting, and caching logic.
- **Graceful System Degradation**:
  - Redis down: bypass cache and continue.
  - Provider failure / timeout: fall through to the next candidate in the chain.
  - Cloud keys unset: gracefully exclude cloud providers and route via Ollama.
  - All providers down: return HTTP 503 with structured JSON error details.
- **Configurable Heuristics**: Classification thresholds, semantic cache cutoff (default `0.92`), token caps, and timeouts are configurable via environment variables.

---

## What Good Looks Like
- A request comes in, gets classified as "simple", routes to local Ollama, hits the semantic cache, and returns in <50ms with a `cache_hit: true` log line.
- A complex request routes to Claude, streams back tokens, and logs the full reconstructed response with token counts and latency on completion.
- A provider outage triggers automatic fallback, logged with `fallback_reason: "upstream_timeout"`, zero user-visible errors.
- Grafana displays p99 latency, cost per model, cache hit rate, and fallback rate on a single dashboard.
- All services run on EKS with zero secrets stored in codebase.
