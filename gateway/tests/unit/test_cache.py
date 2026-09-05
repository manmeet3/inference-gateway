from __future__ import annotations

try:
    import pytest
except ImportError:
    class _MockPytest:
        class mark:
            @staticmethod
            def asyncio(f):
                return f
    pytest = _MockPytest()

from app.cache.base import FakeEmbedder
from app.cache.exact import ExactCache
from app.cache.manager import CacheManager
from app.cache.semantic import SemanticCache
from app.providers.base import LLMResponse, Message


class FakeRedis:
    """In-memory async Redis stand-in for cache unit tests."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.should_fail = False

    async def get(self, key: str) -> str | None:
        if self.should_fail:
            raise ConnectionError("Redis connection lost")
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.should_fail:
            raise ConnectionError("Redis connection lost")
        self.store[key] = value

    async def smembers(self, key: str) -> set[str]:
        if self.should_fail:
            raise ConnectionError("Redis connection lost")
        return set(self.sets.get(key, set()))

    async def sadd(self, key: str, *members: str) -> None:
        if self.should_fail:
            raise ConnectionError("Redis connection lost")
        if key not in self.sets:
            self.sets[key] = set()
        for m in members:
            self.sets[key].add(m)

    async def srem(self, key: str, *members: str) -> None:
        if self.should_fail:
            raise ConnectionError("Redis connection lost")
        if key in self.sets:
            for m in members:
                self.sets[key].discard(m)

    async def mget(self, keys: list[str]) -> list[str | None]:
        if self.should_fail:
            raise ConnectionError("Redis connection lost")
        return [self.store.get(k) for k in keys]


_SAMPLE_MESSAGES = [Message(role="user", content="What is the capital of France?")]
_SAMPLE_RESPONSE = LLMResponse(
    content="Paris",
    model="llama3.2",
    tokens_in=10,
    tokens_out=2,
    provider="ollama",
)


# --- ExactCache Tests ---

@pytest.mark.asyncio
async def test_exact_cache_hit_and_miss():
    redis = FakeRedis()
    cache = ExactCache(redis, ttl=3600)

    # Initially empty -> miss
    assert await cache.get(_SAMPLE_MESSAGES, "llama3.2") is None

    # Populate cache
    await cache.set(_SAMPLE_MESSAGES, "llama3.2", _SAMPLE_RESPONSE)

    # Cache hit
    hit = await cache.get(_SAMPLE_MESSAGES, "llama3.2")
    assert hit is not None
    assert hit.content == "Paris"
    assert hit.model == "llama3.2"
    assert hit.provider == "ollama"

    # Different model or prompt -> miss
    assert await cache.get(_SAMPLE_MESSAGES, "gpt-4o") is None
    diff_msg = [Message(role="user", content="What is the capital of Spain?")]
    assert await cache.get(diff_msg, "llama3.2") is None


# --- SemanticCache Tests ---

@pytest.mark.asyncio
async def test_semantic_cache_high_similarity_hit():
    redis = FakeRedis()
    # Mock vectors: vector A and vector B are nearly identical (similarity 0.99)
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [0.99, 0.1, 0.0]
    embedder = FakeEmbedder({
        "What is the capital of France?": vec_a,
        "Tell me the capital of France.": vec_b,
    })
    cache = SemanticCache(redis, embedder=embedder, threshold=0.92, ttl=86400)

    # Store entry for prompt A
    await cache.set(_SAMPLE_MESSAGES, "llama3.2", _SAMPLE_RESPONSE)

    # Query with similar prompt B
    query_msg = [Message(role="user", content="Tell me the capital of France.")]
    res = await cache.get(query_msg, "llama3.2")
    assert res is not None
    resp, sim = res
    assert resp.content == "Paris"
    assert sim >= 0.92


@pytest.mark.asyncio
async def test_semantic_cache_low_similarity_miss():
    redis = FakeRedis()
    # Mock vectors: vector A and vector C are orthogonal (similarity 0.0)
    vec_a = [1.0, 0.0, 0.0]
    vec_c = [0.0, 1.0, 0.0]
    embedder = FakeEmbedder({
        "What is the capital of France?": vec_a,
        "How far is the moon?": vec_c,
    })
    cache = SemanticCache(redis, embedder=embedder, threshold=0.92, ttl=86400)

    # Store entry for prompt A
    await cache.set(_SAMPLE_MESSAGES, "llama3.2", _SAMPLE_RESPONSE)

    # Query with unrelated prompt C
    query_msg = [Message(role="user", content="How far is the moon?")]
    assert await cache.get(query_msg, "llama3.2") is None


# --- CacheManager Tests ---

@pytest.mark.asyncio
async def test_cache_manager_exact_priority():
    redis = FakeRedis()
    exact = ExactCache(redis)
    semantic = SemanticCache(redis, embedder=FakeEmbedder())
    manager = CacheManager(exact_cache=exact, semantic_cache=semantic)

    # Pre-populate exact cache
    await exact.set(_SAMPLE_MESSAGES, "llama3.2", _SAMPLE_RESPONSE)

    result = await manager.get(_SAMPLE_MESSAGES, "llama3.2")
    assert result is not None
    assert result.cache_type == "exact"
    assert result.response.content == "Paris"


@pytest.mark.asyncio
async def test_cache_manager_semantic_fallback():
    redis = FakeRedis()
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [0.99, 0.05, 0.0]
    embedder = FakeEmbedder({
        "Query A": vec_a,
        "Query B": vec_b,
    })
    exact = ExactCache(redis)
    semantic = SemanticCache(redis, embedder=embedder, threshold=0.90)
    manager = CacheManager(exact_cache=exact, semantic_cache=semantic)

    # Populate semantic cache with Query A
    msg_a = [Message(role="user", content="Query A")]
    await semantic.set(msg_a, "llama3.2", _SAMPLE_RESPONSE)

    # Query with Query B -> exact misses, semantic hits
    msg_b = [Message(role="user", content="Query B")]
    result = await manager.get(msg_b, "llama3.2")
    assert result is not None
    assert result.cache_type == "semantic"
    assert result.response.content == "Paris"
    assert result.similarity is not None and result.similarity >= 0.90


@pytest.mark.asyncio
async def test_cache_manager_exception_shielding():
    redis = FakeRedis()
    exact = ExactCache(redis)
    semantic = SemanticCache(redis, embedder=FakeEmbedder())
    manager = CacheManager(exact_cache=exact, semantic_cache=semantic)

    # Trigger redis failure
    redis.should_fail = True

    # Cache get does not raise, returns None
    result = await manager.get(_SAMPLE_MESSAGES, "llama3.2")
    assert result is None

    # Cache set does not raise
    await manager.set(_SAMPLE_MESSAGES, "llama3.2", _SAMPLE_RESPONSE)
