# Caching Subsystem

The caching subsystem provides dual-layer response caching sitting ahead of the routing and upstream provider layers to reduce latency (<15ms) and eliminate duplicate upstream inference costs.

---

## Cache Lookup Pipeline

```
                    [ Incoming Request: messages, model ]
                                      │
                                      ▼
                        [ CacheManager.get(...) ]
                                      │
           ┌──────────────────────────┴──────────────────────────┐
           │                                                     │
           ▼ (Layer 1)                                           ▼ (Layer 2)
+-----------------------+                             +-----------------------+
|      Exact Cache      |                             |    Semantic Cache     |
| - SHA-256 Hash        |                             | - MiniLM Embeddings   |
| - Sub-5ms Redis GET   |                             | - Cosine Sim >= 0.92  |
+-----------+-----------+                             +-----------+-----------+
            │                                                     │
            ├──────▶ [ Exact Hit: Return Cached Response ]        │
            │                                                     │
            └──────▶ [ Miss: Proceed to Layer 2 ] ───────────────┼──────▶ [ Semantic Hit: Return Cached ]
                                                                 │
                                                                 └──────▶ [ Miss: Proceed to Router ]
```

---

## 1. Exact Cache (`exact.py`)

* **Key Generation**: Deterministic SHA-256 hash computed over sorted JSON representation of message roles, contents, and model identifier:
  ```
  key = "cache:exact:" + sha256(canonical_json({"model": model, "messages": messages}))
  ```
* **Storage**: Serialized JSON string containing response text, model, token counts, and provider name.
* **TTL**: Configurable via `EXACT_CACHE_TTL_SECONDS` (default: 3600 seconds = 1 hour).
* **Performance**: Direct Redis $O(1)$ key-value lookup returning in ~1-3ms.

---

## 2. Semantic Cache (`semantic.py`)

* **Embedding Model**: Local `all-MiniLM-L6-v2` via `sentence-transformers` (runs entirely on CPU with zero external API calls).
* **Vector Normalization**: Vectors are unit-normalized upon encoding so cosine similarity is computed directly via dot product:
  $$\text{Cosine Similarity}(u, v) = \frac{u \cdot v}{\|u\|_2 \|v\|_2} = \sum_{i} u_i v_i$$
* **Storage & Indexing**:
  * Individual entries: `cache:semantic:entry:<id>` (contains prompt, model, embedding vector, and response).
  * Active key index: `cache:semantic:keys` (Redis set tracking active entries).
* **Threshold**: Minimum cosine similarity cutoff configurable via `SEMANTIC_CACHE_THRESHOLD` (default: `0.92`).
* **TTL**: Configurable via `SEMANTIC_CACHE_TTL_SECONDS` (default: 86400 seconds = 24 hours).

---

## 3. Cache Manager (`manager.py`)

* **Orchestration**: Evaluates Layer 1 (Exact) first; only evaluates Layer 2 (Semantic) if Layer 1 misses.
* **Write Path**: On non-cached upstream completion, populates both Exact and Semantic caches.
* **Exception Shielding**: If Redis is offline or encounters connection loss, `CacheManager` catches the exception, logs a structured warning, and yields a cache miss—ensuring inference continues without returning 500 errors to clients.
