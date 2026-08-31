from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from app.ai.routing import ModelTask


MAX_EXTERNAL_PROVIDERS = 16
MAX_EXTERNAL_MODELS_PER_PROVIDER = 64
MAX_EXTERNAL_KEY_CHARACTERS = 512
MAX_EXTERNAL_RESPONSE_BYTES = 1_048_576
_PROVIDER_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_SHA256_PATTERN = re.compile(r"[a-f0-9]{64}\Z")

EXTERNAL_TEXT_TASKS = frozenset(
    {
        ModelTask.GENERAL_CHAT,
        ModelTask.REASONING,
        ModelTask.MATHEMATICS,
        ModelTask.CODING,
        ModelTask.CODE_GENERATION,
        ModelTask.DEBUGGING,
        ModelTask.EXPERT_ANALYSIS,
        ModelTask.RAG,
        ModelTask.MEMORY,
        ModelTask.SUMMARIZATION,
        ModelTask.TOOL_CALLING,
        ModelTask.WORKFLOW_PLANNING,
        ModelTask.LONG_CONTEXT,
        ModelTask.EXACT_OUTPUT,
    }
)


class ExternalProviderKind(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


class ExternalProviderStatus(StrEnum):
    DISABLED = "disabled"
    UNCONFIGURED = "unconfigured"
    READY = "ready"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    SPENDING_LIMIT_REACHED = "spending_limit_reached"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ExternalModelPolicy:
    model_id: str
    tasks: frozenset[ModelTask]
    verified: bool = False
    verification_evidence_sha256: str | None = None
    measured_quality: float = 0.0
    measured_latency_ms: float = 0.0
    stability_rate: float = 0.0
    context_window: int = 0
    input_cost_micros_per_million_tokens: int = 0
    output_cost_micros_per_million_tokens: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not _MODEL_ID_PATTERN.fullmatch(
            self.model_id
        ):
            raise ValueError("external model_id is invalid")
        if (
            not isinstance(self.tasks, frozenset)
            or not self.tasks
            or any(task not in EXTERNAL_TEXT_TASKS for task in self.tasks)
        ):
            raise ValueError("external model tasks are invalid")
        if not isinstance(self.verified, bool):
            raise TypeError("external model verified state must be boolean")
        if self.verified != (
            self.verification_evidence_sha256 is not None
        ):
            raise ValueError("verified external models require evidence")
        if (
            self.verification_evidence_sha256 is not None
            and not _SHA256_PATTERN.fullmatch(
                self.verification_evidence_sha256
            )
        ):
            raise ValueError("external model verification evidence is invalid")
        if (
            isinstance(self.measured_quality, bool)
            or not isinstance(self.measured_quality, (int, float))
            or not 0 <= self.measured_quality <= 100
        ):
            raise ValueError("external model quality is outside its bound")
        if (
            isinstance(self.measured_latency_ms, bool)
            or not isinstance(self.measured_latency_ms, (int, float))
            or not 0 <= self.measured_latency_ms <= 3_600_000
        ):
            raise ValueError("external model latency is outside its bound")
        if (
            isinstance(self.stability_rate, bool)
            or not isinstance(self.stability_rate, (int, float))
            or not 0 <= self.stability_rate <= 1
        ):
            raise ValueError("external model stability is outside its bound")
        if (
            isinstance(self.context_window, bool)
            or not isinstance(self.context_window, int)
            or not 0 <= self.context_window <= 10_000_000
        ):
            raise ValueError("external model context window is outside its bound")
        for field_name, value in (
            (
                "input cost",
                self.input_cost_micros_per_million_tokens,
            ),
            (
                "output cost",
                self.output_cost_micros_per_million_tokens,
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 10_000_000_000
            ):
                raise ValueError(f"external model {field_name} is invalid")
        if self.verified and (
            self.measured_quality <= 0
            or self.stability_rate <= 0
            or self.context_window <= 0
        ):
            raise ValueError(
                "verified external models require measured quality, stability, and context"
            )


@dataclass(frozen=True, slots=True)
class ExternalProviderConfig:
    provider_id: str
    kind: ExternalProviderKind
    enabled: bool = False
    free_tier: bool = False
    priority: int = 100
    timeout_seconds: float = 30.0
    rate_limit_requests_per_minute: int = 30
    spending_limit_micros: int = 0
    quota_remaining_tokens: int | None = None
    models: tuple[ExternalModelPolicy, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not _PROVIDER_ID_PATTERN.fullmatch(
            self.provider_id
        ):
            raise ValueError("external provider_id is invalid")
        if not isinstance(self.kind, ExternalProviderKind):
            raise TypeError("external provider kind is invalid")
        for field_name, value in (
            ("enabled", self.enabled),
            ("free_tier", self.free_tier),
        ):
            if not isinstance(value, bool):
                raise TypeError(f"external provider {field_name} must be boolean")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 1_000
        ):
            raise ValueError("external provider priority is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 1 <= self.timeout_seconds <= 60
        ):
            raise ValueError("external provider timeout is invalid")
        if (
            isinstance(self.rate_limit_requests_per_minute, bool)
            or not isinstance(self.rate_limit_requests_per_minute, int)
            or not 1 <= self.rate_limit_requests_per_minute <= 1_000
        ):
            raise ValueError("external provider rate limit is invalid")
        if (
            isinstance(self.spending_limit_micros, bool)
            or not isinstance(self.spending_limit_micros, int)
            or not 0 <= self.spending_limit_micros <= 10**15
        ):
            raise ValueError("external provider spending limit is invalid")
        if self.quota_remaining_tokens is not None and (
            isinstance(self.quota_remaining_tokens, bool)
            or not isinstance(self.quota_remaining_tokens, int)
            or not 0 <= self.quota_remaining_tokens <= 10**15
        ):
            raise ValueError("external provider quota is invalid")
        if (
            not isinstance(self.models, tuple)
            or len(self.models) > MAX_EXTERNAL_MODELS_PER_PROVIDER
            or any(not isinstance(model, ExternalModelPolicy) for model in self.models)
        ):
            raise ValueError("external provider models are invalid")
        if len({model.model_id for model in self.models}) != len(self.models):
            raise ValueError("external provider model IDs must be unique")


@dataclass(frozen=True, slots=True)
class ExternalProviderRecord:
    config: ExternalProviderConfig
    api_key: str
    spent_micros: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.config, ExternalProviderConfig):
            raise TypeError("external provider record config is invalid")
        if (
            not isinstance(self.api_key, str)
            or not 16 <= len(self.api_key) <= MAX_EXTERNAL_KEY_CHARACTERS
            or self.api_key != self.api_key.strip()
            or any(ord(character) < 0x21 for character in self.api_key)
        ):
            raise ValueError("external provider key is invalid")
        if (
            isinstance(self.spent_micros, bool)
            or not isinstance(self.spent_micros, int)
            or not 0 <= self.spent_micros <= 10**18
        ):
            raise ValueError("external provider spend is invalid")


@dataclass(frozen=True, slots=True)
class ExternalAIState:
    global_enabled: bool = False
    providers: tuple[ExternalProviderRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.global_enabled, bool):
            raise TypeError("external AI global state must be boolean")
        if (
            not isinstance(self.providers, tuple)
            or len(self.providers) > MAX_EXTERNAL_PROVIDERS
            or any(
                not isinstance(provider, ExternalProviderRecord)
                for provider in self.providers
            )
        ):
            raise ValueError("external AI providers are invalid")
        if len({item.config.provider_id for item in self.providers}) != len(
            self.providers
        ):
            raise ValueError("external provider IDs must be unique")


@dataclass(frozen=True, slots=True)
class ExternalGenerationResult:
    content: str
    provider_id: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_micros: int

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("external generation content must not be blank")
        if len(self.content) > 262_144:
            raise ValueError("external generation content exceeds its bound")
        if not _PROVIDER_ID_PATTERN.fullmatch(self.provider_id):
            raise ValueError("external generation provider is invalid")
        if not _MODEL_ID_PATTERN.fullmatch(self.model_id):
            raise ValueError("external generation model is invalid")
        for field_name, value, maximum in (
            ("input tokens", self.input_tokens, 10_000_000),
            ("output tokens", self.output_tokens, 1_000_000),
            ("cost", self.cost_micros, 10**15),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= maximum
            ):
                raise ValueError(f"external generation {field_name} is invalid")
