# Provider Abstraction Layer

The provider subsystem defines unified interfaces and implementations for upstream LLM backends using raw async `httpx` clients (without heavy vendor SDKs).

---

## 1. Core Contracts (`base.py`)

### `LLMProvider`
Abstract base class that all backend integrations implement:

```python
class LLMProvider(ABC):
    name: ClassVar[str]

    @abstractmethod
    async def complete(self, messages: list[Message], model: str) -> LLMResponse: ...

    @abstractmethod
    def stream(self, messages: list[Message], model: str) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...
```

### Data Classes
* `Message(role: str, content: str)`
* `LLMResponse(content: str, model: str, tokens_in: int, tokens_out: int, provider: str)`

---

## 2. Provider Error Taxonomy (`ProviderError`)

All providers wrap upstream transport failures and API errors into `ProviderError` with standardized, machine-readable reason codes:

| Reason Code | Trigger Condition | Fallback Action |
|---|---|---|
| `upstream_timeout` | HTTP client timeout during connection or read | Fall through to next candidate provider |
| `provider_unavailable` | Connection refused, network error, or host unreachable | Fall through to next candidate provider |
| `model_not_found` | Upstream returns 404 (model ID not pulled/found) | Fall through to next candidate provider |
| `upstream_error` | Upstream returns 4xx/5xx HTTP error response | Fall through to next candidate provider |

---

## 3. Implemented Providers

### Local: `OllamaProvider` (`ollama.py`)
* **Endpoint**: `POST /api/chat`
* **Default Model**: `llama3.2`
* **Features**: Zero-cost local inference running on CPU/GPU.

### Cloud: `AnthropicProvider` (`anthropic.py`)
* **Endpoint**: `POST /v1/messages`
* **Default Model**: `claude-sonnet-5`
* **Features**: Automatically extracts `system` messages to the top-level field; parses token usage from response body.

### Cloud: `OpenAIProvider` (`openai.py`)
* **Endpoint**: `POST /chat/completions`
* **Default Model**: `gpt-4o`
* **Features**: Formats OpenAI standard chat payloads and extracts token usage.

---

## 4. Adding a New Provider

To add a new backend (e.g. Google Vertex, Mistral, Groq):
1. Subclass `LLMProvider` in `app/providers/<name>.py`.
2. Implement async `complete()` converting `list[Message]` to the provider's REST format via `httpx`.
3. Wrap exceptions in `ProviderError` with standard reason codes.
4. Return standardized `LLMResponse`.
5. Register the provider in `app/main.py` lifespan and `app/config.py`.
