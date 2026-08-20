from pathlib import Path

import pytest
from pydantic import ValidationError
from app.core.config import (
    MAX_GENERATION_ACTIVE_PER_PROCESS,
    MAX_GENERATION_DURATION_SECONDS,
    MAX_MODEL_LIST_DISCOVERY_SECONDS,
    MAX_MODEL_LIST_RESPONSE_BYTES,
    MAX_OLLAMA_CATALOG_LIST_MODELS,
    MAX_OLLAMA_CATALOG_RESPONSE_BYTES,
    MAX_OLLAMA_GENERATION_REQUEST_BYTES,
    MAX_OLLAMA_GENERATION_RESPONSE_BYTES,
    MAX_REQUEST_BODY_BYTES,
    Settings,
)

def test_settings_preserve_application_defaults():
    settings = Settings(_env_file=None)
    assert settings.APP_TITLE == "AI Workspace Backend"
    assert settings.APP_VERSION == "0.1.0"
    assert settings.BACKEND_CORS_ORIGINS == ["http://localhost:3000"]
    assert settings.DATABASE_URL is None
    assert settings.REDIS_URL is None
    assert settings.OLLAMA_BASE_URL is None
    assert settings.MODEL_LIST_MAX_DISCOVERY_SECONDS == 60.0
    assert settings.MODEL_LIST_MAX_RESPONSE_BYTES == 1_048_576
    assert settings.OLLAMA_CATALOG_MAX_RESPONSE_BYTES == 1_048_576
    assert settings.OLLAMA_CATALOG_MAX_LIST_MODELS == 256
    assert settings.OLLAMA_GENERATION_MAX_REQUEST_BYTES == 1_048_576
    assert settings.OLLAMA_GENERATION_MAX_RESPONSE_BYTES == 262_144
    assert settings.OLLAMA_LOCAL_MODEL_ALLOWLIST == ()
    assert settings.OLLAMA_GENERATION_TIMEOUT_SECONDS == 120.0
    assert settings.GENERATION_MAX_ACTIVE_PER_PROCESS == 1
    assert settings.GENERATION_MAX_DURATION_SECONDS == 180.0
    assert settings.REQUEST_MAX_BODY_BYTES == 262_144
    assert settings.USER_PROVISIONING_TOKEN_DIGEST is None

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


def test_local_model_allowlist_accepts_exact_unique_references():
    settings = Settings(
        _env_file=None,
        OLLAMA_LOCAL_MODEL_ALLOWLIST=[
            "verified-local:latest",
            "/private/models/exact:14b",
        ],
    )

    assert settings.OLLAMA_LOCAL_MODEL_ALLOWLIST == (
        "verified-local:latest",
        "/private/models/exact:14b",
    )


@pytest.mark.parametrize(
    "value",
    [[""], [" whitespace"], ["duplicate", "duplicate"], [3]],
)
def test_local_model_allowlist_rejects_unsafe_entries(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, OLLAMA_LOCAL_MODEL_ALLOWLIST=value)


@pytest.mark.parametrize(
    "value",
    [0, -1, True, "not-a-number", float("inf"), float("-inf"), float("nan")],
)
def test_generation_timeout_must_be_positive_numeric(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            OLLAMA_GENERATION_TIMEOUT_SECONDS=value,
        )


def test_user_provisioning_digest_accepts_only_lowercase_sha256():
    digest = "a" * 64

    settings = Settings(
        _env_file=None,
        USER_PROVISIONING_TOKEN_DIGEST=digest,
    )

    assert settings.USER_PROVISIONING_TOKEN_DIGEST == digest


def test_blank_user_provisioning_digest_disables_provisioning():
    settings = Settings(
        _env_file=None,
        USER_PROVISIONING_TOKEN_DIGEST=" \t ",
    )

    assert settings.USER_PROVISIONING_TOKEN_DIGEST is None


