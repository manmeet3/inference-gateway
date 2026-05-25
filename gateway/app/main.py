import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.api.routes import chat, health
from app.config import get_settings
from app.db.session import close_db, init_db
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing
from app.providers.ollama import OllamaProvider

# Configure logging at import time so all module-level loggers are ready.
configure_logging(get_settings().log_level)
logger = structlog.get_logger(__name__)


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

    app.state.ollama = OllamaProvider(settings.ollama_base_url)
    logger.info("providers_ready", ollama_url=settings.ollama_base_url)

    yield

    await app.state.ollama.close()
    await app.state.redis.aclose()
    await close_db()
    logger.info("shutdown_complete")


app = FastAPI(title="LLM Inference Gateway", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    structlog.contextvars.clear_contextvars()
    trace_id = request.headers.get("X-Trace-ID") or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(trace_id=trace_id, path=request.url.path)
    return await call_next(request)


app.include_router(health.router)
app.include_router(chat.router)
