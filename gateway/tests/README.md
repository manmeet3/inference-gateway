# Test Suite & Verification

This directory contains the automated test suite for the LLM Inference Gateway.

---

## Testing Philosophy

* **100% Isolated Unit Tests**: All unit tests run entirely in-memory with zero network calls, zero live API dependencies, and zero heavy model downloads.
* **Test Doubles**:
  * `FakeProvider`: In-memory provider simulating successful completions, specific token usage, or configurable `ProviderError` failures.
  * `FakeEmbedder`: In-memory embedder generating deterministic unit vectors without loading PyTorch or neural weights.
  * `FakeRedis`: In-memory async Redis stand-in supporting string keys, sets, pipelines, and simulated connection errors.

---

## Test Directory Structure

```
gateway/tests/
├── README.md               # Test documentation & execution guide
└── unit/
    ├── test_cache.py       # Exact & semantic caching tests (6 tests)
    └── test_routing.py     # Classifier, strategy, fallback & router tests (12 tests)
```

---

## Running the Tests

### Option 1: Standard Pytest
```bash
cd gateway
pytest
```

### Option 2: Standalone Async Test Runner
```bash
python3 -c '
import sys; sys.path.insert(0, "gateway")
import asyncio
from tests.unit.test_cache import (
    test_exact_cache_hit_and_miss, test_semantic_cache_high_similarity_hit,
    test_semantic_cache_low_similarity_miss, test_cache_manager_exact_priority,
    test_cache_manager_semantic_fallback, test_cache_manager_exception_shielding
)
from tests.unit.test_routing import (
    test_classifier_short_is_simple, test_classifier_long_is_complex,
    test_classifier_keyword_is_complex, test_cost_strategy_select,
    test_fallback_first_success, test_fallback_falls_through,
    test_fallback_all_fail_raises, test_router_simple_routes_to_ollama,
    test_router_complex_routes_to_anthropic, test_router_requested_model_routes_to_owner,
    test_router_skips_unavailable_provider, test_router_falls_back_on_provider_error
)

async def run_all():
    await test_exact_cache_hit_and_miss()
    await test_semantic_cache_high_similarity_hit()
    await test_semantic_cache_low_similarity_miss()
    await test_cache_manager_exact_priority()
    await test_cache_manager_semantic_fallback()
    await test_cache_manager_exception_shielding()
    test_classifier_short_is_simple()
    test_classifier_long_is_complex()
    test_classifier_keyword_is_complex()
    test_cost_strategy_select()
    await test_fallback_first_success()
    await test_fallback_falls_through()
    await test_fallback_all_fail_raises()
    await test_router_simple_routes_to_ollama()
    await test_router_complex_routes_to_anthropic()
    await test_router_requested_model_routes_to_owner()
    await test_router_skips_unavailable_provider()
    await test_router_falls_back_on_provider_error()
    print("All 18 unit tests passed successfully!")

asyncio.run(run_all())
'
```
