from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./scoring.db"

    voyage_api_key: str = ""
    voyage_model: str = "voyage-3"
    embedding_provider: Literal["voyage", "fake"] = "voyage"
    embedding_dim: int = 1024

    default_user_id: str = "joren"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
