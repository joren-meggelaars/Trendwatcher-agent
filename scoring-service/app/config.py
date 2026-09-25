import secrets
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
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
    # Mark the session cookie Secure (only sent over HTTPS). Set true as soon as
    # the GUI is served over HTTPS (behind a reverse proxy); left false, a
    # plain-http LAN address keeps working. Console-only, like all security settings.
    session_cookie_secure: bool = False
    # The public address of the GUI (what FEEDBACK_BASE_URL is in the mail), e.g.
    # https://trend.example.com. Used to accept form posts from that origin when
    # a reverse proxy rewrites the Host header. Empty = only the Host header.
    public_base_url: str = ""
    # Switches off the "only public addresses" check of app/safe_fetch.py (batch-add,
    # discovery). For a local test setup only; console-only, never on the VM.
    safe_fetch_allow_private: bool = False

    # Base URL of the scheduler's small internal trigger server (see
    # scheduler/trigger_server.py), used only by the admin GUI's "verstuur nu"
    # button. Reachable via the shared Docker network, not published to the
    # host — never exposed to the internet.
    scheduler_url: str = "http://scheduler:8001"

    # Optional sign-in through Authentik (app/oidc.py, the identity-platform repo).
    # Empty issuer = off: only the username/password login. With it set, the
    # password login stays as the emergency way in.
    # Issuer = what the browser sees: https://<authentik host>/application/o/trendwatcher/
    oidc_issuer: str = ""
    oidc_client_id: str = "trendwatcher"
    oidc_client_secret: str = ""
    # Every address the GUI is opened on, ending in /admin/oidc/callback (space separated).
    oidc_redirect_uris: str = ""
    # How the container reaches Authentik over the shared docker network.
    oidc_internal_url: str = ""
    oidc_admin_group: str = "trendwatcher-admin"
    oidc_session_days: int = 7

    @property
    def oidc_redirect_uri_list(self) -> list[str]:
        return [u for u in self.oidc_redirect_uris.replace(",", " ").split() if u]

    @model_validator(mode="after")
    def _check_oidc(self) -> "Settings":
        """A half-filled OIDC block stops the start, not the first sign-in."""
        if not self.oidc_issuer:
            return self

        def secure(url: str) -> bool:
            parsed = urlsplit(url)
            return parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))

        missing = [
            name
            for name, value in (
                ("OIDC_CLIENT_ID", self.oidc_client_id),
                ("OIDC_CLIENT_SECRET", self.oidc_client_secret),
                ("OIDC_REDIRECT_URIS", self.oidc_redirect_uri_list),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"OIDC_ISSUER is set, so these are required too: {', '.join(missing)}")
        if not secure(self.oidc_issuer):
            raise ValueError("OIDC_ISSUER must be an https URL (http only for localhost)")
        for uri in self.oidc_redirect_uri_list:
            if not secure(uri) or not uri.endswith("/admin/oidc/callback"):
                raise ValueError(f"OIDC_REDIRECT_URIS entry {uri!r} must be an https URL ending in /admin/oidc/callback")
        if self.oidc_internal_url and urlsplit(self.oidc_internal_url).scheme not in ("http", "https"):
            raise ValueError("OIDC_INTERNAL_URL must be an http(s) URL")
        if not 1 <= self.oidc_session_days <= 90:
            raise ValueError("OIDC_SESSION_DAYS must be between 1 and 90")
        return self

    @field_validator("session_secret_key", mode="after")
    @classmethod
    def _generate_secret_if_blank(cls, value: str) -> str:
        # An explicit-but-empty SESSION_SECRET_KEY= in .env parses as "" and
        # would otherwise override the default_factory with an empty,
        # forgeable session-signing key.
        return value or secrets.token_hex(32)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