@pytest.mark.parametrize(
    "value",
    [
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "a" * 64 + "\n",
        "g" * 64,
        "P" * 43,
        True,
        123,
        ["a" * 64],
        {"digest": "a" * 64},
    ],
)
def test_user_provisioning_digest_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            USER_PROVISIONING_TOKEN_DIGEST=value,
        )


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_GENERATION_ACTIVE_PER_PROCESS + 1,
    ],
)
def test_generation_process_cap_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            GENERATION_MAX_ACTIVE_PER_PROCESS=value,
        )


@pytest.mark.parametrize(
    "value",
    [1, MAX_GENERATION_ACTIVE_PER_PROCESS],
)
def test_generation_process_cap_accepts_documented_bounds(value):
    settings = Settings(
        _env_file=None,
        GENERATION_MAX_ACTIVE_PER_PROCESS=value,
    )

    assert settings.GENERATION_MAX_ACTIVE_PER_PROCESS == value


@pytest.mark.parametrize(
    "value",
    [0.001, 1, 180.0, MAX_GENERATION_DURATION_SECONDS],
)
def test_generation_max_duration_accepts_positive_finite_values(value):
    configured = Settings(
        _env_file=None,
        GENERATION_MAX_DURATION_SECONDS=value,
    )

    assert configured.GENERATION_MAX_DURATION_SECONDS == float(value)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "180.0",
        0,
        -1,
        float("nan"),
        float("inf"),
        float("-inf"),
        MAX_GENERATION_DURATION_SECONDS + 0.0001,
    ],
)
def test_generation_max_duration_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, GENERATION_MAX_DURATION_SECONDS=value)


def test_generation_max_duration_parses_environment_value(monkeypatch):
    monkeypatch.setenv("GENERATION_MAX_DURATION_SECONDS", "42.5")

    configured = Settings(_env_file=None)

    assert configured.GENERATION_MAX_DURATION_SECONDS == 42.5


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_REQUEST_BODY_BYTES + 1,
    ],
)
def test_request_body_cap_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            REQUEST_MAX_BODY_BYTES=value,
        )


@pytest.mark.parametrize(
    "value",
    [1, 262_144, MAX_REQUEST_BODY_BYTES],
)
def test_request_body_cap_accepts_documented_bounds(value):
    configured = Settings(
        _env_file=None,
        REQUEST_MAX_BODY_BYTES=value,
    )

    assert configured.REQUEST_MAX_BODY_BYTES == value


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_MODEL_LIST_RESPONSE_BYTES + 1,
    ],
)
def test_model_list_response_cap_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            MODEL_LIST_MAX_RESPONSE_BYTES=value,
        )


@pytest.mark.parametrize(
    "value",
    [1, 262_144, MAX_MODEL_LIST_RESPONSE_BYTES],
)
def test_model_list_response_cap_accepts_documented_bounds(value):
    configured = Settings(
        _env_file=None,
        MODEL_LIST_MAX_RESPONSE_BYTES=value,
    )

    assert configured.MODEL_LIST_MAX_RESPONSE_BYTES == value


@pytest.mark.parametrize(
    "value",
    [0.1, 1, 73.25, MAX_MODEL_LIST_DISCOVERY_SECONDS],
)
def test_model_list_discovery_deadline_accepts_documented_values(value):
    configured = Settings(
        _env_file=None,
        MODEL_LIST_MAX_DISCOVERY_SECONDS=value,
    )

    assert configured.MODEL_LIST_MAX_DISCOVERY_SECONDS == float(value)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "60.0",
        0,
        -1,
        float("nan"),
        float("inf"),
        float("-inf"),
        MAX_MODEL_LIST_DISCOVERY_SECONDS + 0.0001,
    ],
)
def test_model_list_discovery_deadline_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            MODEL_LIST_MAX_DISCOVERY_SECONDS=value,
        )


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_OLLAMA_CATALOG_RESPONSE_BYTES + 1,
    ],
)
def test_ollama_catalog_response_cap_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            OLLAMA_CATALOG_MAX_RESPONSE_BYTES=value,
        )


