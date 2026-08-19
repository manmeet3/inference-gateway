import pytest

from app.providers.base import LLMProvider, LLMResponse, Message, ProviderError
from app.routing.classifier import ComplexityClassifier
from app.routing.router import Router
from app.routing.strategies import AllProvidersFailedError, CostStrategy, FallbackChain


class FakeProvider(LLMProvider):
    """In-memory provider for routing tests — no real LLM calls."""

    def __init__(self, name: str, *, fail_reason: str | None = None) -> None:
        self.name = name
        self._fail_reason = fail_reason
        self.calls: list[str] = []

    async def complete(self, messages, model):
        self.calls.append(model)
        if self._fail_reason:
            raise ProviderError(self._fail_reason)
        return LLMResponse(
            content=f"hi from {self.name}",
            model=model,
            tokens_in=1,
            tokens_out=1,
            provider=self.name,
        )

    def stream(self, messages, model):
        raise NotImplementedError


_MSG = [Message("user", "hi")]


# --- classifier ---

def test_classifier_short_is_simple():
    assert ComplexityClassifier(400).classify([Message("user", "hi there")]) == "simple"


def test_classifier_long_is_complex():
    assert ComplexityClassifier(20).classify([Message("user", "x" * 50)]) == "complex"


def test_classifier_keyword_is_complex():
    c = ComplexityClassifier(10_000)
    assert c.classify([Message("user", "please analyze this")]) == "complex"


# --- cost strategy ---

def test_cost_strategy_select():
    s = CostStrategy({"simple": ["ollama"], "complex": ["anthropic", "openai"]})
    assert s.select("simple") == ["ollama"]
    assert s.select("complex") == ["anthropic", "openai"]


# --- fallback chain ---

async def test_fallback_first_success():
    chain = FallbackChain({"ollama": FakeProvider("ollama")})
    resp, attempted, reason = await chain.execute([("ollama", "llama3.2")], _MSG)
    assert resp.provider == "ollama"
    assert attempted == ["ollama"]
    assert reason is None


async def test_fallback_falls_through():
    chain = FallbackChain(
        {
            "ollama": FakeProvider("ollama", fail_reason="provider_unavailable"),
            "anthropic": FakeProvider("anthropic"),
        }
    )
    resp, attempted, reason = await chain.execute(
        [("ollama", "llama3.2"), ("anthropic", "claude-sonnet-5")], _MSG
    )
    assert resp.provider == "anthropic"
    assert attempted == ["ollama", "anthropic"]
    assert reason == "provider_unavailable"


async def test_fallback_all_fail_raises():
    chain = FallbackChain({"ollama": FakeProvider("ollama", fail_reason="upstream_error")})
    with pytest.raises(AllProvidersFailedError):
        await chain.execute([("ollama", "llama3.2")], _MSG)


# --- router ---

def _router(providers):
    default_models = {
        "ollama": "llama3.2",
        "anthropic": "claude-sonnet-5",
        "openai": "gpt-4o",
    }
    model_owner = {"claude-sonnet-5": "anthropic", "gpt-4o": "openai"}
    chains = {
        "simple": ["ollama", "anthropic", "openai"],
        "complex": ["anthropic", "openai", "ollama"],
    }
    return Router(
        providers=providers,
        default_models=default_models,
        model_owner=model_owner,
        classifier=ComplexityClassifier(400),
        strategy=CostStrategy(chains),
        fallback_chain=FallbackChain(providers),
        fallback_order=["ollama", "anthropic", "openai"],
    )


async def test_router_simple_routes_to_ollama():
    r = _router({"ollama": FakeProvider("ollama"), "anthropic": FakeProvider("anthropic")})
    result = await r.route([Message("user", "hi")])
    assert result.response.provider == "ollama"
    assert result.classification == "simple"


async def test_router_complex_routes_to_anthropic():
    r = _router(
        {
            "ollama": FakeProvider("ollama"),
            "anthropic": FakeProvider("anthropic"),
            "openai": FakeProvider("openai"),
        }
    )
    result = await r.route([Message("user", "please analyze and explain the architecture")])
    assert result.classification == "complex"
    assert result.response.provider == "anthropic"


async def test_router_requested_model_routes_to_owner():
    r = _router({"ollama": FakeProvider("ollama"), "openai": FakeProvider("openai")})
    result = await r.route([Message("user", "hi")], requested_model="gpt-4o")
    assert result.response.provider == "openai"
    assert result.classification is None


async def test_router_skips_unavailable_provider():
    # complex prefers anthropic, but it's not registered -> openai serves.
    r = _router({"ollama": FakeProvider("ollama"), "openai": FakeProvider("openai")})
    result = await r.route([Message("user", "analyze " * 100)])
    assert result.classification == "complex"
    assert result.response.provider == "openai"


async def test_router_falls_back_on_provider_error():
    r = _router(
        {
            "ollama": FakeProvider("ollama", fail_reason="upstream_timeout"),
            "anthropic": FakeProvider("anthropic"),
        }
    )
    result = await r.route([Message("user", "hi")])  # simple -> ollama first
    assert result.response.provider == "anthropic"
    assert result.fallback_reason == "upstream_timeout"
    assert result.providers_attempted == ["ollama", "anthropic"]
