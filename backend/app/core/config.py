import ipaddress
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    RedisDsn,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


DatabaseSslMode = Literal["disable", "require", "verify-ca", "verify-full"]
OLLAMA_TASK_MODEL_PREFERENCE_KEYS = frozenset(
    {
        "general_chat",
        "reasoning",
        "mathematics",
        "coding",
        "debugging",
        "code_generation",
        "expert_analysis",
        "vision",
        "rag",
        "memory",
        "summarization",
        "tool_calling",
        "workflow_planning",
        "long_context",
        "exact_output",
    }
)
MAX_GENERATION_ACTIVE_PER_PROCESS = 8
MAX_GENERATION_DURATION_SECONDS = 600.0
MAX_MODEL_LIST_DISCOVERY_SECONDS = 300.0
MAX_MODEL_LIST_RESPONSE_BYTES = 1_048_576
MAX_OLLAMA_CATALOG_LIST_MODELS = 256
MAX_OLLAMA_CATALOG_RESPONSE_BYTES = 1_048_576
MAX_OLLAMA_GENERATION_REQUEST_BYTES = 1_048_576
MAX_OLLAMA_GENERATION_RESPONSE_BYTES = 1_048_576
MAX_OLLAMA_EMBEDDING_REQUEST_BYTES = 1_048_576
MAX_OLLAMA_EMBEDDING_RESPONSE_BYTES = 16_777_216
MAX_DOCUMENT_INGESTION_ACTIVE_PER_PROCESS = 4
MAX_DOCUMENT_INGESTION_DURATION_SECONDS = 300.0
MAX_SPEECH_RUNTIME_ACTIVE_PER_PROCESS = 2
MAX_SPEECH_RUNTIME_DURATION_SECONDS = 300.0
MAX_REQUEST_BODY_BYTES = 1_048_576
_SOURCE_DECODED_STRICT_INTEGER_FIELDS = frozenset(
    {
        "MODEL_LIST_MAX_RESPONSE_BYTES",
        "OLLAMA_CATALOG_MAX_RESPONSE_BYTES",
        "OLLAMA_CATALOG_MAX_LIST_MODELS",
        "OLLAMA_GENERATION_MAX_REQUEST_BYTES",
        "OLLAMA_GENERATION_MAX_RESPONSE_BYTES",
        "OLLAMA_KEEP_ALIVE_SECONDS",
        "OLLAMA_EMBEDDING_MAX_REQUEST_BYTES",
        "OLLAMA_EMBEDDING_MAX_RESPONSE_BYTES",
        "OLLAMA_EMBEDDING_BATCH_SIZE",
        "OLLAMA_EMBEDDING_MAX_ACTIVE_PER_PROCESS",
        "GENERATION_MAX_ACTIVE_PER_PROCESS",
        "DOCUMENT_INGESTION_MAX_ACTIVE_PER_PROCESS",
        "STT_MAX_ACTIVE_PER_PROCESS",
        "TTS_MAX_ACTIVE_PER_PROCESS",
        "COMFYUI_MAX_ACTIVE_PER_PROCESS",
        "REQUEST_MAX_BODY_BYTES",
        "EDGE_AUTH_FAILURE_LIMIT",
        "EDGE_PROVISIONING_LIMIT",
        "EDGE_RATE_LIMIT_WINDOW_SECONDS",
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
    }
)
_DECIMAL_INTEGER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_TAURI_DESKTOP_ORIGINS = frozenset(
    {
        "http://tauri.localhost",
        "tauri://localhost",
    }
)


def _decode_strict_integer_source_value(field_name: str, value: Any) -> Any:
    if (
        field_name not in _SOURCE_DECODED_STRICT_INTEGER_FIELDS
        or not isinstance(value, str)
    ):
        return value

    normalized = value.strip()
    if not _DECIMAL_INTEGER_PATTERN.fullmatch(normalized):
        return value
    try:
        return int(normalized)
    except ValueError:
        return value


