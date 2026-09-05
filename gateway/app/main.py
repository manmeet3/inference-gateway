import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.api.routes import chat, health
from app.cache import (
    CacheManager,
    ExactCache,
    SemanticCache,
    SentenceTransformerEmbedder,
)
from app.config import get_settings


from app.db.session import close_db, init_db
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing
from app.providers.anthropic import AnthropicProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider
from app.routing.classifier import ComplexityClassifier
from app.routing.router import Router
from app.routing.strategies import CostStrategy, FallbackChain

# Configure logging at import time so all module-level loggers are ready.
configure_logging(get_settings().log_level)
logger = structlog.get_logger(__name__)


def _build_router(settings, providers) -> Router:
    default_models = {
        "ollama": settings.ollama_default_model,
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
    }
    model_owner = {
        settings.anthropic_model: "anthropic",
        settings.openai_model: "openai",
    }
    # Cost-first routing: simple stays local, complex prefers cloud; both fall
    # through the same chain so an outage degrades gracefully.
    chains = {
        "simple": ["ollama", "anthropic", "openai"],
        "complex": ["anthropic", "openai", "ollama"],
    }
    return Router(
        providers=providers,
        default_models=default_models,
        model_owner=model_owner,
        classifier=ComplexityClassifier(settings.classifier_complex_char_threshold),
        strategy=CostStrategy(chains),
        fallback_chain=FallbackChain(providers),
        fallback_order=["ollama", "anthropic", "openai"],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    configure_tracing(settings.service_name, settings.otlp_endpoint)
    FastAPIInstrumentor.instrument_app(app)

    logger.info("starting", service=settings.service_name)

    await init_db(settings.database_url)
    logger.info("db_pool_ready")

    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    await app.state.redis.ping()
    logger.info("redis_ready")

    # Local provider is always present; cloud providers only when keyed.
    providers = {"ollama": OllamaProvider(settings.ollama_base_url)}
    if settings.anthropic_api_key:
        providers["anthropic"] = AnthropicProvider(
            settings.anthropic_api_key, settings.anthropic_base_url, settings.cloud_max_tokens
        )
    if settings.openai_api_key:
        providers["openai"] = OpenAIProvider(settings.openai_api_key, settings.openai_base_url)

    # Cache setup (Phase 3)
    exact_cache = None
    semantic_cache = None
    if settings.enable_cache:
        exact_cache = ExactCache(app.state.redis, ttl=settings.exact_cache_ttl_seconds)
        try:
            embedder = SentenceTransformerEmbedder(settings.semantic_cache_model_name)
            semantic_cache = SemanticCache(
                app.state.redis,
                embedder=embedder,
                threshold=settings.semantic_cache_threshold,
                ttl=settings.semantic_cache_ttl_seconds,
            )
        except Exception as exc:
            logger.warning("semantic_embedder_init_failed", error=str(exc))

    app.state.cache_manager = CacheManager(
        exact_cache=exact_cache,
        semantic_cache=semantic_cache,
        enabled=settings.enable_cache,
    )
    logger.info("cache_ready", enabled=settings.enable_cache)

    yield

    for provider in app.state.providers.values():
        await provider.close()
    await app.state.redis.aclose()
    await close_db()
    logger.info("shutdown_complete")


app = FastAPI(title="LLM Inference Gateway", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    structlog.contextvars.clear_contextvars()
    trace_id = request.headers.get("X-Trace-ID") or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(trace_id=trace_id, path=request.url.path)
    return await call_next(request)


app.include_router(health.router)
app.include_router(chat.router)
