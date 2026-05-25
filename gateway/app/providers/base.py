from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


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


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, messages: list[Message], model: str) -> LLMResponse: ...

    @abstractmethod
    def stream(self, messages: list[Message], model: str) -> AsyncIterator[str]: ...

    async def close(self) -> None:
        pass