class _StrictIntegerTextSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self._source = source

    def get_field_value(
        self,
        field: FieldInfo,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        return self._source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        values = self._source()
        for field_name in _SOURCE_DECODED_STRICT_INTEGER_FIELDS:
            if field_name in values:
                values[field_name] = _decode_strict_integer_source_value(
                    field_name,
                    values[field_name],
                )
        return values


class _StrictIntegerEnvironmentSettingsSource(_StrictIntegerTextSettingsSource):
    pass


class _StrictIntegerDotenvSettingsSource(_StrictIntegerTextSettingsSource):
    pass


class Settings(BaseSettings):
    APP_TITLE: str = "AI Workspace Backend"
    APP_VERSION: str = "0.1.0"
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    DATABASE_URL: PostgresDsn | None = None
    REDIS_URL: RedisDsn | None = None
    OLLAMA_BASE_URL: AnyHttpUrl | None = None
    ASSET_STORAGE_ROOT: Path | None = None
    HARDWARE_STATE_PATH: Path | None = None
    EXTERNAL_AI_STATE_ROOT: Path | None = None
    CONNECTOR_STATE_ROOT: Path | None = None
    CONNECTOR_ALLOWED_ORIGINS: list[str] = []
    SELF_UPDATE_STATE_ROOT: Path | None = None
    WORK_STATION_WEB_ROOT: Path | None = None
    REMOTE_GATEWAY_MODE: Literal["local", "tailscale"] = "local"
    COMFYUI_BASE_URL: AnyHttpUrl | None = None
    COMFYUI_CHECKPOINT: Path | None = None
    COMFYUI_INPUT_ROOT: Path | None = None
    COMFYUI_TEMP_ROOT: Path | None = None
    COMFYUI_MODEL_REFERENCE: str | None = Field(
        default=None,
        strict=True,
        min_length=1,
        max_length=240,
    )
    COMFYUI_MODEL_PROFILE: str = Field(
        default="sdxl-base-1.0",
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )
    COMFYUI_TIMEOUT_SECONDS: float = Field(
        default=300.0,
        gt=0,
        le=600.0,
        allow_inf_nan=False,
    )
    COMFYUI_MAX_ACTIVE_PER_PROCESS: int = Field(
        default=1,
        strict=True,
        ge=1,
        le=1,
    )
    STT_PYTHON: Path | None = None
    STT_MODEL_ROOT: Path | None = None
    STT_MODEL_REFERENCE: str | None = Field(
        default=None,
        strict=True,
        min_length=1,
        max_length=240,
    )
    STT_LIBRARY_DIRECTORIES: tuple[Path, ...] = ()
    STT_DEVICE: Literal["cuda", "cpu"] = "cuda"
    STT_COMPUTE_TYPE: Literal[
        "float16", "int8_float16", "int8", "float32"
    ] = "float16"
    STT_TIMEOUT_SECONDS: float = Field(
        default=120.0,
        gt=0,
        le=MAX_SPEECH_RUNTIME_DURATION_SECONDS,
        allow_inf_nan=False,
    )
    STT_MAX_ACTIVE_PER_PROCESS: int = Field(
        default=1,
        strict=True,
        ge=1,
        le=MAX_SPEECH_RUNTIME_ACTIVE_PER_PROCESS,
    )
    TTS_PIPER_BINARY: Path | None = None
    TTS_VOICE_MODEL: Path | None = None
    TTS_VOICE_CONFIG: Path | None = None
    TTS_VOICE_REFERENCE: str | None = Field(
        default=None,
        strict=True,
        min_length=1,
        max_length=240,
    )
    TTS_TIMEOUT_SECONDS: float = Field(
        default=60.0,
        gt=0,
        le=MAX_SPEECH_RUNTIME_DURATION_SECONDS,
        allow_inf_nan=False,
    )
    TTS_MAX_ACTIVE_PER_PROCESS: int = Field(
        default=1,
        strict=True,
        ge=1,
        le=MAX_SPEECH_RUNTIME_ACTIVE_PER_PROCESS,
    )
    USER_PROVISIONING_TOKEN_DIGEST: str | None = Field(
        default=None,
        strict=True,
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
    DATABASE_CONNECT_TIMEOUT_SECONDS: float = Field(
        default=3.0,
        gt=0,
        allow_inf_nan=False,
    )
    DATABASE_POOL_TIMEOUT_SECONDS: float = Field(
        default=5.0,
        gt=0,
        allow_inf_nan=False,
    )
    DATABASE_COMMAND_TIMEOUT_SECONDS: float = Field(
        default=10.0,
        gt=0,
        allow_inf_nan=False,
    )
    DATABASE_POOL_SIZE: int = Field(default=5, strict=True, ge=1)
    DATABASE_MAX_OVERFLOW: int = Field(default=10, strict=True, ge=0)
    DATABASE_SSL_MODE: DatabaseSslMode = "verify-full"
    DATABASE_SSL_ROOT_CERT: str | None = None
    TEST_DATABASE_URL: PostgresDsn | None = None
    REDIS_CONNECT_TIMEOUT_SECONDS: float = Field(
        default=3.0,
        gt=0,
        allow_inf_nan=False,
    )
    OLLAMA_TIMEOUT_SECONDS: float = Field(
        default=5.0,
        gt=0,
        allow_inf_nan=False,
    )
    OLLAMA_LOCAL_MODEL_ALLOWLIST: tuple[str, ...] = ()
    OLLAMA_TASK_MODEL_PREFERENCES: dict[str, str] = Field(default_factory=dict)
    OLLAMA_EMBEDDING_MODEL: str | None = Field(
        default=None,
        strict=True,
        min_length=1,
        max_length=240,
    )
    MODEL_LIST_MAX_DISCOVERY_SECONDS: StrictFloat | StrictInt = Field(
        default=60.0,
        gt=0,
        le=MAX_MODEL_LIST_DISCOVERY_SECONDS,
        allow_inf_nan=False,
    )
    MODEL_LIST_MAX_RESPONSE_BYTES: int = Field(
        default=1_048_576,
        strict=True,
        ge=1,
        le=MAX_MODEL_LIST_RESPONSE_BYTES,
    )
    OLLAMA_CATALOG_MAX_RESPONSE_BYTES: int = Field(
        default=1_048_576,
        strict=True,
        ge=1,
        le=MAX_OLLAMA_CATALOG_RESPONSE_BYTES,
    )
    OLLAMA_CATALOG_MAX_LIST_MODELS: int = Field(
        default=256,
        strict=True,
        ge=1,
        le=MAX_OLLAMA_CATALOG_LIST_MODELS,
    )
    OLLAMA_GENERATION_TIMEOUT_SECONDS: float = Field(
        default=120.0,
        gt=0,
        allow_inf_nan=False,
    )
    OLLAMA_GENERATION_MAX_REQUEST_BYTES: int = Field(
        default=1_048_576,
        strict=True,
        ge=1,
        le=MAX_OLLAMA_EMBEDDING_REQUEST_BYTES,
    )
    OLLAMA_GENERATION_MAX_RESPONSE_BYTES: int = Field(
        default=262_144,
        strict=True,
        ge=1,
        le=MAX_OLLAMA_GENERATION_RESPONSE_BYTES,
    )
    OLLAMA_KEEP_ALIVE_SECONDS: int = Field(
        default=0,
        strict=True,
        ge=0,
        le=3600,
    )
    OLLAMA_EMBEDDING_TIMEOUT_SECONDS: float = Field(
        default=60.0,
        gt=0,
        le=300.0,
        allow_inf_nan=False,
    )
    OLLAMA_EMBEDDING_MAX_REQUEST_BYTES: int = Field(
        default=1_048_576,
        strict=True,
        ge=1,
        le=MAX_OLLAMA_GENERATION_REQUEST_BYTES,
    )
    OLLAMA_EMBEDDING_MAX_RESPONSE_BYTES: int = Field(
        default=1_048_576,
        strict=True,
        ge=1,
        le=MAX_OLLAMA_EMBEDDING_RESPONSE_BYTES,
    )
    OLLAMA_EMBEDDING_BATCH_SIZE: int = Field(
        default=16,
        strict=True,
        ge=1,
        le=64,
    )
    OLLAMA_EMBEDDING_MAX_ACTIVE_PER_PROCESS: int = Field(
        default=1,
        strict=True,
        ge=1,
        le=4,
    )
    GENERATION_MAX_ACTIVE_PER_PROCESS: int = Field(
        default=1,
        strict=True,
        ge=1,
        le=MAX_GENERATION_ACTIVE_PER_PROCESS,
    )
    GENERATION_MAX_DURATION_SECONDS: StrictFloat | StrictInt = Field(
        default=180.0,
        gt=0,
        le=MAX_GENERATION_DURATION_SECONDS,
        allow_inf_nan=False,
    )
    DOCUMENT_INGESTION_MAX_ACTIVE_PER_PROCESS: int = Field(
        default=2,
        strict=True,
        ge=1,
        le=MAX_DOCUMENT_INGESTION_ACTIVE_PER_PROCESS,
    )
    DOCUMENT_INGESTION_MAX_DURATION_SECONDS: StrictFloat | StrictInt = Field(
        default=30.0,
        gt=0,
        le=MAX_DOCUMENT_INGESTION_DURATION_SECONDS,
        allow_inf_nan=False,
    )
    REQUEST_MAX_BODY_BYTES: int = Field(
        default=262_144,
        strict=True,
        ge=1,
        le=MAX_REQUEST_BODY_BYTES,
    )
    EDGE_AUTH_FAILURE_LIMIT: int = Field(
        default=120,
        strict=True,
        ge=10,
        le=1_000,
    )
    EDGE_PROVISIONING_LIMIT: int = Field(
        default=10,
        strict=True,
        ge=1,
        le=60,
    )
    EDGE_RATE_LIMIT_WINDOW_SECONDS: int = Field(
        default=60,
        strict=True,
        ge=10,
        le=600,
    )

    @field_validator(
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "REDIS_URL",
        "OLLAMA_BASE_URL",
        "OLLAMA_EMBEDDING_MODEL",
        "ASSET_STORAGE_ROOT",
        "HARDWARE_STATE_PATH",
        "EXTERNAL_AI_STATE_ROOT",
        "CONNECTOR_STATE_ROOT",
        "SELF_UPDATE_STATE_ROOT",
        "WORK_STATION_WEB_ROOT",
        "COMFYUI_BASE_URL",
        "COMFYUI_CHECKPOINT",
        "COMFYUI_INPUT_ROOT",
        "COMFYUI_TEMP_ROOT",
        "COMFYUI_MODEL_REFERENCE",
        "STT_PYTHON",
        "STT_MODEL_ROOT",
        "STT_MODEL_REFERENCE",
        "TTS_PIPER_BINARY",
        "TTS_VOICE_MODEL",
        "TTS_VOICE_CONFIG",
        "TTS_VOICE_REFERENCE",
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

    @field_validator("BACKEND_CORS_ORIGINS")
    @classmethod
    def require_exact_secure_cors_origins(cls, value):
        normalized: list[str] = []
        for origin in value:
            if not isinstance(origin, str) or origin != origin.strip():
                raise ValueError("CORS origins must be exact nonblank strings")
            if origin in _TAURI_DESKTOP_ORIGINS:
                exact_origin = origin
            else:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme not in {"http", "https"}
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.path not in {"", "/"}
                    or parsed.query
                    or parsed.fragment
                    or parsed.hostname is None
                    or origin == "*"
                ):
                    raise ValueError(
                        "CORS origins must be credential-free HTTP origins or "
                        "an exact WORK STATION desktop origin"
                    )
                host = parsed.hostname
                is_loopback = host == "localhost"
                if not is_loopback:
                    try:
                        is_loopback = ipaddress.ip_address(host).is_loopback
                    except ValueError:
                        is_loopback = False
                if parsed.scheme == "http" and not is_loopback:
                    raise ValueError("non-loopback CORS origins must use HTTPS")
                exact_origin = f"{parsed.scheme}://{parsed.netloc}"
            if exact_origin in normalized:
                raise ValueError("CORS origins must be unique")
            normalized.append(exact_origin)
        return normalized

    @field_validator(
        "ASSET_STORAGE_ROOT",
        "HARDWARE_STATE_PATH",
        "EXTERNAL_AI_STATE_ROOT",
        "CONNECTOR_STATE_ROOT",
        "SELF_UPDATE_STATE_ROOT",
    )
    @classmethod
    def require_private_absolute_asset_storage_root(cls, value):
        if value is None:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError("ASSET_STORAGE_ROOT must be an absolute path")
        candidate = candidate.resolve(strict=False)
        project_root = Path(__file__).resolve().parents[3]
        if candidate == project_root or project_root in candidate.parents:
            raise ValueError(
                "private state paths must be outside the project source tree"
            )
        return candidate

    @field_validator("CONNECTOR_ALLOWED_ORIGINS")
    @classmethod
    def require_exact_connector_origins(cls, value):
        from app.connectors.runtime import normalize_connector_origin

        normalized = [normalize_connector_origin(origin) for origin in value]
        if len(normalized) > 64:
            raise ValueError("at most 64 connector origins may be allowlisted")
        if len(set(normalized)) != len(normalized):
            raise ValueError("connector origins must be unique")
        return normalized

    @field_validator("WORK_STATION_WEB_ROOT")
    @classmethod
    def require_absolute_web_root(cls, value):
        if value is None:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError("WORK_STATION_WEB_ROOT must be an absolute path")
        return candidate.resolve(strict=False)

    @field_validator(
        "STT_MODEL_ROOT",
        "COMFYUI_CHECKPOINT",
        "COMFYUI_INPUT_ROOT",
        "COMFYUI_TEMP_ROOT",
        "TTS_PIPER_BINARY",
        "TTS_VOICE_MODEL",
        "TTS_VOICE_CONFIG",
    )
    @classmethod
    def require_absolute_external_runtime_path(cls, value):
        if value is None:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError("local runtime paths must be absolute")
        candidate = candidate.resolve(strict=False)
        project_root = Path(__file__).resolve().parents[3]
        if candidate == project_root or project_root in candidate.parents:
            raise ValueError("local runtime paths must be outside the source tree")
        return candidate

    @field_validator("STT_PYTHON")
    @classmethod
    def require_absolute_external_python_path(cls, value):
        if value is None:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError("local runtime paths must be absolute")
        resolved = candidate.resolve(strict=False)
        project_root = Path(__file__).resolve().parents[3]
        if resolved == project_root or project_root in resolved.parents:
            raise ValueError("local runtime paths must be outside the source tree")
        # Preserve a virtual environment's bin/python symlink. Resolving it to
        # the system interpreter would bypass the isolated runtime packages.
        return candidate.absolute()

    @field_validator("STT_LIBRARY_DIRECTORIES")
    @classmethod
    def require_absolute_unique_stt_library_directories(cls, value):
        resolved: list[Path] = []
        for item in value:
            candidate = Path(item)
            if not candidate.is_absolute():
                raise ValueError("STT library directories must be absolute")
            candidate = candidate.resolve(strict=False)
            if candidate in resolved:
                raise ValueError("STT library directories must be unique")
            resolved.append(candidate)
        return tuple(resolved)

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

    @field_validator("COMFYUI_BASE_URL")
    @classmethod
    def require_loopback_comfyui_url(cls, value):
        if value is None:
            return None
        if value.username is not None or value.password is not None:
            raise ValueError("COMFYUI_BASE_URL must not include credentials")
        if value.path not in (None, "", "/") or value.query or value.fragment:
            raise ValueError("COMFYUI_BASE_URL must identify only a local origin")
        host = value.host
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if host == "localhost":
            return value
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError(
                "COMFYUI_BASE_URL must use localhost or a loopback IP address"
            ) from exc
        if not address.is_loopback:
            raise ValueError(
                "COMFYUI_BASE_URL must use localhost or a loopback IP address"
            )
        return value

    @field_validator("OLLAMA_GENERATION_TIMEOUT_SECONDS", mode="before")
    @classmethod
    def reject_boolean_generation_timeout(cls, value):
        if isinstance(value, bool):
            raise ValueError("OLLAMA_GENERATION_TIMEOUT_SECONDS must be numeric")
        return value

    @field_validator("MODEL_LIST_MAX_DISCOVERY_SECONDS", mode="before")
    @classmethod
    def validate_model_list_max_discovery_seconds(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("MODEL_LIST_MAX_DISCOVERY_SECONDS must be numeric")
        return value

    @field_validator("GENERATION_MAX_DURATION_SECONDS", mode="before")
    @classmethod
    def validate_generation_max_duration(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("GENERATION_MAX_DURATION_SECONDS must be numeric")
        return value

    @field_validator("DOCUMENT_INGESTION_MAX_DURATION_SECONDS", mode="before")
    @classmethod
    def validate_document_ingestion_max_duration(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                "DOCUMENT_INGESTION_MAX_DURATION_SECONDS must be numeric"
            )
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

    @field_validator("OLLAMA_TASK_MODEL_PREFERENCES")
    @classmethod
    def validate_task_model_preferences(cls, value):
        for task, reference in value.items():
            if task not in OLLAMA_TASK_MODEL_PREFERENCE_KEYS:
                raise ValueError(
                    "OLLAMA_TASK_MODEL_PREFERENCES contains an unsupported task"
                )
            if (
                not isinstance(reference, str)
                or not reference
                or reference != reference.strip()
            ):
                raise ValueError(
                    "OLLAMA_TASK_MODEL_PREFERENCES values must be exact nonblank references"
                )
        return value

    @model_validator(mode="after")
    def require_allowlisted_embedding_model(self):
        if (
            self.OLLAMA_EMBEDDING_MODEL is not None
            and self.OLLAMA_EMBEDDING_MODEL
            not in self.OLLAMA_LOCAL_MODEL_ALLOWLIST
        ):
            raise ValueError(
                "OLLAMA_EMBEDDING_MODEL must be present in the exact local model allowlist"
            )
        return self

    @model_validator(mode="after")
    def require_allowlisted_task_model_preferences(self):
        if any(
            reference not in self.OLLAMA_LOCAL_MODEL_ALLOWLIST
            for reference in self.OLLAMA_TASK_MODEL_PREFERENCES.values()
        ):
            raise ValueError(
                "OLLAMA_TASK_MODEL_PREFERENCES values must be present in the exact local model allowlist"
            )
        return self

    @model_validator(mode="after")
    def require_complete_speech_runtime_groups(self):
        stt_values = (
            self.STT_PYTHON,
            self.STT_MODEL_ROOT,
            self.STT_MODEL_REFERENCE,
        )
        if any(value is not None for value in stt_values) and not all(
            value is not None for value in stt_values
        ):
            raise ValueError(
                "STT_PYTHON, STT_MODEL_ROOT, and STT_MODEL_REFERENCE must be configured together"
            )
        tts_values = (
            self.TTS_PIPER_BINARY,
            self.TTS_VOICE_MODEL,
            self.TTS_VOICE_CONFIG,
            self.TTS_VOICE_REFERENCE,
        )
        if any(value is not None for value in tts_values) and not all(
            value is not None for value in tts_values
        ):
            raise ValueError(
                "Piper binary, voice model, voice config, and voice reference must be configured together"
            )
        return self

    @model_validator(mode="after")
    def require_complete_comfyui_runtime_group(self):
        values = (
            self.COMFYUI_BASE_URL,
            self.COMFYUI_CHECKPOINT,
            self.COMFYUI_INPUT_ROOT,
            self.COMFYUI_TEMP_ROOT,
            self.COMFYUI_MODEL_REFERENCE,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError(
                "ComfyUI URL, checkpoint, input root, temp root, and model reference must be configured together"
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _StrictIntegerEnvironmentSettingsSource(env_settings),
            _StrictIntegerDotenvSettingsSource(dotenv_settings),
            file_secret_settings,
        )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
