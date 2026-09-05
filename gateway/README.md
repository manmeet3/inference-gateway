# Gateway Service

The core Python/FastAPI service powering the LLM Inference Gateway. It manages request lifecycles, caching, routing, provider execution, and observability.

---

## Directory Structure

```
gateway/
├── Dockerfile                  # Production container definition
├── requirements.txt            # Application dependencies
├── requirements-dev.txt        # Development and testing dependencies
├── pytest.ini                  # Pytest configuration
├── app/
│   ├── main.py                 # FastAPI application, lifespan, middleware
│   ├── config.py               # Pydantic settings & environment configuration
│   ├── api/                    # Route handlers (/v1/chat, /health, /ready)
│   ├── cache/                  # Exact & Semantic caching subsystem
│   ├── routing/                # Complexity classifier, CostStrategy, FallbackChain
│   ├── providers/              # LLMProvider implementations (Ollama, Anthropic, OpenAI)
│   ├── db/                     # Async database connection pool (SQLAlchemy + asyncpg)
│   └── observability/          # structlog JSON logging & OpenTelemetry tracing
└── tests/
    └── unit/                   # Isolated in-memory unit tests
```

---

## Configuration Reference

All settings are managed via Pydantic Settings (`app/config.py`) and sourced strictly from environment variables or `.env`:

| Environment Variable | Type | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | string | `postgresql+asyncpg://...` | PostgreSQL async connection string |
| `REDIS_URL` | string | `redis://localhost:6379` | Redis connection URL |
| `OLLAMA_BASE_URL` | string | `http://localhost:11434` | Ollama service base URL |
| `OLLAMA_DEFAULT_MODEL` | string | `llama3.2` | Default local model ID |
| `ANTHROPIC_API_KEY` | string | `None` | Anthropic API key (optional; provider skipped if unset) |
| `ANTHROPIC_BASE_URL` | string | `https://api.anthropic.com` | Anthropic API base endpoint |
| `ANTHROPIC_MODEL` | string | `claude-sonnet-5` | Default Anthropic model |
| `OPENAI_API_KEY` | string | `None` | OpenAI API key (optional; provider skipped if unset) |
| `OPENAI_BASE_URL` | string | `https://api.openai.com/v1` | OpenAI API base endpoint |
| `OPENAI_MODEL` | string | `gpt-4o` | Default OpenAI model |
| `CLOUD_MAX_TOKENS` | int | `1024` | Maximum completion token cap for cloud models |
| `CLASSIFIER_COMPLEX_CHAR_THRESHOLD` | int | `400` | Character count cutoff for complexity classification |
| `ENABLE_CACHE` | bool | `True` | Enable or disable caching subsystem |
| `EXACT_CACHE_TTL_SECONDS` | int | `3600` | TTL in seconds for exact match cache entries |
| `SEMANTIC_CACHE_TTL_SECONDS` | int | `86400` | TTL in seconds for semantic vector cache entries |
| `SEMANTIC_CACHE_THRESHOLD` | float | `0.92` | Minimum cosine similarity for semantic cache hit |
| `SEMANTIC_CACHE_MODEL_NAME` | string | `all-MiniLM-L6-v2` | Sentence-transformers model for dense embeddings |
| `LOG_LEVEL` | string | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SERVICE_NAME` | string | `inference-gateway` | Service identifier for OpenTelemetry tracing |

---

## Local Development (Outside Docker)

### 1. Setup Virtual Environment
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### 2. Run the Gateway
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Subsystem Details
* **[Cache Subsystem](app/cache/README.md)**: Exact hash & semantic vector similarity caching.
* **[Routing Engine](app/routing/README.md)**: Heuristic query complexity classification and fallback chains.
* **[Providers Layer](app/providers/README.md)**: Unified `LLMProvider` contracts and provider error taxonomy.
* **[Unit Tests](tests/README.md)**: Isolated test suite with zero-network test doubles.
