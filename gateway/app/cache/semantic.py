from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis.asyncio as aioredis

from app.cache.base import Embedder
from app.providers.base import LLMResponse, Message


class SemanticCache:
    """Vector similarity cache using sentence-transformers embeddings and cosine similarity."""

    INDEX_KEY = "cache:semantic:keys"

    def __init__(
        self,
        redis_client: aioredis.Redis | Any,
        embedder: Embedder,
        threshold: float = 0.92,
        ttl: int = 86400,
    ) -> None:
        self._redis = redis_client
        self._embedder = embedder
        self._threshold = threshold
        self._ttl = ttl

    def _extract_prompt(self, messages: list[Message]) -> str:
        user_parts = [m.content for m in messages if m.role == "user"]
        if user_parts:
            return " ".join(user_parts)
        return " ".join(m.content for m in messages)

    def _cosine_similarity(self, u: list[float], v: list[float]) -> float:
        dot = sum(a * b for a, b in zip(u, v))
        norm_u = math.sqrt(sum(a * a for a in u))
        norm_v = math.sqrt(sum(b * b for b in v))
        if norm_u == 0 or norm_v == 0:
            return 0.0
        return float(dot / (norm_u * norm_v))

    async def get(
        self, messages: list[Message], model: str | None
    ) -> tuple[LLMResponse, float] | None:
        prompt = self._extract_prompt(messages)
        if not prompt.strip():
            return None

        query_vec = self._embedder.embed(prompt)
        keys = await self._redis.smembers(self.INDEX_KEY)
        if not keys:
            return None

        entry_keys = list(keys)
        raw_entries = await self._redis.mget(entry_keys)

        best_sim = -1.0
        best_resp_data: dict[str, Any] | None = None
        expired_keys: list[str] = []

        for key, raw in zip(entry_keys, raw_entries):
            if raw is None:
                expired_keys.append(key)
                continue
            try:
                entry = json.loads(raw)
                cached_vec = entry["embedding"]
                sim = self._cosine_similarity(query_vec, cached_vec)
                if sim > best_sim:
                    best_sim = sim
                    best_resp_data = entry["response"]
            except Exception:
                continue

        if expired_keys:
            await self._redis.srem(self.INDEX_KEY, *expired_keys)

        if best_sim >= self._threshold and best_resp_data is not None:
            return (
                LLMResponse(
                    content=best_resp_data["content"],
                    model=best_resp_data["model"],
                    tokens_in=best_resp_data["tokens_in"],
                    tokens_out=best_resp_data["tokens_out"],
                    provider=best_resp_data["provider"],
                ),
                round(best_sim, 4),
            )

        return None

    async def set(
        self,
        messages: list[Message],
        model: str | None,
        response: LLMResponse,
        ttl: int | None = None,
    ) -> None:
        prompt = self._extract_prompt(messages)
        if not prompt.strip():
            return

        vec = self._embedder.embed(prompt)
        entry_id = hashlib.sha256(f"{model}:{prompt}".encode("utf-8")).hexdigest()
        key = f"cache:semantic:entry:{entry_id}"

        payload = {
            "id": entry_id,
            "prompt": prompt,
            "model": response.model,
            "embedding": vec,
            "response": {
                "content": response.content,
                "model": response.model,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "provider": response.provider,
            },
        }

        ex_seconds = ttl or self._ttl
        await self._redis.set(key, json.dumps(payload), ex=ex_seconds)
        await self._redis.sadd(self.INDEX_KEY, key)
