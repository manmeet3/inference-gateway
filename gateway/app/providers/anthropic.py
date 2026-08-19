from typing import AsyncIterator

import httpx

from .base import LLMProvider, LLMResponse, Message, ProviderError


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str, max_tokens: int) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=120.0,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        self._max_tokens = max_tokens

    async def complete(self, messages: list[Message], model: str) -> LLMResponse:
        # Anthropic takes the system prompt as a top-level field, not a message role.
        system = " ".join(m.content for m in messages if m.role == "system")
        convo = [
            {"role": m.role, "content": m.content} for m in messages if m.role != "system"
        ]
        payload: dict = {"model": model, "max_tokens": self._max_tokens, "messages": convo}
        if system:
            payload["system"] = system

        try:
            resp = await self._client.post("/v1/messages", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError("upstream_timeout", str(exc)) from exc
        except httpx.ConnectError as exc:
            raise ProviderError("provider_unavailable", str(exc)) from exc

        if resp.status_code == 404:
            raise ProviderError("model_not_found", f"model '{model}' not found")
        if resp.status_code >= 400:
            raise ProviderError("upstream_error", f"{resp.status_code}: {resp.text}")

        data = resp.json()
        text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        usage = data.get("usage", {})
        return LLMResponse(
            content=text,
            model=data.get("model", model),
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            provider=self.name,
        )

    def stream(self, messages: list[Message], model: str) -> AsyncIterator[str]:
        raise NotImplementedError("streaming is phase 5")

    async def close(self) -> None:
        await self._client.aclose()