@pytest.mark.parametrize(
    "value",
    [1, 262_144, MAX_OLLAMA_CATALOG_RESPONSE_BYTES],
)
def test_ollama_catalog_response_cap_accepts_documented_bounds(value):
    configured = Settings(
        _env_file=None,
        OLLAMA_CATALOG_MAX_RESPONSE_BYTES=value,
    )

    assert configured.OLLAMA_CATALOG_MAX_RESPONSE_BYTES == value


@pytest.mark.parametrize(
    "value",
    [True, False, "1", 1.0, 0, -1, MAX_OLLAMA_CATALOG_LIST_MODELS + 1],
)
def test_ollama_catalog_list_model_cap_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            OLLAMA_CATALOG_MAX_LIST_MODELS=value,
        )


@pytest.mark.parametrize(
    "value",
    [1, 64, MAX_OLLAMA_CATALOG_LIST_MODELS],
)
def test_ollama_catalog_list_model_cap_accepts_documented_bounds(value):
    configured = Settings(
        _env_file=None,
        OLLAMA_CATALOG_MAX_LIST_MODELS=value,
    )

    assert configured.OLLAMA_CATALOG_MAX_LIST_MODELS == value


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_OLLAMA_GENERATION_REQUEST_BYTES + 1,
    ],
)
def test_ollama_generation_request_cap_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            OLLAMA_GENERATION_MAX_REQUEST_BYTES=value,
        )


@pytest.mark.parametrize(
    "value",
    [1, 262_144, MAX_OLLAMA_GENERATION_REQUEST_BYTES],
)
def test_ollama_generation_request_cap_accepts_documented_bounds(value):
    configured = Settings(
        _env_file=None,
        OLLAMA_GENERATION_MAX_REQUEST_BYTES=value,
    )

    assert configured.OLLAMA_GENERATION_MAX_REQUEST_BYTES == value


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_OLLAMA_GENERATION_RESPONSE_BYTES + 1,
    ],
)
def test_ollama_generation_response_cap_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            OLLAMA_GENERATION_MAX_RESPONSE_BYTES=value,
        )


@pytest.mark.parametrize(
    "value",
    [1, 262_144, MAX_OLLAMA_GENERATION_RESPONSE_BYTES],
)
def test_ollama_generation_response_cap_accepts_documented_bounds(value):
    configured = Settings(
        _env_file=None,
        OLLAMA_GENERATION_MAX_RESPONSE_BYTES=value,
    )

    assert configured.OLLAMA_GENERATION_MAX_RESPONSE_BYTES == value

_STRICT_INTEGER_SOURCE_FIELDS = (
    (
        "MODEL_LIST_MAX_RESPONSE_BYTES",
        1_048_576,
        MAX_MODEL_LIST_RESPONSE_BYTES,
    ),
    (
        "OLLAMA_CATALOG_MAX_RESPONSE_BYTES",
        1_048_576,
        MAX_OLLAMA_CATALOG_RESPONSE_BYTES,
    ),
    (
        "OLLAMA_CATALOG_MAX_LIST_MODELS",
        256,
        MAX_OLLAMA_CATALOG_LIST_MODELS,
    ),
    (
        "OLLAMA_GENERATION_MAX_REQUEST_BYTES",
        1_048_576,
        MAX_OLLAMA_GENERATION_REQUEST_BYTES,
    ),
    (
        "OLLAMA_GENERATION_MAX_RESPONSE_BYTES",
        262_144,
        MAX_OLLAMA_GENERATION_RESPONSE_BYTES,
    ),
    (
        "GENERATION_MAX_ACTIVE_PER_PROCESS",
        1,
        MAX_GENERATION_ACTIVE_PER_PROCESS,
    ),
    (
        "REQUEST_MAX_BODY_BYTES",
        262_144,
        MAX_REQUEST_BODY_BYTES,
    ),
)
_INVALID_STRICT_INTEGER_SOURCE_FORMS = (
    "1.0",
    "1e3",
    "true",
    "false",
    '"1"',
    "null",
    "",
    " \t ",
    "[1]",
    '{"value":1}',
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field, documented_value, maximum in _STRICT_INTEGER_SOURCE_FIELDS
        for value in (1, documented_value, maximum)
    ],
)
def test_strict_integer_environment_accepts_documented_bounds(
    monkeypatch,
    field,
    value,
):
    monkeypatch.setenv(field, str(value))

    configured = Settings(_env_file=None)
    parsed = getattr(configured, field)

    assert parsed == value
    assert type(parsed) is int


