from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Tavonza AI"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://tavonza:tavonza@localhost:5432/tavonza"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    jarvis_temperature: float = 0.2
    jwt_secret: str = "tavonza-dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url", mode="before")
    @classmethod
    def use_async_driver(cls, value: object) -> object:
        """Force the asyncpg driver.

        A bare ``postgresql://`` URL makes SQLAlchemy fall back to psycopg2,
        which is not a dependency of this project, so engine creation raises
        ``ModuleNotFoundError: No module named 'psycopg2'``.
        """
        if isinstance(value, str) and value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value


settings = Settings()
