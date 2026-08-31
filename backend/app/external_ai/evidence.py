from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from app.ai.routing import ModelTask
from app.external_ai.contracts import ExternalModelPolicy, ExternalProviderKind


MAX_EVIDENCE_BYTES = 1_048_576
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class ExternalEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExternalVerificationEvidence:
    provider_kind: ExternalProviderKind
    model_id: str
    tasks: frozenset[ModelTask]
    benchmark_artifact_sha256: str
    complete_category: bool
    passed: bool
    measured_quality: float
    measured_latency_ms: float
    stability_rate: float
    context_window: int
    input_cost_micros_per_million_tokens: int
    output_cost_micros_per_million_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.provider_kind, ExternalProviderKind):
            raise TypeError("evidence provider kind is invalid")
        policy = ExternalModelPolicy(
            model_id=self.model_id,
            tasks=self.tasks,
            verified=False,
            measured_quality=self.measured_quality,
            measured_latency_ms=self.measured_latency_ms,
            stability_rate=self.stability_rate,
            context_window=self.context_window,
            input_cost_micros_per_million_tokens=self.input_cost_micros_per_million_tokens,
            output_cost_micros_per_million_tokens=self.output_cost_micros_per_million_tokens,
        )
        del policy
        if not _SHA256.fullmatch(self.benchmark_artifact_sha256):
            raise ValueError("benchmark evidence digest is invalid")
        if self.complete_category is not True or self.passed is not True:
            raise ValueError("only complete passing category evidence can be admitted")
        if (
            self.measured_quality <= 0
            or self.stability_rate <= 0
            or self.context_window <= 0
        ):
            raise ValueError("passing evidence requires measured capability metadata")

    def payload(self) -> dict[str, object]:
        return {
            "format_version": 1,
            "provider_kind": self.provider_kind.value,
            "model_id": self.model_id,
            "tasks": sorted(task.value for task in self.tasks),
            "benchmark_artifact_sha256": self.benchmark_artifact_sha256,
            "complete_category": self.complete_category,
            "passed": self.passed,
            "measured_quality": self.measured_quality,
            "measured_latency_ms": self.measured_latency_ms,
            "stability_rate": self.stability_rate,
            "context_window": self.context_window,
            "input_cost_micros_per_million_tokens": self.input_cost_micros_per_million_tokens,
            "output_cost_micros_per_million_tokens": self.output_cost_micros_per_million_tokens,
        }


class ExternalEvidenceStore:
    """Immutable, content-addressed complete-category admission evidence."""

    def __init__(self, root: Path) -> None:
        self.root = root / "verification-evidence"
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink() or stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise ExternalEvidenceError("external evidence root must be owner-only")

    def register(self, evidence: ExternalVerificationEvidence) -> str:
        if not isinstance(evidence, ExternalVerificationEvidence):
            raise TypeError("external verification evidence is invalid")
        content = (json.dumps(evidence.payload(), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / f"{digest}.json"
        if path.exists():
            metadata = path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or path.read_bytes() != content
            ):
                raise ExternalEvidenceError("external evidence collision or unsafe file")
            return digest
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        root_descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)
        return digest

    def verify(self, provider_kind: ExternalProviderKind, model: ExternalModelPolicy) -> None:
        digest = model.verification_evidence_sha256
        if not model.verified or digest is None or not _SHA256.fullmatch(digest):
            raise ExternalEvidenceError("external model is not verified")
        expected = self.resolve_policy(provider_kind, digest)
        if model != expected:
            raise ExternalEvidenceError("external model policy does not match its evidence")

    def resolve_policy(
        self,
        provider_kind: ExternalProviderKind,
        digest: str,
    ) -> ExternalModelPolicy:
        if not isinstance(provider_kind, ExternalProviderKind) or not _SHA256.fullmatch(digest):
            raise ExternalEvidenceError("external model evidence reference is invalid")
        path = self.root / f"{digest}.json"
        try:
            metadata = path.lstat()
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise ExternalEvidenceError("external model evidence is not registered") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or not 1 <= metadata.st_size <= MAX_EVIDENCE_BYTES
            or hashlib.sha256(content).hexdigest() != digest
        ):
            raise ExternalEvidenceError("external model evidence integrity failed")
        try:
            payload = json.loads(content.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("evidence payload is not an object")
            evidence = ExternalVerificationEvidence(
                provider_kind=ExternalProviderKind(payload["provider_kind"]),
                model_id=payload["model_id"],
                tasks=frozenset(ModelTask(task) for task in payload["tasks"]),
                benchmark_artifact_sha256=payload["benchmark_artifact_sha256"],
                complete_category=payload["complete_category"],
                passed=payload["passed"],
                measured_quality=payload["measured_quality"],
                measured_latency_ms=payload["measured_latency_ms"],
                stability_rate=payload["stability_rate"],
                context_window=payload["context_window"],
                input_cost_micros_per_million_tokens=payload[
                    "input_cost_micros_per_million_tokens"
                ],
                output_cost_micros_per_million_tokens=payload[
                    "output_cost_micros_per_million_tokens"
                ],
            )
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            raise ExternalEvidenceError("external model evidence is invalid") from exc
        if payload != evidence.payload() or evidence.provider_kind is not provider_kind:
            raise ExternalEvidenceError("external model evidence does not match the provider")
        return ExternalModelPolicy(
            model_id=evidence.model_id,
            tasks=evidence.tasks,
            verified=True,
            verification_evidence_sha256=digest,
            measured_quality=evidence.measured_quality,
            measured_latency_ms=evidence.measured_latency_ms,
            stability_rate=evidence.stability_rate,
            context_window=evidence.context_window,
            input_cost_micros_per_million_tokens=(
                evidence.input_cost_micros_per_million_tokens
            ),
            output_cost_micros_per_million_tokens=(
                evidence.output_cost_micros_per_million_tokens
            ),
        )
