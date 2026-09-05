from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis.asyncio as aioredis

from app.providers.base import LLMResponse, Message


class ExactCache:
    """Hash-based exact match cache stored in Redis."""

    def __init__(self, redis_client: aioredis.Redis | Any, ttl: int = 3600) -> None:
        self._redis = redis_client
        self._ttl = ttl

    def _hash_key(self, messages: list[Message], model: str | None) -> str:
        payload: dict[str, Any] = {
            "model": model or "",
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        serialized = json.dumps(payload, sort_keys=True)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return f"cache:exact:{digest}"

    async def get(self, messages: list[Message], model: str | None) -> LLMResponse | None:
        key = self._hash_key(messages, model)
        raw = await self._redis.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        return LLMResponse(
            content=data["content"],
            model=data["model"],
            tokens_in=data["tokens_in"],
            tokens_out=data["tokens_out"],
            provider=data["provider"],
        )

    async def set(
        self,
        messages: list[Message],
        model: str | None,
        response: LLMResponse,
        ttl: int | None = None,
    ) -> None:
        key = self._hash_key(messages, model)
        payload = {
            "content": response.content,
            "model": response.model,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "provider": response.provider,
        }
        await self._redis.set(key, json.dumps(payload), ex=ttl or self._ttl)
