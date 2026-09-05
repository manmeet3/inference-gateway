from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    redis_url: str = "redis://localhost:6379"

    # Ollama (local)
    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "llama3.2"

    # Anthropic (cloud) — key optional; provider is skipped when unset
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-5"

    # OpenAI (cloud) — key optional; provider is skipped when unset
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # Cloud providers require a max output token cap.
    cloud_max_tokens: int = 1024

    # Routing — a query at/above this many characters is treated as "complex".
    classifier_complex_char_threshold: int = 400

    # Caching (Phase 3)
    enable_cache: bool = True
    exact_cache_ttl_seconds: int = 3600
    semantic_cache_ttl_seconds: int = 86400
    semantic_cache_threshold: float = 0.92
    semantic_cache_model_name: str = "all-MiniLM-L6-v2"

    log_level: str = "INFO"
    otlp_endpoint: str | None = None
    service_name: str = "inference-gateway"


    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
