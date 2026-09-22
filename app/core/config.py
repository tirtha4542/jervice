from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Tavonza AI"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://tavonza:tavonza@localhost:5432/tavonza"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    jarvis_temperature: float = 0.2

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
