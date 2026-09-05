from __future__ import annotations

from dataclasses import dataclass

from app.providers.base import LLMProvider, LLMResponse, Message

from app.routing.classifier import ComplexityClassifier
from app.routing.strategies import CostStrategy, FallbackChain


@dataclass
class RouteResult:
    response: LLMResponse
    classification: str | None
    providers_attempted: list[str]
    fallback_reason: str | None


class Router:
    """Picks the backend for a request and executes it with fallback.

    - If the caller names a model, route to the provider that owns it first,
      then fall through the canonical fallback order.
    - Otherwise classify the query and use the cost strategy's provider chain.

    In both cases candidates are filtered to providers that are actually
    registered (e.g. cloud providers are absent when their API key is unset),
    so the gateway degrades gracefully.
    """

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        default_models: dict[str, str],
        model_owner: dict[str, str],
        classifier: ComplexityClassifier,
        strategy: CostStrategy,
        fallback_chain: FallbackChain,
        fallback_order: list[str],
    ) -> None:
        self._providers = providers
        self._default_models = default_models
        self._model_owner = model_owner
        self._classifier = classifier
        self._strategy = strategy
        self._chain = fallback_chain
        self._fallback_order = fallback_order

    def _owner_of(self, model: str) -> str:
        # Known cloud model -> its provider; anything else is treated as a local
        # (Ollama) model, preserving the "any model name goes to Ollama" behaviour.
        return self._model_owner.get(model, "ollama")

    def _build_candidates(
        self, messages: list[Message], requested_model: str | None
    ) -> tuple[list[tuple[str, str]], str | None]:
        if requested_model:
            primary = self._owner_of(requested_model)
            order = [primary] + [p for p in self._fallback_order if p != primary]
            candidates = [
                (p, requested_model if p == primary else self._default_models[p])
                for p in order
            ]
            return candidates, None

        classification = self._classifier.classify(messages)
        candidates = [(p, self._default_models[p]) for p in self._strategy.select(classification)]
        return candidates, classification

    async def route(
        self, messages: list[Message], requested_model: str | None = None
    ) -> RouteResult:
        candidates, classification = self._build_candidates(messages, requested_model)
        candidates = [(p, m) for (p, m) in candidates if p in self._providers]
        response, attempted, fallback_reason = await self._chain.execute(candidates, messages)
        return RouteResult(
            response=response,
            classification=classification,
            providers_attempted=attempted,
            fallback_reason=fallback_reason,
        )
