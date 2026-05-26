from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
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
    polling_interval_seconds: int = 300
    sentiment_drop_threshold: float = 0.3
    spike_multiplier: float = 2.0
    rolling_window_minutes: int = 60

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
