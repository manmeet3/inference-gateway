# Routing & Fallback Subsystem

The routing subsystem inspects incoming requests, determines optimal provider and model assignments based on cost and query complexity, and executes requests through a resilient fallback chain.

---

## Routing Decision Flow

```
                         [ POST /v1/chat Request ]
                                     │
                                     ▼
                        [ Model Requested in Body? ]
                                     │
                    ┌────────────────┴────────────────┐
                    │ (Yes)                           │ (No)
                    ▼                                 ▼
        [ Map Model to Owner ]            [ ComplexityClassifier ]
        - gpt-4o -> openai                - Char count >= 400?
        - claude-sonnet-5 -> anthropic    - Contains reasoning keywords?
        - others -> ollama                            │
                    │                                 ▼
                    │                     [ CostStrategy Chains ]
                    │                     - simple -> [ollama, anthropic, openai]
                    │                     - complex -> [anthropic, openai, ollama]
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                                     ▼
                    [ Filter Registered Providers ]
                    (Exclude unkeyed cloud providers)
                                     │
                                     ▼
                     [ FallbackChain Execution ]
                     - Try candidate (provider, model)
                     - On ProviderError -> try next candidate
                     - All fail -> AllProvidersFailedError (503)
```

---

## 1. Complexity Classifier (`classifier.py`)

The classifier uses lightweight heuristics to assign a `"simple"` or `"complex"` classification:

* **Character Length**: If total user prompt length $\ge \text{threshold}$ (default: 400 characters, configurable via `CLASSIFIER_COMPLEX_CHAR_THRESHOLD`).
* **Reasoning Keywords**: If prompt contains intent signals:
  `analyze`, `explain`, `debug`, `refactor`, `prove`, `design`, `architecture`, `optimize`, `algorithm`, `implement`, `compare`, `evaluate`, `step by step`, `write code`.
* **Classification Output**: `"complex"` if length or keyword match; otherwise `"simple"`.

---

## 2. Cost Strategy (`strategies.py`)

Maps query complexity to prioritized provider candidate chains:

* **`simple` Chain**: `["ollama", "anthropic", "openai"]` (prioritizes zero-cost local inference).
* **`complex` Chain**: `["anthropic", "openai", "ollama"]` (prioritizes capable cloud reasoning models).

---

## 3. Fallback Chain (`strategies.py`)

* **Execution**: Steps sequentially through candidate `(provider_name, model)` pairs.
* **Error Interception**: Catches `ProviderError` (e.g. `upstream_timeout`, `provider_unavailable`, `model_not_found`, `upstream_error`), records the failure reason, and immediately tries the next candidate.
* **Failure Handling**: If all candidates fail, raises `AllProvidersFailedError`, which the API layer translates into an HTTP 503 response.

---

## 4. Router (`router.py`)

* **Model Ownership**: Directs explicit model requests (e.g., `model: "gpt-4o"`) directly to the owning provider, maintaining the canonical fallback sequence for outages.
* **Graceful Degradation**: Dynamically filters candidate chains against currently registered providers (e.g., if cloud API keys are not supplied in the environment, cloud candidates are omitted seamlessly).
