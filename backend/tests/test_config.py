import pytest
from pydantic import ValidationError
from app.core.config import Settings

def test_settings_preserve_application_defaults():
    settings = Settings(_env_file=None)
    assert settings.APP_TITLE == "AI Workspace Backend"
    assert settings.APP_VERSION == "0.1.0"
    assert settings.BACKEND_CORS_ORIGINS == ["http://localhost:3000"]
    assert settings.DATABASE_URL is None
    assert settings.REDIS_URL is None
    assert settings.OLLAMA_BASE_URL is None

def test_blank_runtime_urls_are_unconfigured():
    settings = Settings(_env_file=None, DATABASE_URL="", REDIS_URL="", OLLAMA_BASE_URL="")
    assert settings.DATABASE_URL is None
    assert settings.REDIS_URL is None
    assert settings.OLLAMA_BASE_URL is None

def test_settings_validate_runtime_urls():
    settings = Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/workspace", REDIS_URL="redis://:password@localhost:6379/0", OLLAMA_BASE_URL="http://localhost:11434")
    assert str(settings.DATABASE_URL).startswith("postgresql+asyncpg://")
    assert str(settings.REDIS_URL).startswith("redis://")
    assert str(settings.OLLAMA_BASE_URL) == "http://localhost:11434/"

@pytest.mark.parametrize(("field", "value"), [("DATABASE_URL", "postgresql://user:password@localhost:5432/workspace"), ("DATABASE_URL", "not-a-postgres-url"), ("REDIS_URL", "not-a-redis-url"), ("OLLAMA_BASE_URL", "not-a-http-url")])
def test_settings_reject_invalid_runtime_urls(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
