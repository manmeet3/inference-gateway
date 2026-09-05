from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, ClassVar



@dataclass
class Message:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    tokens_in: int
    tokens_out: int
    provider: str


class ProviderError(Exception):
    """Raised when an upstream provider call fails.

    `reason` is a stable, log-friendly code (e.g. "upstream_timeout",
    "model_not_found", "provider_unavailable", "upstream_error") used by the
    fallback chain to decide what to try next and by the request logger.
    """

    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = reason
        super().__init__(message or reason)


class LLMProvider(ABC):
    name: ClassVar[str]

    @abstractmethod
    async def complete(self, messages: list[Message], model: str) -> LLMResponse: ...

    @abstractmethod
    def stream(self, messages: list[Message], model: str) -> AsyncIterator[str]: ...

    async def close(self) -> None:
        pass