@pytest.mark.parametrize(
    ("field", "documented_value", "maximum"),
    _STRICT_INTEGER_SOURCE_FIELDS,
)
def test_strict_integer_environment_accepts_surrounding_whitespace(
    monkeypatch,
    field,
    documented_value,
    maximum,
):
    monkeypatch.setenv(field, f" \t{documented_value}\n")

    parsed = getattr(Settings(_env_file=None), field)

    assert parsed == documented_value
    assert type(parsed) is int


@pytest.mark.parametrize(
    "source_value",
    _INVALID_STRICT_INTEGER_SOURCE_FORMS,
)
@pytest.mark.parametrize(
    ("field", "_documented_value", "_maximum"),
    _STRICT_INTEGER_SOURCE_FIELDS,
)
def test_strict_integer_environment_rejects_noninteger_source_forms(
    monkeypatch,
    field,
    _documented_value,
    _maximum,
    source_value,
):
    monkeypatch.setenv(field, source_value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("field", "_documented_value", "maximum"),
    _STRICT_INTEGER_SOURCE_FIELDS,
)
def test_strict_integer_environment_rejects_out_of_range_values(
    monkeypatch,
    field,
    _documented_value,
    maximum,
):
    for source_value in ("0", "-1", str(maximum + 1), "9" * 1_000):
        with monkeypatch.context() as isolated_environment:
            isolated_environment.setenv(field, source_value)
            with pytest.raises(ValidationError):
                Settings(_env_file=None)


def test_strict_integer_dotenv_accepts_documented_values(tmp_path):
    dotenv_path = tmp_path / "valid.env"
    dotenv_path.write_text(
        "\n".join(
            f"{field}={documented_value}"
            for field, documented_value, _maximum in _STRICT_INTEGER_SOURCE_FIELDS
        )
        + "\n",
        encoding="utf-8",
    )

    configured = Settings(_env_file=dotenv_path)

    for field, documented_value, _maximum in _STRICT_INTEGER_SOURCE_FIELDS:
        parsed = getattr(configured, field)
        assert parsed == documented_value
        assert type(parsed) is int


def _write_dotenv_source_value(path: Path, field: str, source_value: str) -> None:
    rendered_value = f"'{source_value}'" if source_value == '"1"' else source_value
    path.write_text(
        f"{field}={rendered_value}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "source_value",
    _INVALID_STRICT_INTEGER_SOURCE_FORMS,
)
@pytest.mark.parametrize(
    ("field", "_documented_value", "_maximum"),
    _STRICT_INTEGER_SOURCE_FIELDS,
)
def test_strict_integer_dotenv_rejects_noninteger_source_forms(
    tmp_path,
    field,
    _documented_value,
    _maximum,
    source_value,
):
    dotenv_path = tmp_path / "invalid.env"
    _write_dotenv_source_value(dotenv_path, field, source_value)

    with pytest.raises(ValidationError):
        Settings(_env_file=dotenv_path)


@pytest.mark.parametrize(
    ("field", "_documented_value", "maximum"),
    _STRICT_INTEGER_SOURCE_FIELDS,
)
def test_strict_integer_dotenv_rejects_out_of_range_values(
    tmp_path,
    field,
    _documented_value,
    maximum,
):
    for source_value in ("0", "-1", str(maximum + 1), "9" * 1_000):
        dotenv_path = tmp_path / f"invalid-{len(source_value)}.env"
        _write_dotenv_source_value(dotenv_path, field, source_value)
        with pytest.raises(ValidationError):
            Settings(_env_file=dotenv_path)


