# LLM Inference Gateway

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Redis](https://img.shields.io/badge/redis-7-red.svg)](https://redis.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A production-grade, infrastructure-first API gateway and proxy layer sitting between client applications and multiple LLM backends (local Ollama models, Anthropic Claude, and OpenAI).

Built with **FastAPI**, **Redis**, and **Sentence-Transformers**, the gateway provides centralized cost-aware routing, automatic fallback resilience, dual-layer exact and semantic caching, structured JSON observability, and graceful degradation.

---

## Architecture Overview

```
                          +-------------------------------+
                          |        Client Request         |
                          +---------------+---------------+
                                          |
                                          v
                          +-------------------------------+
                          |   FastAPI Gateway & Context   |
                          |   - X-Trace-ID Binding        |
                          |   - Pydantic Validation       |
                          +---------------+---------------+
                                          |
                                          v
                      +---------------------------------------+
                      |       Dual-Layer Caching Engine       |
                      |   1. Exact Cache (SHA-256 in Redis)   |
                      |   2. Semantic Cache (MiniLM Cosine)   |
                      +-------------------+-------------------+
                             | (Miss)            | (Hit: <15ms)
                             v                   +----------------------+
                      +-----------------------+                         |
                      |    Routing Engine     |                         |
                      |  - Model Ownership    |                         |
                      |  - Complexity Clf.    |                         |
                      |  - Cost Strategy      |                         |
                      +-----------+-----------+                         |
                                  |                                     |
                                  v                                     |
                      +-----------------------+                         |
                      |    Fallback Chain     |                         |
                      |  - Filter Registered  |                         |
                      |  - Cascade on Error   |                         |
                      +-----------+-----------+                         |
                                  |                                     |
                                  v                                     |
         +--------------------------------------------------+           |
         |             Upstream Providers (httpx)           |           |
         |  - Local: Ollama (llama3.2)                      |           |
         |  - Cloud: Anthropic (claude-sonnet-5)            |           |
         |  - Cloud: OpenAI (gpt-4o)                        |           |
         +------------------------+-------------------------+           |
                                  |                                     |
                                  +------------------+------------------+
                                                     |
                                                     v
                                      +-------------------------------+
                                      |   JSON Observability (Logs)   |
                                      |   - Latency, Tokens, Trace ID |
                                      |   - Cache Hit / Fallback Data |
                                      +-------------------------------+
```

---

## Key Features

* **Dual-Layer Caching**:
  * **Exact Cache**: Deterministic SHA-256 message hashing $\rightarrow$ sub-5ms Redis lookups.
  * **Semantic Cache**: Local `all-MiniLM-L6-v2` dense embeddings $\rightarrow$ cosine similarity match ($\ge 0.92$) in ~15ms with zero external API costs.
* **Intelligent Cost-First Routing**:
  * Heuristic query complexity classifier (`simple` vs `complex`).
  * Routine/simple queries route to local zero-cost Ollama models.
  * Complex reasoning queries route to cloud models (Claude / GPT-4o).
* **Automated Fallback & Resilience**:
  * Provider outages or timeouts trigger automatic fallthrough to next candidate in chain.
  * Missing cloud API keys gracefully degrade to available local providers.
* **Production Observability**:
  * Structured JSON logs via `structlog` with `trace_id`, `tokens_in`, `tokens_out`, `latency_ms`, `cache_hit`, and `fallback_reason`.
  * OpenTelemetry tracing integration.
* **Zero Hardcoded Secrets**:
  * Strict environment-variable-based configuration via `pydantic-settings`.

---

## Quickstart

### Prerequisites
* Docker & Docker Compose
* (Optional) `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` for cloud models

### 1. Clone and Configure
```bash
git clone https://github.com/manmeet3/inference-gateway.git
cd inference-gateway

# Copy example environment file
cp .env.example .env
```

### 2. Start the Local Stack
```bash
# Optional: export cloud keys before launching
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

docker compose up --build
```

The stack automatically:
1. Starts PostgreSQL and Redis with healthchecks.
2. Launches Ollama and pre-pulls `llama3.2` into persistent storage.
3. Boots the FastAPI application on `http://localhost:8000`.

---

## API Usage Examples

### 1. Health & Readiness
```bash
# Liveness
curl http://localhost:8000/health
# {"status":"ok"}

# Readiness (verifies Redis connection)
curl http://localhost:8000/ready
# {"status":"ready"}
```

### 2. Automatic Routing (Inference)
```bash
# Simple query -> auto-routed to local Ollama (llama3.2)
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "user_id": "user-123"
  }'
```

### 3. Explicit Model Override
```bash
# Explicitly request GPT-4o
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "user_id": "user-123"
  }'
```

---

## Documentation & Directory Index

| Section | Link | Description |
|---|---|---|
| **System Overview & Roadmap** | [docs/overview-description.md](docs/overview-description.md) | 7-Phase roadmap, tech stack domain breakdown, architectural principles. |
| **Detailed Architecture** | [docs/detailed-architecture.md](docs/detailed-architecture.md) | In-depth subsystem specifications and validation runbook. |
| **Changelog & Verification** | [WORKING-DOC.md](WORKING-DOC.md) | Phase-by-phase build logs, verification history, and test logs. |
| **Gateway Application** | [gateway/README.md](gateway/README.md) | FastAPI app structure, settings, running locally. |
| **Caching Subsystem** | [gateway/app/cache/README.md](gateway/app/cache/README.md) | Exact and semantic vector caching internals and math. |
| **Routing & Fallback** | [gateway/app/routing/README.md](gateway/app/routing/README.md) | Query classifier, cost strategy, and fallback chain. |
| **Providers Layer** | [gateway/app/providers/README.md](gateway/app/providers/README.md) | Unified `LLMProvider` contracts and error taxonomy. |
| **Testing Suite** | [gateway/tests/README.md](gateway/tests/README.md) | Isolated unit testing guide and mock architectures. |

---

## License
MIT
