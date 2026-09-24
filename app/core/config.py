from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    The local profile is intentionally convenient, but production must provide
    its own secrets and must not enable the test QR bypass.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Tavonza AI"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://tavonza:tavonza@localhost:5432/tavonza"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    jarvis_temperature: float = 0.2

    # Authentication. The development value is deliberately recognizable so a
    # production deployment can fail fast instead of silently using it.
    jwt_secret: str = "tavonza-local-development-only-change-me"
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "tavonza-ai"
    jwt_audience: str = "tavonza-api"
    jwt_ttl_seconds: int = 3600

    # Local-only bootstrap for the web console. Leave empty unless explicitly
    # running a local development profile.
    dev_auth_token: str = ""
    allow_test_qr: bool = False
    allow_real_qr: bool = False
    allow_test_context: bool = False

    # The AI layer talks to the backend through these routes. It never imports
    # the application's SQLAlchemy session for operational context.
    backend_api_url: str = "http://127.0.0.1:8000"
    backend_api_token: str = ""
    backend_timeout_seconds: float = 8.0

    # Defensive bounds for the local AI test surface.
    ai_max_query_length: int = 4000
    ai_max_context_items: int = 200
    ai_max_output_tokens: int = 1200
    reservation_duration_minutes: int = 120

    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url", mode="before")
    @classmethod
    def use_async_driver(cls, value: object) -> object:
        """Force the asyncpg driver for a bare PostgreSQL URL."""
        if isinstance(value, str) and value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    @field_validator("backend_api_url")
    @classmethod
    def normalize_backend_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}


settings = Settings()
