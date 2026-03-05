from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://polyedge:polyedge@localhost:5432/polyedge"
    perplexity_api_key: str = ""
    grok_api_key: str = ""

    class Config:
        env_prefix = "POLYEDGE_"


settings = Settings()
engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
