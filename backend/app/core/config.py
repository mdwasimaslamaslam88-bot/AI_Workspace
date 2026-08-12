import ipaddress
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DatabaseSslMode = Literal["disable", "require", "verify-ca", "verify-full"]


class Settings(BaseSettings):
    APP_TITLE: str = "AI Workspace Backend"
    APP_VERSION: str = "0.1.0"
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    DATABASE_URL: PostgresDsn | None = None
    REDIS_URL: RedisDsn | None = None
    OLLAMA_BASE_URL: AnyHttpUrl | None = None
    DATABASE_CONNECT_TIMEOUT_SECONDS: float = Field(default=3.0, gt=0)
    DATABASE_POOL_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0)
    DATABASE_COMMAND_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)
    DATABASE_POOL_SIZE: int = Field(default=5, ge=1)
    DATABASE_MAX_OVERFLOW: int = Field(default=10, ge=0)
    DATABASE_SSL_MODE: DatabaseSslMode = "verify-full"
    DATABASE_SSL_ROOT_CERT: str | None = None
    TEST_DATABASE_URL: PostgresDsn | None = None
    REDIS_CONNECT_TIMEOUT_SECONDS: float = Field(default=3.0, gt=0)
    OLLAMA_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0)

    @field_validator(
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "REDIS_URL",
        "OLLAMA_BASE_URL",
        "DATABASE_SSL_ROOT_CERT",
        mode="before",
    )
    @classmethod
    def normalize_blank_optional_values(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("DATABASE_URL", "TEST_DATABASE_URL")
    @classmethod
    def require_asyncpg_scheme(cls, value):
        if value is not None and not str(value).startswith("postgresql+asyncpg://"):
            raise ValueError("PostgreSQL URLs must use the postgresql+asyncpg:// scheme")
        return value

    @field_validator("OLLAMA_BASE_URL")
    @classmethod
    def require_loopback_ollama_url(cls, value):
        if value is None:
            return None
        if value.username is not None or value.password is not None:
            raise ValueError("OLLAMA_BASE_URL must not include credentials")
        if value.path not in (None, "", "/") or value.query or value.fragment:
            raise ValueError("OLLAMA_BASE_URL must identify only a local origin")

        host = value.host
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if host == "localhost":
            return value
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError(
                "OLLAMA_BASE_URL must use localhost or a loopback IP address"
            ) from exc
        if not address.is_loopback:
            raise ValueError(
                "OLLAMA_BASE_URL must use localhost or a loopback IP address"
            )
        return value

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
