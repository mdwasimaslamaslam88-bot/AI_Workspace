from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


MAX_CODE_CHARACTERS = 262_144
MAX_VERIFIER_SOURCE_CHARACTERS = 262_144
MAX_COMMANDS = 16
MAX_COMMAND_ARGUMENTS = 32
MAX_EVIDENCE_CHARACTERS = 4_000
MAX_TIMEOUT_SECONDS = 120
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_VERIFIER_IMAGE = "sha256:678c6550cc43645e08669028bc177f50be4e7c5b8cca677067b1914d4afc7a03"
_MUTATED_RETURN_CODE = 86
_CONTAINER_WRAPPER = """
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

source = Path('/input')
workspace = Path('/workspace')
expected = {
    path.relative_to(source): hashlib.sha256(path.read_bytes()).digest()
    for path in source.rglob('*')
    if path.is_file()
}
shutil.copytree(source, workspace, dirs_exist_ok=True)
completed = subprocess.run(sys.argv[1:], check=False)
mutated = any(
    not (workspace / relative).is_file()
    or hashlib.sha256((workspace / relative).read_bytes()).digest() != digest
    for relative, digest in expected.items()
)
raise SystemExit(86 if mutated else completed.returncode)
""".strip()


class CodeVerificationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TrustedCodeVerificationProfile:
    """Server-owned verifier contract; generated output cannot change it."""

    profile_id: str
    language: str
    artifact_filename: str
    verifier_files: tuple[tuple[str, str], ...]
    commands: tuple[tuple[str, ...], ...]
    expected_stdout: str | None = None
    timeout_seconds: int = 20

    def __post_init__(self) -> None:
        if not _SAFE_NAME.fullmatch(self.profile_id):
            raise ValueError("verification profile ID is invalid")
        if self.language not in {
            "python",
            "javascript",
            "typescript",
            "rust",
            "bash",
            "sql",
        }:
            raise ValueError("verification language is unsupported")
        _validate_filename(self.artifact_filename)
        if not 1 <= len(self.commands) <= MAX_COMMANDS:
            raise ValueError("verification command count is outside its bound")
        names = {self.artifact_filename}
        for filename, source in self.verifier_files:
            _validate_filename(filename)
            if filename in names:
                raise ValueError("verification filenames must be unique")
            if not isinstance(source, str) or len(source) > MAX_VERIFIER_SOURCE_CHARACTERS:
                raise ValueError("verification source is outside its bound")
            names.add(filename)
        for command in self.commands:
            if (
                not isinstance(command, tuple)
                or not 1 <= len(command) <= MAX_COMMAND_ARGUMENTS
                or any(not isinstance(item, str) or not item or "\x00" in item for item in command)
                or not Path(command[0]).is_absolute()
            ):
                raise ValueError("verification commands must use fixed absolute executables")
        if self.expected_stdout is not None and len(self.expected_stdout) > MAX_EVIDENCE_CHARACTERS:
            raise ValueError("expected verifier output is outside its bound")
        if isinstance(self.timeout_seconds, bool) or not 1 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError("verification timeout is outside its bound")


@dataclass(frozen=True, slots=True)
class CodeVerificationEvidence:
    command_index: int
    return_code: int
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True, slots=True)
class CodeVerificationResult:
    passed: bool
    profile_id: str
    original_artifact_sha256: str
    original_byte_size: int
    static_safety_passed: bool
    evidence: tuple[CodeVerificationEvidence, ...]
    failure_code: str | None = None


CommandRunner = Callable[
    [tuple[str, ...], Path, str | None, int],
    subprocess.CompletedProcess[str],
]


