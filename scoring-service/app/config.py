import secrets
from typing import Literal

from pydantic import Field, field_validator
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

    # Local admin GUI (/admin/*). Single fixed account, session cookie auth —
    # deliberately no OAuth/JWT, this never leaves the local network.
    admin_username: str = "admin"
    # Bcrypt hash, never plaintext. Generate one with scripts/hash_admin_password.py.
    admin_password_hash: str = ""
    # Signs the session cookie. If left unset in .env, a random key is
    # generated per process start — safe by default, but existing sessions
    # won't survive a restart. Set a fixed value in .env for persistent
    # sessions across restarts/deploys.
    session_secret_key: str = Field(default_factory=lambda: secrets.token_hex(32))

    # Base URL of the scheduler's small internal trigger server (see
    # scheduler/trigger_server.py), used only by the admin GUI's "verstuur nu"
    # button. Reachable via the shared Docker network, not published to the
    # host — never exposed to the internet.
    scheduler_url: str = "http://scheduler:8001"

    @field_validator("session_secret_key", mode="after")
    @classmethod
    def _generate_secret_if_blank(cls, value: str) -> str:
        # An explicit-but-empty SESSION_SECRET_KEY= in .env parses as "" and
        # would otherwise override the default_factory with an empty,
        # forgeable session-signing key.
        return value or secrets.token_hex(32)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
