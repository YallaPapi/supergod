from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://polyedge:polyedge@localhost:5432/polyedge"
    perplexity_api_key: str = ""
    grok_api_key: str = ""
    prediction_metrics_cutoff: str = ""
    prediction_resolution_sources: str = "polymarket_api"
    supergod_orchestrator_url: str = "ws://89.167.99.187:8080/ws/client"
    supergod_repo_path: str = "/opt/polyedge"
    research_market_limit: int = 25

    model_config = SettingsConfigDict(
        env_prefix="POLYEDGE_", extra="ignore", env_file=".env", env_file_encoding="utf-8",
    )


settings = Settings()
engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