def test_env_example_is_an_executable_settings_contract(monkeypatch):
    for field, _documented_value, _maximum in _STRICT_INTEGER_SOURCE_FIELDS:
        monkeypatch.delenv(field, raising=False)
    env_example = Path(__file__).resolve().parents[1] / ".env.example"

    configured = Settings(_env_file=env_example)

    for field, documented_value, _maximum in _STRICT_INTEGER_SOURCE_FIELDS:
        parsed = getattr(configured, field)
        assert parsed == documented_value
        assert type(parsed) is int


def test_initializer_integer_overrides_environment(monkeypatch):
    monkeypatch.setenv("REQUEST_MAX_BODY_BYTES", "1")

    configured = Settings(
        _env_file=None,
        REQUEST_MAX_BODY_BYTES=262_144,
    )

    assert configured.REQUEST_MAX_BODY_BYTES == 262_144
    assert type(configured.REQUEST_MAX_BODY_BYTES) is int


def test_environment_integer_overrides_dotenv(monkeypatch, tmp_path):
    dotenv_path = tmp_path / "precedence.env"
    dotenv_path.write_text(
        "REQUEST_MAX_BODY_BYTES=1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("REQUEST_MAX_BODY_BYTES", "262144")

    configured = Settings(_env_file=dotenv_path)

    assert configured.REQUEST_MAX_BODY_BYTES == 262_144
    assert type(configured.REQUEST_MAX_BODY_BYTES) is int


def test_dotenv_integer_overrides_default(tmp_path):
    dotenv_path = tmp_path / "precedence.env"
    dotenv_path.write_text(
        "REQUEST_MAX_BODY_BYTES=1\n",
        encoding="utf-8",
    )

    configured = Settings(_env_file=dotenv_path)

    assert configured.REQUEST_MAX_BODY_BYTES == 1
    assert type(configured.REQUEST_MAX_BODY_BYTES) is int


def test_unrelated_environment_settings_keep_existing_parsing(monkeypatch):
    monkeypatch.setenv(
        "BACKEND_CORS_ORIGINS",
        '["https://workspace.example.test"]',
    )
    monkeypatch.setenv(
        "OLLAMA_LOCAL_MODEL_ALLOWLIST",
        '["verified-local:latest"]',
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:password@localhost:5432/workspace",
    )
    monkeypatch.setenv("REDIS_URL", "redis://:password@localhost:6379/0")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("TEST_DATABASE_URL", " \t ")
    monkeypatch.setenv("MODEL_LIST_MAX_DISCOVERY_SECONDS", "42.5")
    monkeypatch.setenv("GENERATION_MAX_DURATION_SECONDS", "73.25")
    monkeypatch.setenv("OLLAMA_GENERATION_TIMEOUT_SECONDS", "12.5")

    configured = Settings(_env_file=None)

    assert configured.BACKEND_CORS_ORIGINS == [
        "https://workspace.example.test"
    ]
    assert configured.OLLAMA_LOCAL_MODEL_ALLOWLIST == (
        "verified-local:latest",
    )
    assert str(configured.DATABASE_URL).startswith(
        "postgresql+asyncpg://"
    )
    assert str(configured.REDIS_URL).startswith("redis://")
    assert str(configured.OLLAMA_BASE_URL) == "http://127.0.0.1:11434/"
    assert configured.TEST_DATABASE_URL is None
    assert configured.MODEL_LIST_MAX_DISCOVERY_SECONDS == 42.5
    assert configured.GENERATION_MAX_DURATION_SECONDS == 73.25
    assert configured.OLLAMA_GENERATION_TIMEOUT_SECONDS == 12.5
