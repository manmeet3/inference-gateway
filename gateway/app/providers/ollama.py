from typing import AsyncIterator

import httpx

from .base import LLMProvider, LLMResponse, Message


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=120.0)

    async def complete(self, messages: list[Message], model: str) -> LLMResponse:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }
        resp = await self._client.post("/api/chat", json=payload)
        if resp.status_code == 404:
            raise ValueError(f"model '{model}' not found in Ollama")
        resp.raise_for_status()
        data = resp.json()
        return LLMResponse(
            content=data["message"]["content"],
            model=model,
            tokens_in=data.get("prompt_eval_count", 0),
            tokens_out=data.get("eval_count", 0),
        )

    def stream(self, messages: list[Message], model: str) -> AsyncIterator[str]:
        raise NotImplementedError("streaming is phase 2")

    async def close(self) -> None:
        await self._client.aclose()
