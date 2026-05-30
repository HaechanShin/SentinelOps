from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ai_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_max_retries: int = 3

    # Used when AI_PROVIDER is ollama, openai-compatible, or local.
    local_llm_backend: str = "ollama"
    local_llm_base_url: str = "http://localhost:11434"
    local_llm_api_key: str = "ollama"
    local_llm_model: str = "qwen3.6:latest"
    local_llm_temperature: float = 0.2
    local_llm_context_tokens: int = 16384
    local_llm_top_p: float = 0.9
    local_llm_top_k: int = 20
    local_llm_min_p: float = 0.0
    local_llm_repeat_penalty: float = 1.05
    local_llm_presence_penalty: float = 0.0
    local_llm_timeout_seconds: float = 120.0
    local_llm_max_retries: int = 3
    local_llm_keep_alive: str = "10m"
    local_llm_think: bool = False

    steam_api_key: str = ""
    steam_app_id: int = 578080

    database_url: str = "postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops"
    database_url_sync: str = "postgresql://sentinelops:sentinelops@localhost:5432/sentinelops"
    redis_url: str = "redis://localhost:6379/0"

    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_app_token: str = ""
    slack_alert_channel: str = "#community-alerts"

    api_secret_key: str = ""
    internal_api_url: str = "http://app:8000"

    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "sentinelops"

    log_level: str = "INFO"
    polling_interval_seconds: int = 3600
    sentiment_drop_threshold: float = 0.3
    spike_multiplier: float = 2.0
    rolling_window_minutes: int = 60
    alert_cooldown_minutes: int = 120

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
