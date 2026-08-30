# LLM Inference Gateway — Project Overview

## System Purpose
A production-grade LLM Inference Gateway — an API layer that sits between client applications and multiple LLM backends (local Ollama instances, Anthropic Claude, OpenAI). It provides centralized routing, automated fallback, rate limiting, dual-layer semantic caching, streaming, and full observability.

This system is built infrastructure-first: cost-aware, highly available, debuggable in production, and structured for enterprise deployment on AWS EKS with zero credentials hardcoded in the codebase.

---

## Tech Stack Summary

| Domain | Technology / Tool | Purpose & Details |
|---|---|---|
| **Core Framework** | Python 3.12, FastAPI, Pydantic v2 | Async API layer, SSE streaming, request validation, `pydantic-settings` env management |
| **HTTP Client** | `httpx` (async) | Uniform upstream calls to LLM REST APIs without heavy vendor SDKs |
| **Local LLM** | Ollama (`llama3.2` default, `qwen2.5`) | Zero-cost local inference for simple queries via REST API |
| **Cloud LLMs** | Anthropic (`claude-sonnet-5`), OpenAI (`gpt-4o`) | High-capacity cloud backends for complex reasoning |
| **Caching Layer** | Redis + `sentence-transformers` | **Exact cache**: SHA-256 hash `(model + prompt)`<br>**Semantic cache**: `all-MiniLM-L6-v2` embeddings with cosine similarity >= `0.92` |
| **Storage & State** | PostgreSQL (RDS) + Redis (ElastiCache) | Async request audit log, cost attribution, sliding-window rate limit counters, user token quotas |
| **Async Tasks** | Celery + Redis | Asynchronous DB log persistence, cost calculation, and batch analytics |
| **Observability** | `structlog`, Prometheus, OpenTelemetry, Grafana | Structured JSON logs with `trace_id`, Prometheus `/metrics`, distributed tracing via OTLP to Tempo |
| **Infrastructure** | Docker, Kubernetes (EKS), Terraform | Multi-stage Docker container, Helm/K8s manifests (HPA, Ingress), Terraform IaC, AWS Secrets Manager + External Secrets Operator |

---

## 7-Phase Implementation Roadmap

### Phase 1 — Foundation `[COMPLETED & VERIFIED]`
1. FastAPI app skeleton with lifespan management (async DB pool, Redis connection, OpenTelemetry tracer initialization).
2. Centralized configuration with `pydantic-settings` reading strictly from environment variables.
3. Unified abstract `LLMProvider` interface and async `OllamaProvider` implementation.
4. `POST /v1/chat` endpoint returning `{response, model_used, latency_ms, tokens_in, tokens_out}`.
5. Structured JSON logging on every request with correlated `trace_id` and latency metrics.
6. Local development environment via `docker-compose.yml` (`app`, `postgres`, `redis`, `ollama`, `ollama_init`).

### Phase 2 — Routing and Fallback `[COMPLETED & VERIFIED]`
7. Provider implementations for Anthropic (`claude-sonnet-5`) and OpenAI (`gpt-4o`) using raw async `httpx`.
8. Centralized, configurable routing policies (chains and model-to-provider mappings).
9. Heuristic `ComplexityClassifier` analyzing prompt character length and reasoning keywords (`analyze`, `explain`, `debug`, `refactor`, `architecture`, etc.).
10. `CostStrategy` mapping complexity to provider candidate chains (`simple` -> `ollama` -> `anthropic` -> `openai`; `complex` -> `anthropic` -> `openai` -> `ollama`).
11. Resilient `FallbackChain` that catches `ProviderError` and cascades through backup candidates, raising `AllProvidersFailedError` (HTTP 503) only when all fail.
12. Comprehensive request logging including `classification`, `provider`, `providers_attempted`, `fallback`, and `fallback_reason`.

### Phase 3 — Caching `[NEXT UP]`
13. **Exact Cache**: Hash `(model + prompt)` in Redis with configurable TTL.
14. **Semantic Cache**: Compute local vector embeddings using `all-MiniLM-L6-v2`, perform cosine similarity search against cached embeddings, and return hit if similarity >= `0.92` (configurable).
15. **Cache Metrics & Orchestration**: Cache manager checking exact cache first, then semantic cache; record cache hit status and latency savings in logs and metrics.
16. **Admin Invalidation**: Invalidation routes to purge cache keys by pattern, user, or model.

### Phase 4 — Rate Limiting and Quotas `[PLANNED]`
17. Redis-backed sliding window rate limiter per `user_id` (requests/minute and tokens/day).
18. Multi-tier token budget enforcement (`free`, `pro`, `internal`).
19. Standardized HTTP 429 response with `Retry-After` header when limits are exceeded.
20. Admin usage inspection route: `GET /admin/usage/{user_id}` for real-time quota tracking.

### Phase 5 — Streaming `[PLANNED]`
21. Server-Sent Events (SSE) streaming endpoint: `POST /v1/chat/stream`.
22. Clean cancellation and upstream connection termination upon client disconnect.
23. Stream-compatible logging: capture full reconstructed text, aggregate token counts, and total duration on stream completion.
24. Upstream backpressure handling for slow clients.

### Phase 6 — Observability Stack `[PLANNED]`
25. Prometheus metrics: `request_count` (by model, user tier, cache hit, status), `request_latency_seconds` histogram (p50/p95/p99), `token_usage_total`, `fallback_count`, and `cache_hit_rate`.
26. OpenTelemetry tracing spans: detailed spans across routing, cache evaluation, upstream HTTP calls, and response serialization.
27. Grafana dashboards: single-pane visualization for latencies, costs per user/model, cache hit efficiency, and provider error rates.
28. Automated alert rules: error rate > 5%, p99 latency > 5s, fallback rate > 20%.

### Phase 7 — EKS Deployment & Production IaC `[PLANNED]`
29. Hardened multi-stage Dockerfile running as non-root user.
30. Production Kubernetes manifests: `Deployment`, `Service`, `Ingress` (ALB/Nginx), and `HPA` (scaling on CPU and request rates).
31. Terraform modules for AWS infrastructure: EKS cluster, RDS PostgreSQL, ElastiCache Redis, ECR repository, and least-privilege IAM roles.
32. GitHub Actions CI/CD pipeline: unit tests -> build container -> push ECR -> rollout deployment on EKS.
33. External Secrets Operator: runtime secret synchronization from AWS Secrets Manager directly into K8s secrets.

---

## Architectural Principles & Guarantees
* **Zero Hardcoded Secrets**: All credentials and API keys are sourced from environment variables.
* **Cost-First Efficiency**: Default routing prioritizes zero-cost local inference for routine queries, conserving cloud spend for high-complexity prompts.
* **Graceful Degradation**: Outages at any layer (Redis, Ollama, Anthropic, OpenAI) do not crash the gateway; the request cascades gracefully down the fallback hierarchy.
* **Observability by Design**: Every request outputs full trace metadata for auditing, latency attribution, and cost reconstruction.
