from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from app.providers.base import LLMResponse


@dataclass
class CacheHitResult:
    response: LLMResponse
    cache_type: str  # "exact" | "semantic"
    similarity: float | None = None


class Embedder(Protocol):
    """Protocol for generating normalized text embeddings."""

    def embed(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """Production embedder using sentence-transformers running locally on CPU."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    def embed(self, text: str) -> list[float]:
        # normalize_embeddings=True guarantees unit length so dot product == cosine similarity.
        vector = self._model.encode(text, normalize_embeddings=True)
        if hasattr(vector, "tolist"):
            return vector.tolist()
        return list(vector)


class FakeEmbedder:
    """Deterministic in-memory embedder for unit testing with zero network/model download."""

    def __init__(self, fixed_vectors: dict[str, list[float]] | None = None) -> None:
        self.fixed_vectors = fixed_vectors or {}

    def embed(self, text: str) -> list[float]:
        if text in self.fixed_vectors:
            return self.fixed_vectors[text]
        # Generate deterministic synthetic unit vector based on text hash
        val = sum(ord(c) for c in text)
        vec = [math.sin(val + i) for i in range(16)]
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec
