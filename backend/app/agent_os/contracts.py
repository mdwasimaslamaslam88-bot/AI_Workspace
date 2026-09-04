from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
import re

from app.ai.routing import ModelTask


MAX_AGENT_GOAL_CHARACTERS = 32_000
MAX_AGENT_INSTRUCTION_CHARACTERS = 16_000
MAX_AGENT_OUTPUT_CHARACTERS = 262_144
MAX_AGENT_PLAN_STEPS = 16
MAX_AGENT_ARTIFACTS = 64
MAX_AGENT_EVIDENCE = 64
MAX_AGENT_RETRIES = 2
MAX_AGENT_DEADLINE_SECONDS = 600.0
_STEP_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SHA256_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
_PUBLIC_MODEL_ID_PATTERN = re.compile(
    r"[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}\Z"
)


class AgentKind(StrEnum):
    PLANNER = "planner"
    CODING = "coding"
    DEBUGGING = "debugging"
    RESEARCH = "research"
    BROWSER = "browser"
    DATA = "data"
    VISION = "vision"
    IMAGE = "image"
    VOICE = "voice"
    RAG = "rag"
    AUTOMATION = "automation"
    VERIFIER = "verifier"


class AgentPermission(StrEnum):
    MODEL_INFERENCE = "model_inference"
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_WRITE = "workspace_write"
    BUILD_EXECUTION = "build_execution"
    TEST_EXECUTION = "test_execution"
    NETWORK_RESEARCH = "network_research"
    BROWSER_CONTROL = "browser_control"
    DATA_ANALYSIS = "data_analysis"
    RAG_READ = "rag_read"
    MEMORY_READ = "memory_read"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDITING = "image_editing"
    VOICE_INPUT = "voice_input"
    VOICE_OUTPUT = "voice_output"
    BOUNDED_TOOL_EXECUTION = "bounded_tool_execution"


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    NEEDS_APPROVAL = "needs_approval"
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    VERIFYING = "verifying"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class AgentInputSource(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class VerificationFailure(StrEnum):
    NONE = "none"
    EMPTY_OUTPUT = "empty_output"
    OUTPUT_TOO_LARGE = "output_too_large"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_MUTATED = "artifact_mutated"
    ARTIFACT_UNSAFE = "artifact_unsafe"
    SCHEMA_INVALID = "schema_invalid"
    EVIDENCE_MISSING = "evidence_missing"
    SECURITY_POLICY = "security_policy"
    OBJECTIVE_CHECK_FAILED = "objective_check_failed"
    VERIFIER_ERROR = "verifier_error"


@dataclass(frozen=True, slots=True)
class AgentArtifact:
    """Immutable evidence describing an original specialist artifact."""

    artifact_id: str
    relative_path: str
    content_sha256: str
    byte_size: int
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        if not _STEP_ID_PATTERN.fullmatch(self.artifact_id):
            raise ValueError("artifact_id must be a normalized identifier")
        if (
            not isinstance(self.relative_path, str)
            or not self.relative_path
            or self.relative_path.startswith(("/", "\\"))
            or ".." in self.relative_path.replace("\\", "/").split("/")
            or len(self.relative_path) > 512
        ):
            raise ValueError("artifact path must be bounded and relative")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("artifact digest must be a lowercase SHA-256")
        if (
            isinstance(self.byte_size, bool)
            or not isinstance(self.byte_size, int)
            or not 0 <= self.byte_size <= 64 * 1024 * 1024
        ):
            raise ValueError("artifact byte size is outside its bound")
        if (
            not isinstance(self.media_type, str)
            or not self.media_type
            or len(self.media_type) > 127
        ):
            raise ValueError("artifact media type is invalid")


@dataclass(frozen=True, slots=True)
class AgentExecution:
    output: str
    model_id: str | None = None
    artifacts: tuple[AgentArtifact, ...] = ()
    evidence_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.output, str):
            raise TypeError("agent output must be text")
        if len(self.output) > MAX_AGENT_OUTPUT_CHARACTERS:
            raise ValueError("agent output exceeds its bound")
        if self.model_id is not None and not _PUBLIC_MODEL_ID_PATTERN.fullmatch(
            self.model_id
        ):
            raise ValueError("agent model_id must be a public model identifier")
        if (
            not isinstance(self.artifacts, tuple)
            or len(self.artifacts) > MAX_AGENT_ARTIFACTS
            or any(not isinstance(item, AgentArtifact) for item in self.artifacts)
        ):
            raise TypeError("agent artifacts must be a bounded tuple")
        if len({item.artifact_id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("agent artifact identifiers must be unique")
        if (
            not isinstance(self.evidence_codes, tuple)
            or len(self.evidence_codes) > MAX_AGENT_EVIDENCE
            or any(
                not isinstance(code, str)
                or not _STEP_ID_PATTERN.fullmatch(code)
                for code in self.evidence_codes
            )
        ):
            raise ValueError("agent evidence codes must be normalized")


@dataclass(frozen=True, slots=True)
class AgentPlanStep:
    step_id: str
    agent: AgentKind
    task: ModelTask
    instruction: str
    permissions: frozenset[AgentPermission]
    requires_objective_evidence: bool = False

    def __post_init__(self) -> None:
        if not _STEP_ID_PATTERN.fullmatch(self.step_id):
            raise ValueError("agent step_id must be normalized")
        if not isinstance(self.agent, AgentKind):
            raise TypeError("agent plan step requires an AgentKind")
        if not isinstance(self.task, ModelTask):
            raise TypeError("agent plan step requires a ModelTask")
        if (
            not isinstance(self.instruction, str)
            or not self.instruction.strip()
            or self.instruction != self.instruction.strip()
            or len(self.instruction) > MAX_AGENT_INSTRUCTION_CHARACTERS
        ):
            raise ValueError("agent step instruction is invalid")
        if not isinstance(self.permissions, frozenset) or any(
            not isinstance(permission, AgentPermission)
            for permission in self.permissions
        ):
            raise TypeError("agent permissions must be a frozenset")
        if not isinstance(self.requires_objective_evidence, bool):
            raise TypeError("objective evidence flag must be boolean")


@dataclass(frozen=True, slots=True)
class AgentPlan:
    steps: tuple[AgentPlanStep, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.steps, tuple)
            or not 1 <= len(self.steps) <= MAX_AGENT_PLAN_STEPS
            or any(not isinstance(step, AgentPlanStep) for step in self.steps)
        ):
            raise ValueError("agent plan step count is outside its bound")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("agent plan step identifiers must be unique")


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    goal: str
    task: ModelTask
    source: AgentInputSource = AgentInputSource.TEXT
    specialist: AgentKind | None = None
    permissions: frozenset[AgentPermission] = frozenset(
        {AgentPermission.MODEL_INFERENCE}
    )
    max_retries: int = 1
    deadline_seconds: float = 180.0
    required_context_tokens: int = 0
    require_objective_evidence: bool = False
    allow_external_models: bool = True
    require_owner_approval: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.goal, str)
            or not self.goal.strip()
            or self.goal != self.goal.strip()
            or len(self.goal) > MAX_AGENT_GOAL_CHARACTERS
        ):
            raise ValueError("agent goal is invalid")
        if not isinstance(self.task, ModelTask):
            raise TypeError("agent task must be a ModelTask")
        if not isinstance(self.source, AgentInputSource):
            raise TypeError("agent input source must be an AgentInputSource")
        if self.specialist is not None and not isinstance(
            self.specialist, AgentKind
        ):
            raise TypeError("agent specialist must be an AgentKind or None")
        if not isinstance(self.permissions, frozenset) or any(
            not isinstance(permission, AgentPermission)
            for permission in self.permissions
        ):
            raise TypeError("agent permissions must be a frozenset")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= MAX_AGENT_RETRIES
        ):
            raise ValueError("agent retry count is outside its bound")
        if (
            isinstance(self.deadline_seconds, bool)
            or not isinstance(self.deadline_seconds, (int, float))
            or not 0 < self.deadline_seconds <= MAX_AGENT_DEADLINE_SECONDS
        ):
            raise ValueError("agent deadline is outside its bound")
        if (
            isinstance(self.required_context_tokens, bool)
            or not isinstance(self.required_context_tokens, int)
            or not 0 <= self.required_context_tokens <= 1_000_000
        ):
            raise ValueError("agent context requirement is outside its bound")
        if not isinstance(self.require_objective_evidence, bool):
            raise TypeError("objective evidence requirement must be boolean")
        if not isinstance(self.allow_external_models, bool):
            raise TypeError("external model allowance must be boolean")
        if not isinstance(self.require_owner_approval, bool):
            raise TypeError("owner approval requirement must be boolean")


@dataclass(frozen=True, slots=True)
class AgentLifecycleUpdate:
    status: AgentRunStatus
    plan: AgentPlan | None = None
    step_id: str | None = None
    attempt: int | None = None
    agent: AgentKind | None = None
    model_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            AgentRunStatus.PLANNING,
            AgentRunStatus.RUNNING,
            AgentRunStatus.VERIFYING,
            AgentRunStatus.RETRYING,
        }:
            raise ValueError("lifecycle update must be a non-terminal execution state")
        if self.step_id is not None and not _STEP_ID_PATTERN.fullmatch(self.step_id):
            raise ValueError("lifecycle step identifier is invalid")
        if self.attempt is not None and (
            isinstance(self.attempt, bool)
            or not isinstance(self.attempt, int)
            or not 1 <= self.attempt <= MAX_AGENT_RETRIES + 1
        ):
            raise ValueError("lifecycle attempt is outside its bound")
        if self.agent is not None and not isinstance(self.agent, AgentKind):
            raise TypeError("lifecycle agent is invalid")
        if self.model_id is not None and not _PUBLIC_MODEL_ID_PATTERN.fullmatch(
            self.model_id
        ):
            raise ValueError("lifecycle model identifier is invalid")


AgentLifecycleReporter = Callable[[AgentLifecycleUpdate], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    check_id: str
    passed: bool
    failure: VerificationFailure = VerificationFailure.NONE
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if not _STEP_ID_PATTERN.fullmatch(self.check_id):
            raise ValueError("verification check_id must be normalized")
        if not isinstance(self.passed, bool):
            raise TypeError("verification passed state must be boolean")
        if not isinstance(self.failure, VerificationFailure):
            raise TypeError("verification failure classification is invalid")
        if self.passed != (self.failure is VerificationFailure.NONE):
            raise ValueError("verification state and failure must agree")
        if self.evidence_sha256 is not None and not _SHA256_PATTERN.fullmatch(
            self.evidence_sha256
        ):
            raise ValueError("verification evidence digest is invalid")


@dataclass(frozen=True, slots=True)
class VerificationReport:
    passed: bool
    checks: tuple[VerificationCheck, ...]
    output_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise TypeError("verification report state must be boolean")
        if not self.checks or any(
            not isinstance(check, VerificationCheck) for check in self.checks
        ):
            raise ValueError("verification report requires checks")
        if self.passed != all(check.passed for check in self.checks):
            raise ValueError("verification report state must match its checks")
        if not _SHA256_PATTERN.fullmatch(self.output_sha256):
            raise ValueError("verification output digest is invalid")


@dataclass(frozen=True, slots=True)
class AgentAttempt:
    step_id: str
    attempt: int
    agent: AgentKind
    model_id: str | None
    verification: VerificationReport


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    status: AgentRunStatus
    plan: AgentPlan | None
    output: str | None
    attempts: tuple[AgentAttempt, ...]
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentRunStatus):
            raise TypeError("agent run status is invalid")
        terminal_with_output = self.status is AgentRunStatus.COMPLETED
        if terminal_with_output != (self.output is not None):
            raise ValueError("only completed agent runs may contain output")
        if self.output is not None and (
            not self.output.strip() or len(self.output) > MAX_AGENT_OUTPUT_CHARACTERS
        ):
            raise ValueError("agent run output is invalid")
        if (
            self.status
            in {
                AgentRunStatus.FAILED,
                AgentRunStatus.TIMED_OUT,
                AgentRunStatus.CANCELLED,
            }
        ) != (self.failure_code is not None):
            raise ValueError("terminal failure state must contain a failure code")
