from __future__ import annotations

try:
    import structlog
    logger = structlog.get_logger(__name__)
except ImportError:
    import logging

    class _CompatLogger:
        def __init__(self, std_logger: logging.Logger) -> None:
            self._logger = std_logger

        def info(self, event: str, **kwargs) -> None:
            self._logger.info("%s %s", event, kwargs)

        def warning(self, event: str, **kwargs) -> None:
            self._logger.warning("%s %s", event, kwargs)

        def error(self, event: str, **kwargs) -> None:
            self._logger.error("%s %s", event, kwargs)

    logger = _CompatLogger(logging.getLogger(__name__))

from app.cache.base import CacheHitResult
from app.cache.exact import ExactCache
from app.cache.semantic import SemanticCache
from app.providers.base import LLMResponse, Message


class CacheManager:
    """Orchestrates exact and semantic cache lookups and writes with exception shielding."""

    def __init__(
        self,
        exact_cache: ExactCache | None = None,
        semantic_cache: SemanticCache | None = None,
        enabled: bool = True,
    ) -> None:
        self._exact = exact_cache
        self._semantic = semantic_cache
        self._enabled = enabled

    async def get(
        self, messages: list[Message], model: str | None = None
    ) -> CacheHitResult | None:
        if not self._enabled:
            return None

        # 1. Exact Cache Lookup (fast hash check)
        if self._exact:
            try:
                exact_resp = await self._exact.get(messages, model)
                if exact_resp is not None:
                    return CacheHitResult(response=exact_resp, cache_type="exact")
            except Exception as exc:
                logger.warning("exact_cache_error", error=str(exc))

        # 2. Semantic Cache Lookup (embedding similarity check)
        if self._semantic:
            try:
                semantic_res = await self._semantic.get(messages, model)
                if semantic_res is not None:
                    resp, sim = semantic_res
                    return CacheHitResult(
                        response=resp, cache_type="semantic", similarity=sim
                    )
            except Exception as exc:
                logger.warning("semantic_cache_error", error=str(exc))

        return None

    async def set(
        self, messages: list[Message], model: str | None, response: LLMResponse
    ) -> None:
        if not self._enabled:
            return

        if self._exact:
            try:
                await self._exact.set(messages, model, response)
            except Exception as exc:
                logger.warning("exact_cache_write_error", error=str(exc))

        if self._semantic:
            try:
                await self._semantic.set(messages, model, response)
            except Exception as exc:
                logger.warning("semantic_cache_write_error", error=str(exc))
