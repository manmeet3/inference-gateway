import structlog

from app.providers.base import LLMProvider, LLMResponse, Message, ProviderError

logger = structlog.get_logger(__name__)


class CostStrategy:
    """Maps a query's complexity to an ordered list of provider names to try.

    The chains are supplied as config (not hardcoded here) so routing rules can
    change without touching code — e.g. simple -> [ollama, ...], complex ->
    [anthropic, ...].
    """

    def __init__(self, chains: dict[str, list[str]]) -> None:
        self._chains = chains

    def select(self, complexity: str) -> list[str]:
        return list(self._chains[complexity])


class AllProvidersFailedError(Exception):
    def __init__(self, attempted: list[str]) -> None:
        self.attempted = attempted
        super().__init__(f"all providers failed: {attempted}")


class FallbackChain:
    """Executes an ordered list of (provider_name, model) candidates, falling
    through to the next candidate on any ProviderError. Returns the first
    success as (response, providers_attempted, fallback_reason)."""

    def __init__(self, providers: dict[str, LLMProvider]) -> None:
        self._providers = providers

    async def execute(
        self, candidates: list[tuple[str, str]], messages: list[Message]
    ) -> tuple[LLMResponse, list[str], str | None]:
        attempted: list[str] = []
        fallback_reason: str | None = None
        for provider_name, model in candidates:
            attempted.append(provider_name)
            try:
                response = await self._providers[provider_name].complete(messages, model)
                return response, attempted, fallback_reason
            except ProviderError as exc:
                fallback_reason = exc.reason
                logger.warning(
                    "provider_failed",
                    provider=provider_name,
                    model=model,
                    reason=exc.reason,
                )
        raise AllProvidersFailedError(attempted)
