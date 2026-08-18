import ipaddress
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DatabaseSslMode = Literal["disable", "require", "verify-ca", "verify-full"]
MAX_GENERATION_ACTIVE_PER_PROCESS = 8


class Settings(BaseSettings):
    APP_TITLE: str = "AI Workspace Backend"
    APP_VERSION: str = "0.1.0"
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    DATABASE_URL: PostgresDsn | None = None
    REDIS_URL: RedisDsn | None = None
    OLLAMA_BASE_URL: AnyHttpUrl | None = None
    USER_PROVISIONING_TOKEN_DIGEST: str | None = Field(
        default=None,
        strict=True,
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
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
    OLLAMA_LOCAL_MODEL_ALLOWLIST: tuple[str, ...] = ()
    OLLAMA_GENERATION_TIMEOUT_SECONDS: float = Field(
        default=120.0,
        gt=0,
        allow_inf_nan=False,
    )
    GENERATION_MAX_ACTIVE_PER_PROCESS: int = Field(
        default=1,
        strict=True,
        ge=1,
        le=MAX_GENERATION_ACTIVE_PER_PROCESS,
    )

    @field_validator(
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "REDIS_URL",
        "OLLAMA_BASE_URL",
        "DATABASE_SSL_ROOT_CERT",
        "USER_PROVISIONING_TOKEN_DIGEST",
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

    @field_validator("OLLAMA_GENERATION_TIMEOUT_SECONDS", mode="before")
    @classmethod
    def reject_boolean_generation_timeout(cls, value):
        if isinstance(value, bool):
            raise ValueError("OLLAMA_GENERATION_TIMEOUT_SECONDS must be numeric")
        return value

    @field_validator("OLLAMA_LOCAL_MODEL_ALLOWLIST")
    @classmethod
    def validate_local_model_allowlist(cls, value):
        seen: set[str] = set()
        for reference in value:
            if not reference or reference != reference.strip():
                raise ValueError(
                    "OLLAMA_LOCAL_MODEL_ALLOWLIST entries must be exact nonblank references"
                )
            if reference in seen:
                raise ValueError(
                    "OLLAMA_LOCAL_MODEL_ALLOWLIST entries must be unique"
                )
            seen.add(reference)
        return value

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
