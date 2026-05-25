import time

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.providers.base import Message

router = APIRouter()
logger = structlog.get_logger(__name__)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
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
    structlog.contextvars.bind_contextvars(user_id=body.user_id, model=body.model)

    start = time.monotonic()
    messages = [Message(role=m.role, content=m.content) for m in body.messages]

    try:
        result = await request.app.state.ollama.complete(messages, body.model)
    except ValueError as exc:
        logger.warning("model_not_found", error=str(exc))
        raise HTTPException(status_code=400, detail={"error": "model_not_found", "message": str(exc)})
    except httpx.ConnectError as exc:
        logger.error("provider_unavailable", error=str(exc))
        raise HTTPException(status_code=503, detail={"error": "provider_unavailable", "message": "Ollama unreachable"})
    except Exception as exc:
        logger.error("provider_error", error=str(exc))
        raise HTTPException(status_code=503, detail={"error": "provider_error", "message": str(exc)})

    latency_ms = round((time.monotonic() - start) * 1000, 2)

    logger.info(
        "request_complete",
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        latency_ms=latency_ms,
        cache_hit=False,
        provider="ollama",
    )

    return ChatResponse(
        response=result.content,
        model_used=result.model,
        latency_ms=latency_ms,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )
