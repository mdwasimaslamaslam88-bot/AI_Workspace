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


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.20.30.40:11434",
        "http://[::1]:11434",
    ],
)
def test_ollama_runtime_accepts_only_loopback_origins(url):
    settings = Settings(_env_file=None, OLLAMA_BASE_URL=url)

    assert settings.OLLAMA_BASE_URL is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0:11434",
        "http://192.168.1.20:11434",
        "http://example.com:11434",
        "http://localhost.example.com:11434",
        "http://user:secret@localhost:11434",
        "http://localhost:11434/private/path",
        "http://localhost:11434?target=external",
        "http://localhost:11434#fragment",
    ],
)
def test_ollama_runtime_rejects_nonlocal_or_unsafe_origins(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, OLLAMA_BASE_URL=url)
