from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import uuid

from app.core.secret_box import KEY_BYTES, SecretBoxError, XChaCha20Poly1305Box


MAX_GATE_COUNT = 32
MAX_GATE_TIMEOUT_SECONDS = 7_200
MAX_CONFIG_BYTES = 4 * 1024 * 1024
REQUIRED_UPDATE_GATES = frozenset(
    {
        "source",
        "backend",
        "database",
        "web",
        "mobile",
        "desktop",
        "browser_e2e",
        "rag_memory",
        "vision_image_voice",
        "tools_workflows_agents",
        "routing_admission_hardware",
        "api_fallback",
        "self_update",
        "security",
        "performance",
        "rollback",
        "release",
    }
)
_COMMIT = re.compile(r"[a-f0-9]{40}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}\Z")
_GATE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_UPDATE_SANDBOX_IMAGE = "sha256:678c6550cc43645e08669028bc177f50be4e7c5b8cca677067b1914d4afc7a03"
_CHECKPOINT_BOX = XChaCha20Poly1305Box(
    magic=b"WSCP1\x00",
    additional_data=b"work-station-update-checkpoint-v1",
)
_CHECKPOINT_AUTHENTICATION_FILE = "AUTHENTICATION"
_CHECKPOINT_FILES = (
    "package-lock.json",
    "backend/requirements.txt",
    "apps/desktop/src-tauri/Cargo.lock",
    "backend/app/ai/routing.py",
    "backend/app/ai/admission.py",
    "backend/app/ai/future_models.py",
)
_PRIVATE_CONFIG_FILES = (
    ".env",
    "backend/.env",
    "frontend/.env",
    "apps/mobile/.env",
)


class SelfUpdateError(RuntimeError):
    pass


class UpdateStatus(StrEnum):
    IDLE = "idle"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    ACTIVATED = "activated"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ValidationGate:
    name: str
    command: tuple[str, ...]
    timeout_seconds: int = 3_600
    network_access: bool = False
    read_only_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not _GATE_NAME.fullmatch(self.name):
            raise ValueError("update gate name is invalid")
        if (
            not isinstance(self.command, tuple)
            or not 1 <= len(self.command) <= 32
            or any(not isinstance(value, str) or not value or "\x00" in value for value in self.command)
            or not Path(self.command[0]).is_absolute()
        ):
            raise ValueError("update gates require fixed absolute executables")
        if isinstance(self.timeout_seconds, bool) or not 1 <= self.timeout_seconds <= MAX_GATE_TIMEOUT_SECONDS:
            raise ValueError("update gate timeout is outside its bound")
        if not isinstance(self.network_access, bool):
            raise TypeError("update gate network policy must be boolean")
        if len(self.read_only_paths) > 16:
            raise ValueError("update gate read-only path count is outside its bound")
        for path in self.read_only_paths:
            if not isinstance(path, Path) or not path.is_absolute() or not path.exists() or path.is_symlink():
                raise ValueError("update gate read-only paths must be safe absolute paths")


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    passed: bool
    return_code: int
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True, slots=True)
class UpdateState:
    status: UpdateStatus = UpdateStatus.IDLE
    version: str | None = None
    candidate_commit: str | None = None
    checkpoint_id: str | None = None
    candidate_directory: str | None = None
    gate_results: tuple[GateResult, ...] = ()
    prepared_at: str | None = None
    activated_at: str | None = None
    failure_code: str | None = None
    previous_release: str | None = None


GateRunner = Callable[
    [ValidationGate, Path],
    subprocess.CompletedProcess[str],
]


