import time

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.providers.base import Message
from app.routing.strategies import AllProvidersFailedError

router = APIRouter()
logger = structlog.get_logger(__name__)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    # Optional: when omitted the router classifies the query and picks a backend.
    model: str | None = None
    messages: list[ChatMessage]
    user_id: str


class ChatResponse(BaseModel):
    response: str
    model_used: str
    latency_ms: float
    tokens_in: int
    tokens_out: int


@router.post("/v1/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    structlog.contextvars.bind_contextvars(user_id=body.user_id, requested_model=body.model)

    start = time.monotonic()
    messages = [Message(role=m.role, content=m.content) for m in body.messages]

    # 1. Cache lookup (Phase 3)
    cache_manager = getattr(request.app.state, "cache_manager", None)
    if cache_manager:
        hit = await cache_manager.get(messages, model=body.model)
        if hit is not None:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            resp = hit.response
            logger.info(
                "request_complete",
                provider=resp.provider,
                model_used=resp.model,
                classification=None,
                providers_attempted=[],
                fallback=False,
                fallback_reason=None,
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
                latency_ms=latency_ms,
                cache_hit=True,
                cache_type=hit.cache_type,
                similarity=hit.similarity,
            )
            return ChatResponse(
                response=resp.content,
                model_used=resp.model,
                latency_ms=latency_ms,
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
            )

    # 2. Router & Fallback Chain
    try:
        result = await request.app.state.router.route(messages, requested_model=body.model)
    except AllProvidersFailedError as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        logger.error(
            "all_providers_failed",
            providers_attempted=exc.attempted,
            latency_ms=latency_ms,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "all_providers_unavailable",
                "message": "no upstream provider could serve the request",
                "providers_attempted": exc.attempted,
            },
        )

    latency_ms = round((time.monotonic() - start) * 1000, 2)
    resp = result.response
    fell_back = len(result.providers_attempted) > 1

    # 3. Populate Cache
    if cache_manager:
        await cache_manager.set(messages, body.model, resp)

    logger.info(
        "request_complete",
        provider=resp.provider,
        model_used=resp.model,
        classification=result.classification,
        providers_attempted=result.providers_attempted,
        fallback=fell_back,
        fallback_reason=result.fallback_reason if fell_back else None,
        tokens_in=resp.tokens_in,
        tokens_out=resp.tokens_out,
        latency_ms=latency_ms,
        cache_hit=False,
    )

    return ChatResponse(
        response=resp.content,
        model_used=resp.model,
        latency_ms=latency_ms,
        tokens_in=resp.tokens_in,
        tokens_out=resp.tokens_out,
    )

