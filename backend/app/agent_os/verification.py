from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib
from pathlib import Path
import stat

from app.agent_os.contracts import (
    AgentExecution,
    AgentPlanStep,
    VerificationCheck,
    VerificationFailure,
    VerificationReport,
)


ObjectiveVerifier = Callable[
    [AgentPlanStep, AgentExecution],
    Awaitable[VerificationCheck],
]


class IndependentVerificationEngine:
    """Read-only verifier; it never rewrites or repairs specialist output."""

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        objective_verifiers: tuple[ObjectiveVerifier, ...] = (),
    ) -> None:
        self.workspace_root = (
            workspace_root.resolve(strict=True)
            if workspace_root is not None
            else None
        )
        if self.workspace_root is not None and not self.workspace_root.is_dir():
            raise ValueError("verification workspace must be a directory")
        if any(not callable(verifier) for verifier in objective_verifiers):
            raise TypeError("objective verifiers must be callable")
        self.objective_verifiers = objective_verifiers

    async def verify(
        self,
        step: AgentPlanStep,
        execution: AgentExecution,
    ) -> VerificationReport:
        if not isinstance(step, AgentPlanStep):
            raise TypeError("verification requires an AgentPlanStep")
        if not isinstance(execution, AgentExecution):
            raise TypeError("verification requires an AgentExecution")
        encoded = execution.output.encode("utf-8")
        output_digest = hashlib.sha256(encoded).hexdigest()
        checks = [
            VerificationCheck(
                check_id="nonblank-output",
                passed=bool(execution.output.strip()),
                failure=(
                    VerificationFailure.NONE
                    if execution.output.strip()
                    else VerificationFailure.EMPTY_OUTPUT
                ),
                evidence_sha256=output_digest,
            )
        ]
        for artifact in execution.artifacts:
            checks.append(self._verify_artifact(artifact))
        if step.requires_objective_evidence and not (
            execution.evidence_codes or self.objective_verifiers
        ):
            checks.append(
                VerificationCheck(
                    check_id="objective-evidence",
                    passed=False,
                    failure=VerificationFailure.EVIDENCE_MISSING,
                )
            )
        for index, verifier in enumerate(self.objective_verifiers, start=1):
            try:
                result = await verifier(step, execution)
                if not isinstance(result, VerificationCheck):
                    raise TypeError("objective verifier returned an invalid check")
            except Exception:
                result = VerificationCheck(
                    check_id=f"objective-verifier-{index}",
                    passed=False,
                    failure=VerificationFailure.VERIFIER_ERROR,
                )
            checks.append(result)
        return VerificationReport(
            passed=all(check.passed for check in checks),
            checks=tuple(checks),
            output_sha256=output_digest,
        )

    def _verify_artifact(self, artifact) -> VerificationCheck:
        if self.workspace_root is None:
            return VerificationCheck(
                check_id=f"artifact-{artifact.artifact_id}",
                passed=False,
                failure=VerificationFailure.ARTIFACT_UNSAFE,
            )
        candidate = self.workspace_root / artifact.relative_path
        try:
            metadata = candidate.lstat()
            resolved = candidate.resolve(strict=True)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or self.workspace_root not in resolved.parents
            ):
                return VerificationCheck(
                    check_id=f"artifact-{artifact.artifact_id}",
                    passed=False,
                    failure=VerificationFailure.ARTIFACT_UNSAFE,
                )
            digest = hashlib.sha256()
            size = 0
            with resolved.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > 64 * 1024 * 1024:
                        raise ValueError("artifact exceeds verifier bound")
                    digest.update(chunk)
            actual = digest.hexdigest()
        except FileNotFoundError:
            return VerificationCheck(
                check_id=f"artifact-{artifact.artifact_id}",
                passed=False,
                failure=VerificationFailure.ARTIFACT_MISSING,
            )
        except (OSError, ValueError):
            return VerificationCheck(
                check_id=f"artifact-{artifact.artifact_id}",
                passed=False,
                failure=VerificationFailure.ARTIFACT_UNSAFE,
            )
        return VerificationCheck(
            check_id=f"artifact-{artifact.artifact_id}",
            passed=(
                metadata.st_size == artifact.byte_size
                and actual == artifact.content_sha256
            ),
            failure=(
                VerificationFailure.NONE
                if metadata.st_size == artifact.byte_size
                and actual == artifact.content_sha256
                else VerificationFailure.ARTIFACT_MUTATED
            ),
            evidence_sha256=actual,
        )