class SelfUpdateManager:
    """Checkpointed, isolated update validation and atomic release switching."""

    def __init__(
        self,
        repository_root: Path,
        state_root: Path,
        *,
        gate_runner: GateRunner | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve(strict=True)
        if not (self.repository_root / ".git").exists():
            raise ValueError("update repository must be a Git checkout")
        requested_root = Path(state_root)
        if not requested_root.is_absolute():
            raise ValueError("update state root must be absolute")
        if requested_root.exists() and requested_root.is_symlink():
            raise SelfUpdateError("update state root must not be a symbolic link")
        self.state_root = requested_root.resolve(strict=False)
        if self.state_root == self.repository_root or self.repository_root in self.state_root.parents:
            raise SelfUpdateError("update state must remain outside the source checkout")
        self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.state_root.stat().st_mode) & 0o077:
            raise SelfUpdateError("update state root must be owner-only")
        self.checkpoint_root = self.state_root / "checkpoints"
        self.candidate_root = self.state_root / "candidates"
        self.checkpoint_root.mkdir(mode=0o700, exist_ok=True)
        self.candidate_root.mkdir(mode=0o700, exist_ok=True)
        self.state_path = self.state_root / "update-state.json"
        self.key_path = self.state_root / "checkpoint.key"
        self.current_link = self.state_root / "current"
        self._gate_runner = gate_runner or _run_gate_sandboxed
        self._key = self._load_or_create_key()

    def state(self) -> UpdateState:
        try:
            metadata = self.state_path.lstat()
        except FileNotFoundError:
            return UpdateState()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > 1_048_576
        ):
            raise SelfUpdateError("update state file is unsafe")
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            gates = tuple(GateResult(**item) for item in payload.pop("gate_results"))
            status = UpdateStatus(payload.pop("status"))
            return UpdateState(
                **payload,
                status=status,
                gate_results=gates,
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise SelfUpdateError("update state file is invalid") from exc

    def stage_candidate(self, candidate_ref: str) -> tuple[Path, str]:
        candidate_commit = self._resolve_commit(candidate_ref)
        destination = self.candidate_root / (
            f"{candidate_commit}-{uuid.uuid4().hex[:8]}"
        )
        temporary = Path(tempfile.mkdtemp(prefix=".candidate-", dir=self.candidate_root))
        try:
            self._run_checked(
                (
                    "/usr/bin/git",
                    "clone",
                    "--no-hardlinks",
                    "--no-checkout",
                    str(self.repository_root),
                    str(temporary),
                ),
                self.state_root,
                "candidate_clone_failed",
            )
            self._run_checked(
                ("/usr/bin/git", "checkout", "--detach", candidate_commit),
                temporary,
                "candidate_checkout_failed",
            )
            temporary.rename(destination)
            return destination, candidate_commit
        except BaseException:
            _remove_private_temporary(temporary, self.candidate_root, ".candidate-")
            raise

    def prepare(
        self,
        *,
        candidate_ref: str,
        version: str,
        gates: tuple[ValidationGate, ...],
        database_backup: Path | None = None,
    ) -> UpdateState:
        if not _VERSION.fullmatch(version):
            raise ValueError("update version is invalid")
        gate_names = {gate.name for gate in gates}
        if (
            not 1 <= len(gates) <= MAX_GATE_COUNT
            or len(gate_names) != len(gates)
            or not REQUIRED_UPDATE_GATES.issubset(gate_names)
        ):
            raise ValueError("update gate set is invalid")
        if self._git(self.repository_root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise SelfUpdateError("production checkout must be clean before checkpointing")
        candidate, candidate_commit = self.stage_candidate(candidate_ref)
        self._provision_candidate_dependencies(candidate)
        checkpoint_id = self.create_checkpoint(database_backup=database_backup)
        validating = UpdateState(
            status=UpdateStatus.VALIDATING,
            version=version,
            candidate_commit=candidate_commit,
            checkpoint_id=checkpoint_id,
            candidate_directory=str(candidate),
            prepared_at=_utc_now(),
        )
        self._write_state(validating)
        results: list[GateResult] = []
        for gate in gates:
            try:
                completed = self._gate_runner(gate, candidate)
            except subprocess.TimeoutExpired:
                failed = self._terminal_validation_state(
                    validating,
                    tuple(results),
                    "gate_timeout",
                )
                self._write_state(failed)
                return failed
            result = GateResult(
                gate.name,
                completed.returncode == 0,
                completed.returncode,
                _digest_text(completed.stdout),
                _digest_text(completed.stderr),
            )
            results.append(result)
            if not result.passed:
                failed = self._terminal_validation_state(
                    validating,
                    tuple(results),
                    "gate_failed",
                )
                self._write_state(failed)
                return failed
        ready = UpdateState(
            status=UpdateStatus.READY,
            version=version,
            candidate_commit=candidate_commit,
            checkpoint_id=checkpoint_id,
            candidate_directory=str(candidate),
            gate_results=tuple(results),
            prepared_at=validating.prepared_at,
        )
        self._write_state(ready)
        return ready

    def create_checkpoint(self, *, database_backup: Path | None = None) -> str:
        if self._git(self.repository_root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise SelfUpdateError("production checkout must be clean before checkpointing")
        commit = self._git(self.repository_root, "rev-parse", "HEAD")
        if not _COMMIT.fullmatch(commit):
            raise SelfUpdateError("current Git commit is invalid")
        checkpoint_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{commit[:12]}-{uuid.uuid4().hex[:8]}"
        temporary = Path(tempfile.mkdtemp(prefix=".checkpoint-", dir=self.checkpoint_root))
        final = self.checkpoint_root / checkpoint_id
        try:
            bundle = temporary / "source.bundle"
            self._run_checked(
                ("/usr/bin/git", "bundle", "create", str(bundle), "HEAD"),
                self.repository_root,
                "checkpoint_bundle_failed",
            )
            bundle.chmod(0o600)
            copied: list[str] = []
            for relative in _CHECKPOINT_FILES:
                source = self.repository_root / relative
                if not source.exists():
                    continue
                if source.is_symlink() or not source.is_file():
                    raise SelfUpdateError("checkpoint source file is unsafe")
                destination = temporary / "tracked" / relative
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
                copied.append(relative)
            private_config = self._read_private_config()
            if private_config:
                encrypted = _CHECKPOINT_BOX.encrypt(
                    json.dumps(private_config, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                    self._key,
                )
                _exclusive_write(temporary / "private-config.enc", encrypted)
            database_reference = self._validated_database_reference(database_backup)
            manifest = {
                "format_version": 1,
                "checkpoint_id": checkpoint_id,
                "created_at": _utc_now(),
                "commit": commit,
                "tracked_configuration": copied,
                "private_configuration": bool(private_config),
                "database_backup": database_reference,
            }
            _exclusive_write(
                temporary / "manifest.json",
                (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            checksums = []
            for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
                relative = path.relative_to(temporary).as_posix()
                if relative == "SHA256SUMS":
                    continue
                checksums.append(f"{_sha256(path)}  {relative}\n")
            checksum_content = "".join(checksums).encode("ascii")
            _exclusive_write(temporary / "SHA256SUMS", checksum_content)
            authentication = hmac.new(
                self._key,
                checksum_content,
                hashlib.sha256,
            ).hexdigest().encode("ascii") + b"\n"
            _exclusive_write(
                temporary / _CHECKPOINT_AUTHENTICATION_FILE,
                authentication,
            )
            _make_checkpoint_tree_owner_only(temporary)
            self.verify_checkpoint(temporary)
            temporary.rename(final)
            return checkpoint_id
        except BaseException:
            _remove_private_temporary(temporary, self.checkpoint_root, ".checkpoint-")
            raise

    def _provision_candidate_dependencies(self, candidate: Path) -> None:
        """Copy the currently verified dependency environments into isolation.

        Reflinks are requested when supported, never hard links. Candidate
        gates still decide whether those dependencies are compatible.
        """
        for relative in ("node_modules", "backend/.venv"):
            source = self.repository_root / relative
            destination = candidate / relative
            if not source.exists():
                continue
            if source.is_symlink() or not source.is_dir() or destination.exists():
                raise SelfUpdateError("candidate dependency source is unsafe")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._run_checked(
                (
                    "/bin/cp",
                    "--archive",
                    "--reflink=auto",
                    str(source),
                    str(destination),
                ),
                candidate,
                "candidate_dependency_copy_failed",
            )

    def verify_checkpoint(self, checkpoint: Path | str) -> dict[str, object]:
        root = self._resolve_checkpoint(checkpoint)
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            checksum_content = (root / "SHA256SUMS").read_bytes()
            checksum_lines = checksum_content.decode("ascii").splitlines()
            authentication = (root / _CHECKPOINT_AUTHENTICATION_FILE).read_text(
                encoding="ascii"
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SelfUpdateError("checkpoint metadata is invalid") from exc
        expected_authentication = hmac.new(
            self._key,
            checksum_content,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(authentication.rstrip("\n"), expected_authentication):
            raise SelfUpdateError("checkpoint authentication failed")
        for entry in root.rglob("*"):
            metadata = entry.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise SelfUpdateError("checkpoint contains an unsafe entry")
        required_keys = {
            "format_version",
            "checkpoint_id",
            "created_at",
            "commit",
            "tracked_configuration",
            "private_configuration",
            "database_backup",
        }
        if (
            not isinstance(manifest, dict)
            or set(manifest) != required_keys
            or manifest["format_version"] != 1
            or not _COMMIT.fullmatch(str(manifest["commit"]))
        ):
            raise SelfUpdateError("checkpoint manifest is unsupported")
        expected_files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and path.name not in {"SHA256SUMS", _CHECKPOINT_AUTHENTICATION_FILE}
        }
        observed: dict[str, str] = {}
        for line in checksum_lines:
            digest, separator, relative = line.partition("  ")
            if (
                separator != "  "
                or relative in observed
                or relative not in expected_files
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                raise SelfUpdateError("checkpoint checksum file is invalid")
            observed[relative] = digest
        if set(observed) != expected_files:
            raise SelfUpdateError("checkpoint checksum set is incomplete")
        for relative, expected in observed.items():
            path = root / relative
            if path.is_symlink() or not path.is_file() or _sha256(path) != expected:
                raise SelfUpdateError("checkpoint integrity validation failed")
        self._run_checked(
            ("/usr/bin/git", "bundle", "verify", str(root / "source.bundle")),
            self.repository_root,
            "checkpoint_bundle_invalid",
        )
        if manifest["private_configuration"]:
            try:
                plaintext = _CHECKPOINT_BOX.decrypt(
                    (root / "private-config.enc").read_bytes(),
                    self._key,
                )
                payload = json.loads(plaintext.decode("utf-8"))
            except (OSError, UnicodeError, ValueError, SecretBoxError) as exc:
                raise SelfUpdateError("checkpoint private configuration is invalid") from exc
            self._validate_private_config_payload(payload)
        database_reference = manifest["database_backup"]
        if database_reference is not None:
            if not isinstance(database_reference, str):
                raise SelfUpdateError("checkpoint database reference is invalid")
            self._validated_database_reference(Path(database_reference))
        return manifest

    def activate_ready(self, *, user_confirmed: bool) -> UpdateState:
        if user_confirmed is not True:
            raise SelfUpdateError("activation requires the final owner decision")
        current = self.state()
        if current.status is not UpdateStatus.READY or current.candidate_directory is None:
            raise SelfUpdateError("no validated update is ready")
        candidate = Path(current.candidate_directory).resolve(strict=True)
        if candidate.parent != self.candidate_root or self._git(candidate, "rev-parse", "HEAD") != current.candidate_commit:
            raise SelfUpdateError("validated candidate is no longer intact")
        self.verify_checkpoint(current.checkpoint_id or "")
        self._restore_private_config(current.checkpoint_id or "", candidate)
        previous = None
        if self.current_link.is_symlink():
            previous_path = self.current_link.resolve(strict=True)
            if previous_path.parent != self.candidate_root:
                raise SelfUpdateError("current release link is unsafe")
            previous = str(previous_path)
        elif self.current_link.exists():
            raise SelfUpdateError("current release path must be a symbolic link")
        _atomic_symlink(candidate, self.current_link)
        activated = replace(
            current,
            status=UpdateStatus.ACTIVATED,
            activated_at=_utc_now(),
            previous_release=previous,
        )
        self._write_state(activated)
        return activated

    def reconcile_ready_candidate(self) -> UpdateState:
        """Fail closed when an unactivated candidate no longer advances production."""
        current = self.state()
        if current.status is not UpdateStatus.READY:
            return current
        active_commit = self._git(self.repository_root, "rev-parse", "HEAD")
        candidate_commit = current.candidate_commit
        if candidate_commit == active_commit:
            failure_code = "candidate_already_active"
        elif candidate_commit is None or not _COMMIT.fullmatch(candidate_commit):
            failure_code = "candidate_state_invalid"
        else:
            advances_production = subprocess.run(
                (
                    "/usr/bin/git",
                    "merge-base",
                    "--is-ancestor",
                    active_commit,
                    candidate_commit,
                ),
                cwd=self.repository_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            if advances_production.returncode == 0:
                return current
            superseded = subprocess.run(
                (
                    "/usr/bin/git",
                    "merge-base",
                    "--is-ancestor",
                    candidate_commit,
                    active_commit,
                ),
                cwd=self.repository_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            failure_code = (
                "candidate_superseded"
                if superseded.returncode == 0
                else "candidate_diverged"
            )
        reconciled = replace(
            current,
            status=UpdateStatus.FAILED,
            failure_code=failure_code,
        )
        self._write_state(reconciled)
        return reconciled

    def cancel_ready(self) -> UpdateState:
        current = self.state()
        if current.status is not UpdateStatus.READY:
            raise SelfUpdateError("no validated update is ready")
        cancelled = replace(
            current,
            status=UpdateStatus.CANCELLED,
            failure_code="owner_cancelled",
        )
        self._write_state(cancelled)
        return cancelled

    def check_health_and_rollback(self, health_probe: Callable[[Path], bool]) -> UpdateState:
        current = self.state()
        if current.status is not UpdateStatus.ACTIVATED:
            return current
        candidate = self.current_link.resolve(strict=True)
        try:
            healthy = health_probe(candidate) is True
        except Exception:
            healthy = False
        if healthy:
            return current
        if current.previous_release is None:
            failed = replace(
                current,
                status=UpdateStatus.FAILED,
                failure_code="health_failed_without_previous_release",
            )
            self._write_state(failed)
            return failed
        previous = Path(current.previous_release).resolve(strict=True)
        if previous.parent != self.candidate_root:
            raise SelfUpdateError("rollback release is outside the managed root")
        _atomic_symlink(previous, self.current_link)
        rolled_back = replace(
            current,
            status=UpdateStatus.ROLLED_BACK,
            failure_code="post_activation_health_failed",
        )
        self._write_state(rolled_back)
        return rolled_back

    def _read_private_config(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        total = 0
        for relative in _PRIVATE_CONFIG_FILES:
            source = self.repository_root / relative
            if not source.exists():
                continue
            metadata = source.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise SelfUpdateError("private configuration file is unsafe")
            total += metadata.st_size
            if total > MAX_CONFIG_BYTES:
                raise SelfUpdateError("private configuration exceeds checkpoint bound")
            payload[relative] = base64.b64encode(source.read_bytes()).decode("ascii")
        return payload

    @staticmethod
    def _validate_private_config_payload(payload: object) -> dict[str, str]:
        if not isinstance(payload, dict) or set(payload) - set(_PRIVATE_CONFIG_FILES):
            raise SelfUpdateError("checkpoint configuration paths are invalid")
        decoded: dict[str, str] = {}
        total = 0
        for relative, encoded in payload.items():
            if not isinstance(encoded, str):
                raise SelfUpdateError("checkpoint configuration content is invalid")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise SelfUpdateError("checkpoint configuration content is invalid") from exc
            total += len(raw)
            if total > MAX_CONFIG_BYTES:
                raise SelfUpdateError("checkpoint configuration exceeds its bound")
            decoded[relative] = encoded
        return decoded

    def _restore_private_config(self, checkpoint: str, destination: Path) -> None:
        root = self._resolve_checkpoint(checkpoint)
        manifest = self.verify_checkpoint(root)
        if not manifest["private_configuration"]:
            return
        plaintext = _CHECKPOINT_BOX.decrypt((root / "private-config.enc").read_bytes(), self._key)
        payload = json.loads(plaintext.decode("utf-8"))
        self._validate_private_config_payload(payload)
        for relative, encoded in payload.items():
            target = destination / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.exists() and (target.is_symlink() or not target.is_file()):
                raise SelfUpdateError("candidate private configuration target is unsafe")
            _atomic_write(target, base64.b64decode(encoded), 0o600)

    def _validated_database_reference(self, database_backup: Path | None) -> str | None:
        if database_backup is None:
            return None
        backup = Path(database_backup).resolve(strict=True)
        completed = subprocess.run(
            (
                str(self.repository_root / "backend/.venv/bin/python"),
                str(self.repository_root / "scripts/backup_tool.py"),
                "verify",
                str(backup),
            ),
            cwd=self.repository_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            raise SelfUpdateError("database checkpoint verification failed")
        return str(backup)

    def _resolve_checkpoint(self, checkpoint: Path | str) -> Path:
        candidate = Path(checkpoint)
        requested = candidate if candidate.is_absolute() else self.checkpoint_root / candidate
        if requested.is_symlink():
            raise SelfUpdateError("checkpoint path is unsafe")
        root = requested.resolve(strict=True)
        if root.parent != self.checkpoint_root or root.is_symlink() or not root.is_dir():
            raise SelfUpdateError("checkpoint path is unsafe")
        return root

    def _load_or_create_key(self) -> bytes:
        try:
            metadata = self.key_path.lstat()
        except FileNotFoundError:
            key = os.urandom(KEY_BYTES)
            _exclusive_write(self.key_path, key)
            return key
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size != KEY_BYTES
        ):
            raise SelfUpdateError("checkpoint encryption key is unsafe")
        return self.key_path.read_bytes()

    def _resolve_commit(self, ref: str) -> str:
        if not isinstance(ref, str) or not ref or len(ref) > 256 or ref.startswith("-"):
            raise ValueError("candidate reference is invalid")
        commit = self._git(self.repository_root, "rev-parse", "--verify", f"{ref}^{{commit}}")
        if not _COMMIT.fullmatch(commit):
            raise SelfUpdateError("candidate reference did not resolve to a commit")
        return commit

    @staticmethod
    def _terminal_validation_state(
        validating: UpdateState,
        results: tuple[GateResult, ...],
        failure: str,
    ) -> UpdateState:
        return replace(
            validating,
            status=UpdateStatus.FAILED,
            gate_results=results,
            failure_code=failure,
        )

    def _write_state(self, state: UpdateState) -> None:
        payload = asdict(state)
        payload["status"] = state.status.value
        _atomic_write(
            self.state_path,
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            0o600,
        )

    @staticmethod
    def _run_checked(command: tuple[str, ...], cwd: Path, failure: str) -> None:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
            check=False,
        )
        if completed.returncode != 0:
            raise SelfUpdateError(failure)

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ("/usr/bin/git", *arguments),
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise SelfUpdateError("Git operation failed")
        return completed.stdout.strip()


def _run_gate_sandboxed(gate: ValidationGate, candidate: Path) -> subprocess.CompletedProcess[str]:
    candidate = candidate.resolve(strict=True)
    if not candidate.is_dir():
        raise ValueError("update candidate sandbox root must be a directory")
    mounts: dict[str, str] = {
        "/usr": "readonly",
        "/bin": "readonly",
        "/lib": "readonly",
        "/lib64": "readonly",
        "/etc/ld.so.cache": "readonly",
        str(candidate): "rw",
    }
    for argument in gate.command[1:]:
        path = Path(argument)
        if (
            path.is_absolute()
            and path.exists()
            and candidate not in path.parents
            and not any(parent in path.parents or path == parent for parent in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")))
        ):
            mounts[str(path.resolve(strict=True))] = "readonly"
    for path in gate.read_only_paths:
        mounts[str(path.resolve(strict=True))] = "readonly"
    if any(any(character in path for character in (",", "\n", "\r")) for path in mounts):
        raise ValueError("update sandbox mount path is unsupported")
    container_name = (
        f"work-station-update-{gate.name}-{os.getpid()}-{uuid.uuid4().hex}"
    )
    mount_arguments = tuple(
        f"--mount=type=bind,src={path},dst={path}"
        + (",readonly" if access == "readonly" else "")
        for path, access in mounts.items()
    )
    command = (
        "/usr/bin/docker",
        "run",
        "--rm",
        "--pull=never",
        f"--name={container_name}",
        f"--network={'bridge' if gate.network_access else 'none'}",
        "--ipc=none",
        "--read-only",
        "--memory=12g",
        "--memory-swap=12g",
        "--pids-limit=2048",
        f"--cpus={max(1, min(8, os.cpu_count() or 1))}",
        "--shm-size=1g",
        "--ulimit=nofile=4096:4096",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={os.getuid()}:{os.getgid()}",
        f"--env=HOME={Path.home()}",
        "--env=PATH=/usr/local/bin:/usr/bin:/bin",
        "--env=LD_LIBRARY_PATH=/lib/x86_64-linux-gnu:/usr/lib/x86_64-linux-gnu",
        "--env=NPM_CONFIG_CACHE=/tmp/npm-cache",
        "--env=XDG_CACHE_HOME=/tmp/cache",
        "--env=WORK_STATION_ISOLATED_UPDATE_VALIDATION=1",
        *mount_arguments,
        f"--tmpfs=/tmp:rw,exec,nosuid,nodev,size=4g,mode=700,uid={os.getuid()},gid={os.getgid()}",
        f"--workdir={candidate}",
        _UPDATE_SANDBOX_IMAGE,
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=5s",
        f"{gate.timeout_seconds}s",
        *gate.command,
    )
    docker_environment = {
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "HOME": "/nonexistent",
        "PATH": "/usr/bin:/bin",
    }
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=gate.timeout_seconds + 10,
            check=False,
            env=docker_environment,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(
            ("/usr/bin/docker", "rm", "--force", container_name),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
            env=docker_environment,
        )
        raise


def _atomic_symlink(target: Path, link: Path) -> None:
    temporary = link.parent / f".{link.name}.{uuid.uuid4().hex}.tmp"
    os.symlink(target, temporary)
    try:
        os.replace(temporary, link)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _exclusive_write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())


def _make_checkpoint_tree_owner_only(root: Path) -> None:
    root.chmod(0o700)
    for entry in root.rglob("*"):
        metadata = entry.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            entry.chmod(0o700)
        elif stat.S_ISREG(metadata.st_mode):
            entry.chmod(0o600)
        else:
            raise SelfUpdateError("checkpoint contains an unsafe entry")


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _remove_private_temporary(path: Path, parent: Path, prefix: str) -> None:
    if path.exists() and path.parent == parent and path.name.startswith(prefix):
        shutil.rmtree(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_text(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
