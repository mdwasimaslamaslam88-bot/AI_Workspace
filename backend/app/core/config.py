# app/core/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_TITLE: str = "AI Workspace Backend"
    APP_VERSION: str = "0.1.0"

    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000"
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

settings = Settings()