class IndependentCodeVerifier:
    """Verifies the original model text in an isolated, bounded environment.

    Profiles and test sources are trusted application objects. The generated
    artifact is hashed before any compiler runs and is never repaired.
    """

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or _run_sandboxed

    def verify(
        self,
        profile: TrustedCodeVerificationProfile,
        generated_answer: str,
        *,
        stdin: str | None = None,
    ) -> CodeVerificationResult:
        if not isinstance(profile, TrustedCodeVerificationProfile):
            raise TypeError("code verifier requires a trusted profile")
        try:
            artifact = _extract_original_artifact(generated_answer)
        except ValueError as exc:
            return _failed(profile, generated_answer, False, (), str(exc))
        digest = hashlib.sha256(artifact.encode("utf-8")).hexdigest()
        safety_failure = _static_safety_failure(profile.language, artifact)
        if safety_failure is not None:
            return CodeVerificationResult(
                False,
                profile.profile_id,
                digest,
                len(artifact.encode("utf-8")),
                False,
                (),
                safety_failure,
            )

        evidence: list[CodeVerificationEvidence] = []
        with tempfile.TemporaryDirectory(
            prefix="work-station-code-verify.",
            dir="/tmp",
        ) as temporary:
            root = Path(temporary)
            artifact_path = root / profile.artifact_filename
            artifact_path.write_text(artifact, encoding="utf-8")
            for filename, source in profile.verifier_files:
                (root / filename).write_text(source, encoding="utf-8")
            for index, command in enumerate(profile.commands, start=1):
                try:
                    completed = self._runner(
                        command,
                        root,
                        stdin,
                        profile.timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    return CodeVerificationResult(
                        False,
                        profile.profile_id,
                        digest,
                        len(artifact.encode("utf-8")),
                        True,
                        tuple(evidence),
                        "verifier_timeout",
                    )
                evidence.append(
                    CodeVerificationEvidence(
                        index,
                        completed.returncode,
                        _text_digest(completed.stdout),
                        _text_digest(completed.stderr),
                    )
                )
                if completed.returncode == _MUTATED_RETURN_CODE:
                    return CodeVerificationResult(
                        False,
                        profile.profile_id,
                        digest,
                        len(artifact.encode("utf-8")),
                        True,
                        tuple(evidence),
                        "original_artifact_mutated",
                    )
                if completed.returncode != 0:
                    return CodeVerificationResult(
                        False,
                        profile.profile_id,
                        digest,
                        len(artifact.encode("utf-8")),
                        True,
                        tuple(evidence),
                        "verifier_command_failed",
                    )
            if profile.expected_stdout is not None and completed.stdout.strip() != profile.expected_stdout:
                return CodeVerificationResult(
                    False,
                    profile.profile_id,
                    digest,
                    len(artifact.encode("utf-8")),
                    True,
                    tuple(evidence),
                    "verifier_output_mismatch",
                )
            if artifact_path.read_text(encoding="utf-8") != artifact:
                return CodeVerificationResult(
                    False,
                    profile.profile_id,
                    digest,
                    len(artifact.encode("utf-8")),
                    True,
                    tuple(evidence),
                    "original_artifact_mutated",
                )
        return CodeVerificationResult(
            True,
            profile.profile_id,
            digest,
            len(artifact.encode("utf-8")),
            True,
            tuple(evidence),
        )


def _validate_filename(filename: str) -> None:
    if not _SAFE_NAME.fullmatch(filename) or filename in {".", ".."}:
        raise ValueError("verification filenames must be flat safe names")


def _extract_original_artifact(answer: str) -> str:
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("artifact_blank")
    if len(answer) > MAX_CODE_CHARACTERS:
        raise ValueError("artifact_too_large")
    fences = re.findall(r"```(?:[A-Za-z0-9_+.-]+)?\s*\n(.*?)```", answer, re.DOTALL)
    if len(fences) > 1:
        raise ValueError("multiple_artifacts")
    artifact = fences[0].strip() if fences else answer.strip()
    if not artifact:
        raise ValueError("artifact_blank")
    return artifact + ("" if artifact.endswith("\n") else "\n")


def _static_safety_failure(language: str, artifact: str) -> str | None:
    common = (
        r"/(?:etc|home)/|\.ssh|BEGIN PRIVATE KEY|\b(?:curl|wget|nc|ssh)\b",
        r"\b(?:eval|exec)\s*\(",
    )
    patterns = {
        "python": (r"\b(?:import|from)\s+(?:os|subprocess|socket|pathlib|requests|httpx)\b", r"\bopen\s*\("),
        "javascript": (r"\brequire\s*\(\s*['\"](?:fs|net|http|https|child_process|worker_threads)['\"]", r"\bfetch\s*\("),
        "typescript": (r"\b(?:import|require)\b[^\n]*(?:fs|net|http|https|child_process)", r"\bfetch\s*\("),
        "rust": (r"\bstd::(?:fs|net|process)\b", r"\bCommand\s*::"),
        "bash": (r"\b(?:source|\.)\s+/(?:etc|home|proc)\b", r"\b(?:rm|dd|chmod|chown)\b"),
        "sql": (r"\b(?:insert|update|delete|drop|alter|attach|detach|pragma|vacuum|load_extension)\b",),
    }
    for pattern in (*common, *patterns[language]):
        if re.search(pattern, artifact, re.IGNORECASE):
            return "static_safety_rejection"
    if language == "sql":
        statement = artifact.strip().rstrip(";").strip()
        if not re.match(r"^(?:select|with)\b", statement, re.IGNORECASE):
            return "sql_not_read_only"
        if ";" in statement:
            return "multiple_sql_statements"
    return None


def _run_sandboxed(
    command: tuple[str, ...],
    root: Path,
    stdin: str | None,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    container_name = f"work-station-code-verify-{os.getpid()}-{uuid.uuid4().hex}"
    sandbox = (
        "/usr/bin/docker",
        "run",
        "--rm",
        "--pull=never",
        f"--name={container_name}",
        "--network=none",
        "--ipc=none",
        "--read-only",
        "--memory=512m",
        "--memory-swap=512m",
        "--pids-limit=64",
        "--cpus=2",
        "--ulimit=fsize=131072:131072",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={os.getuid()}:{os.getgid()}",
        "--env=HOME=/tmp",
        "--env=PATH=/usr/bin:/bin",
        "--mount=type=bind,src=/usr,dst=/usr,readonly",
        "--mount=type=bind,src=/bin,dst=/bin,readonly",
        "--mount=type=bind,src=/lib,dst=/lib,readonly",
        "--mount=type=bind,src=/lib64,dst=/lib64,readonly",
        f"--mount=type=bind,src={root},dst=/input,readonly",
        f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m,mode=700,uid={os.getuid()},gid={os.getgid()}",
        f"--tmpfs=/workspace:rw,exec,nosuid,nodev,size=128m,mode=700,uid={os.getuid()},gid={os.getgid()}",
        "--workdir=/workspace",
        _VERIFIER_IMAGE,
        "/usr/bin/python3",
        "-I",
        "-c",
        _CONTAINER_WRAPPER,
        *command,
    )
    docker_environment = {
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "HOME": "/nonexistent",
        "PATH": "/usr/bin:/bin",
    }
    try:
        return subprocess.run(
            sandbox,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
            check=False,
            env=docker_environment,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(
            ("/usr/bin/docker", "rm", "--force", container_name),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            env=docker_environment,
        )
        raise


def _text_digest(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _failed(
    profile: TrustedCodeVerificationProfile,
    answer: object,
    static_safety_passed: bool,
    evidence: tuple[CodeVerificationEvidence, ...],
    failure_code: str,
) -> CodeVerificationResult:
    encoded = answer.encode("utf-8") if isinstance(answer, str) else b""
    return CodeVerificationResult(
        False,
        profile.profile_id,
        hashlib.sha256(encoded).hexdigest(),
        len(encoded),
        static_safety_passed,
        evidence,
        failure_code,
    )
