# LLM Inference Gateway

## Project Overview
A production-grade LLM Inference Gateway — an API layer that sits between
application code and multiple LLM backends (local Ollama models, Anthropic,
OpenAI). It handles routing, fallback, rate limiting, semantic caching,
streaming, and observability. This is infrastructure-first: the goal is a
system that is reliable, cost-aware, and debuggable in production.

This is not a demo. Every component should be built as if it will run in
production on AWS EKS and be maintained by a team.

## Tech Stack
### Core Application
- **Language**: Python 3.12
- **Framework**: FastAPI (async, SSE streaming support)
- **Data validation**: Pydantic v2
- **Async HTTP client**: httpx (for upstream LLM calls)
- **Task queue**: Celery + Redis (for async logging, cost attribution jobs)

### LLM Backends
- **Local**: Ollama (Qwen2.5 or llama3.2 via REST API)
- **Cloud**: Anthropic Claude (claude-sonnet-4-6), OpenAI (gpt-4o)
- **Abstraction**: a unified LLMProvider interface so backends are 
  swappable without touching routing logic

### Caching
- **Redis** (AWS ElastiCache in prod) — two layers:
  - Exact cache: hash of (model + prompt) → cached response
  - Semantic cache: embed the incoming query, cosine similarity search
    against cached query embeddings, return hit if similarity > threshold
- **Embedding model for semantic cache**: local sentence-transformers
  (all-MiniLM-L6-v2) — no external call, no cost, no latency

### Storage & State
- **PostgreSQL** (AWS RDS in prod) — request logs, cost attribution,
  user quota state, model performance metrics
- **Redis** — rate limit counters (sliding window), quota state (fast path)

### Observability
- **Structured logging**: structlog → JSON, every request gets a
  trace_id, user_id, model, tokens_in, tokens_out, latency_ms, cache_hit
- **Metrics**: Prometheus client → expose /metrics endpoint
- **Tracing**: OpenTelemetry SDK → OTLP export to Grafana Tempo
- **Dashboards**: Grafana (latency p50/p95/p99, cost per model per user,
  cache hit rate, error rate, fallback rate)

### Infrastructure
- **Container runtime**: Docker
- **Orchestration**: Kubernetes on AWS EKS
- **IaC**: Terraform (EKS cluster, RDS, ElastiCache, IAM roles)
- **Secrets**: AWS Secrets Manager + External Secrets Operator in K8s
- **Ingress**: AWS ALB Ingress Controller + nginx ingress
- **CI/CD**: GitHub Actions → build image → push to ECR → kubectl apply
- **Local dev**: docker-compose (app + postgres + redis + ollama)

## Directory Structure
gateway/
├── app/
│   ├── main.py                  # FastAPI app, lifespan, middleware
│   ├── config.py                # Settings via pydantic-settings, env vars
│   ├── api/
│   │   ├── routes/
│   │   │   ├── chat.py          # POST /v1/chat — main inference endpoint
│   │   │   ├── stream.py        # POST /v1/chat/stream — SSE streaming
│   │   │   ├── health.py        # GET /health, /ready
│   │   │   └── admin.py         # GET /admin/usage, /admin/costs
│   │   └── middleware/
│   │       ├── auth.py          # API key validation, user extraction
│   │       ├── rate_limit.py    # Sliding window rate limiter via Redis
│   │       └── tracing.py       # OTel trace context injection
│   ├── providers/
│   │   ├── base.py              # Abstract LLMProvider interface
│   │   ├── anthropic.py         # Anthropic backend impl
│   │   ├── openai.py            # OpenAI backend impl
│   │   └── ollama.py            # Ollama local backend impl
│   ├── routing/
│   │   ├── router.py            # Core routing logic — picks backend
│   │   ├── strategies.py        # CostStrategy, LatencyStrategy,
│   │   │                        # TaskTypeStrategy, FallbackChain
│   │   └── classifier.py        # Classifies query complexity/type
│   ├── cache/
│   │   ├── exact.py             # Hash-based exact cache
│   │   ├── semantic.py          # Embedding similarity cache
│   │   └── manager.py           # Cache read/write orchestration
│   ├── observability/
│   │   ├── logging.py           # structlog config, request logger
│   │   ├── metrics.py           # Prometheus counters/histograms
│   │   └── tracing.py           # OTel tracer setup
│   ├── db/
│   │   ├── models.py            # SQLAlchemy ORM models
│   │   ├── session.py           # Async session factory
│   │   └── migrations/          # Alembic migrations
│   └── workers/
│       └── cost_attribution.py  # Celery task: async cost logging
├── infra/
│   ├── terraform/
│   │   ├── eks.tf
│   │   ├── rds.tf
│   │   ├── elasticache.tf
│   │   └── variables.tf
│   └── k8s/
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── ingress.yaml
│       ├── hpa.yaml             # Horizontal pod autoscaler
│       └── secrets-store.yaml   # External Secrets Operator config
├── tests/
│   ├── unit/
│   ├── integration/
│   └── load/                    # k6 load test scripts
├── docker-compose.yml           # Local dev: app + postgres + redis + ollama
├── Dockerfile
└── .github/workflows/
    └── deploy.yaml              # CI: test → build → push ECR → deploy EKS

## Key Design Constraints
- All provider API keys come from environment variables only — never in code
- Every request must be logged with enough context to reconstruct cost and
  debug failures post-hoc
- The routing layer must be testable in isolation — no LLM calls in unit tests
- Streaming and non-streaming must share the same routing and caching logic
- The system must degrade gracefully: if Redis is down, bypass cache and
  continue; if a provider is down, fall to next in chain; if all providers
  are down, return 503 with a structured error body
- Semantic cache threshold (0.92) must be configurable — not hardcoded

## What Good Looks Like
- A request comes in, gets classified as "simple", routes to local Ollama,
  hits the semantic cache, returns in <50ms with a cache_hit: true log line
- A complex request routes to Claude, streams back tokens, logs the full
  reconstructed response with token counts and latency on completion
- A provider outage triggers automatic fallback, logged with
  fallback_reason: "upstream_timeout", zero user-visible errors
- Grafana shows p99 latency, cost per model, cache hit rate, and fallback
  rate on a single dashboard
- All of this runs on EKS with zero secrets in the codebase
