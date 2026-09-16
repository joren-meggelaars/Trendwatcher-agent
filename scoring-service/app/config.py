from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./scoring.db"

    voyage_api_key: str = ""
    voyage_model: str = "voyage-3"
    embedding_provider: Literal["voyage", "fake"] = "voyage"
    embedding_dim: int = 1024

    default_user_id: str = "joren"

    # Instroom: kandidaat -> actief wanneer minstens `source_activation_min_high_score`
    # van de laatste `source_activation_window` items een relevance_score boven
    # `source_activation_score_threshold` hebben.
    source_activation_window: int = 5
    source_activation_min_high_score: int = 3
    source_activation_score_threshold: float = 0.6

    # Krimp: actief -> gedeactiveerd wanneer minstens `source_deactivation_min_negative`
    # van de laatste `source_deactivation_window` items als "niet_interessant" zijn
    # gelabeld, OF de running_avg_score onder `source_deactivation_avg_score_threshold` zakt.
    source_deactivation_window: int = 10
    source_deactivation_min_negative: int = 8
    source_deactivation_avg_score_threshold: float = 0.3

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
