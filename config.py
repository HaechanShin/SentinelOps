from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "SentinelOps/1.0"
    steam_api_key: str = ""
    steam_app_id: int = 578080

    database_url: str = "postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops"
    database_url_sync: str = "postgresql://sentinelops:sentinelops@localhost:5432/sentinelops"
    redis_url: str = "redis://localhost:6379/0"

    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_alert_channel: str = "#community-alerts"

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
