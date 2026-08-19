from typing import AsyncIterator

import httpx

from .base import LLMProvider, LLMResponse, Message, ProviderError


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=120.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def complete(self, messages: list[Message], model: str) -> LLMResponse:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError("upstream_timeout", str(exc)) from exc
        except httpx.ConnectError as exc:
            raise ProviderError("provider_unavailable", str(exc)) from exc

        if resp.status_code == 404:
            raise ProviderError("model_not_found", f"model '{model}' not found")
        if resp.status_code >= 400:
            raise ProviderError("upstream_error", f"{resp.status_code}: {resp.text}")

        data = resp.json()
        message = data["choices"][0]["message"]
        usage = data.get("usage", {})
        return LLMResponse(
            content=message.get("content") or "",
            model=data.get("model", model),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            provider=self.name,
        )

    def stream(self, messages: list[Message], model: str) -> AsyncIterator[str]:
        raise NotImplementedError("streaming is phase 5")

    async def close(self) -> None:
        await self._client.aclose()
