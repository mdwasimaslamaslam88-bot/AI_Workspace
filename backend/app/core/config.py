from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_TITLE: str = "AI Workspace Backend"
    APP_VERSION: str = "0.1.0"
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    DATABASE_URL: PostgresDsn | None = None
    REDIS_URL: RedisDsn | None = None
    OLLAMA_BASE_URL: AnyHttpUrl | None = None
    OLLAMA_MODEL: str | None = None
    DATABASE_CONNECT_TIMEOUT_SECONDS: float = Field(default=3.0, gt=0)
    REDIS_CONNECT_TIMEOUT_SECONDS: float = Field(default=3.0, gt=0)
    OLLAMA_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0)
    @field_validator("DATABASE_URL", "REDIS_URL", "OLLAMA_BASE_URL", mode="before")
    @classmethod
    def normalize_blank_urls(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def require_asyncpg_scheme(cls, value):
        if value is not None and not str(value).startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// scheme")
        return value

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
