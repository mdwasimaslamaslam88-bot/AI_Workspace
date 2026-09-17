#!/usr/bin/env python3
"""Persisted ASTER master/student loop controller.

This controller is deliberately small and boring at the orchestration boundary:
the repository's existing Agent OS, ToolService, DEX gateway, reports, and
release gates remain authoritative.  The controller only selects one queue
item, starts one bounded Codex child, captures its result, and records evidence.

The command is safe to restart.  A started iteration remains the current
iteration until its child process has a terminal record, so an interruption
does not silently advance the queue or lose the prompt.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import tarfile
from typing import Any, Iterator
import urllib.error
from urllib.parse import urlsplit
import urllib.request


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
DEFAULT_EVIDENCE_ROOT = Path(
    os.environ.get(
        "ASTER_EVIDENCE_ROOT", "/home/md-wasim/AI_Workspace_Data/aster-evidence"
    )
).resolve()
STATUS_JSON = REPOSITORY_ROOT / "reports/ASTER_AI_OS_MASTER_STUDENT_STATUS.json"
STATUS_MD = REPOSITORY_ROOT / "reports/ASTER_AI_OS_MASTER_STUDENT_STATUS.md"
QUEUE_JSON = REPOSITORY_ROOT / "reports/ASTER_AI_OS_ISSUE_QUEUE.json"
PROGRESS_JSON = REPOSITORY_ROOT / "reports/ASTER_AI_OS_PROGRESS.json"
PROGRESS_MD = REPOSITORY_ROOT / "reports/ASTER_AI_OS_PROGRESS.md"
MAX_HISTORY = 100
MAX_ITERATIONS_PER_INVOCATION = 8
DEFAULT_CHILD_TIMEOUT = 1800
DEFAULT_WATCH_INTERVAL_SECONDS = 300
DEFAULT_MAX_WATCH_CYCLES = 12
# Model acquisition remains bounded and resumable, but the former 15-minute /
# 30-minute defaults made realistic local coding-model acquisition impossible.
# These are hard controller ceilings, not an instruction to pull blindly.
DEFAULT_MAX_DOWNLOAD_SECONDS = 10_800
DEFAULT_MAX_ESTIMATED_DOWNLOAD_HOURS = 3.0
DEFAULT_EXTERNAL_CODING_CANDIDATE = "qwen2.5-coder:3b"
HOST_CONTROLLER_CANDIDATES = (
    "qwen2.5-coder:3b",
    "qwen2.5-coder:1.5b",
    "deepseek-coder:1.3b",
    "starcoder2:3b",
    "codegemma:2b",
)
CURRENT_RELEASE_ARTIFACT_NAMES = frozenset(
    {
        "Work_Station_Ubuntu.AppImage",
        "Work_Station_Ubuntu.deb",
        "Work_Station_PWA.tar.gz",
    }
)
WATCH_RANGE_BYTES = 1024 * 1024
FOCUSED_ADMISSION_ROUTES = frozenset(
    {
        "explicit_candidate",
        "generic_coding",
        "generic_code_generation",
        "automatic_task_aware_agent_os",
    }
)
FOCUSED_ADMISSION_TESTS = frozenset(
    {"medium-coding-04", "model-comparison-coder-06"}
)

UNRESOLVED_STATUSES = {
    "OPEN",
    "INVESTIGATING",
    "REPRODUCED",
    "FIXED",
    "VERIFYING",
    "REGRESSION",
    "REOPENED",
}
TERMINAL_QUEUE_STATUSES = {"VERIFIED", "CLOSED", "BLOCKED_EXTERNAL"}
REPORT_ONLY_PATH_PREFIXES = ("reports/ASTER_AI_OS_",)
STATE_INTERNAL_BASE_SNAPSHOT = "_aster_persist_base_snapshot"
WATCH_CYCLE_METADATA_KEYS = frozenset(
    {
        "cycle",
        "status",
        "candidate",
        "candidate_state",
        "current_action",
        "next_retry_at",
        "report_commit",
        "evidence",
        "child_result",
        "trusted_parent_gate",
        "focused_objective_gate",
        "all_eight_objective_pass",
        "genericity_and_canonical_validation_allowed",
        "rejection_reason",
    }
)

# The issue queue records product defects and external blockers.  It is not a
# complete release work queue: current-source provenance and controller
# integrity still need work after all queue entries become terminal.  Keep
# those bounded tasks in the same persisted controller state so the canonical
# owner can select them before returning to external model watch.
ACCEPTANCE_TASK_SPECS = (
    {
        "id": "ASTER-STATE-MERGE-001",
        "priority": "P0",
        "title": "Verify monotonic persisted state merge",
        "action": "Run the isolated both-write-order and restart regression for the external-watch cycle metadata bundle.",
        "dependencies": [],
        "verification": "The focused regression passes in a temporary evidence root and proves cycle, evidence, action, decision and exclusions remain consistent after restart.",
    },
    {
        "id": "ASTER-RUNTIME-PROVENANCE-001",
        "priority": "P1",
        "title": "Verify current runtime identity",
        "action": "Re-observe the authenticated current runtime and validate source, backend and web identity against the current application source.",
        "dependencies": ["ASTER-STATE-MERGE-001"],
        "verification": "Parent-side observation reports runtime identity PASS with current-source content hashes; historical benchmark/release gaps remain explicit.",
    },
    {
        "id": "ASTER-RELEASE-VALIDATION-001",
        "priority": "P1",
        "title": "Run current-source release validation",
        "action": "Run the existing current-source release/security/artifact validation at the settled source tip and preserve every external failure.",
        "dependencies": ["ASTER-RUNTIME-PROVENANCE-001"],
        "verification": "The existing release gate produces hashable current evidence or a precise external/native-platform blocker; no historical report is relabeled.",
    },
    {
        "id": "ASTER-ARTIFACT-PROVENANCE-001",
        "priority": "P1",
        "title": "Attest current release artifacts",
        "action": "Bind the latest successful current-source release gate to the actual AppImage, Debian package and served PWA artifact hashes.",
        "dependencies": ["ASTER-RELEASE-VALIDATION-001"],
        "verification": "A current-source artifact attestation records safe existing artifact paths, recomputed SHA256 values, the successful release evidence and current Git identity; historical attestations remain preserved.",
    },
    {
        "id": "ASTER-CANONICAL-CODER-001",
        "priority": "P2",
        "title": "Resolve unchanged canonical coder cases",
        "action": "Admit a genuinely new coding-capable model only through the existing integrity and trusted 8-record gate.",
        "dependencies": [],
        "verification": "Both unchanged coder cases pass all eight objective route/case records and the unchanged 459-case benchmark has no non-pass.",
        "external_blocker": "All configured candidates are rejected or acquisition-blocked; a new eligible verified model/digest is required.",
    },
    {
        "id": "ASTER-REVERSE-CALLBACK-001",
        "priority": "P4",
        "title": "Verify AI OS to ASTER callback",
        "action": "Execute the authenticated reverse callback through the supported parent MCP transport.",
        "dependencies": [],
        "verification": "Executable owner/capability/correlation-validated callback and audit evidence exists in the current runtime.",
        "external_blocker": "The supported parent MCP/reverse callback endpoint rejects the available credential or is unavailable.",
    },
    {
        "id": "ASTER-DEX-001",
        "priority": "P4",
        "title": "Verify DEX live boundary",
        "action": "Re-test the existing DEX gateway under the current host permissions without bypassing the sandbox.",
        "dependencies": [],
        "verification": "Live DEX communication has capability/provenance evidence, or the host restriction is reproduced and fail-closed evidence is preserved.",
        "external_blocker": "The host sandbox denies the required network namespace operation (RTM_NEWADDR/EPERM).",
    },
    {
        "id": "ASTER-VOICE-001",
        "priority": "P4",
        "title": "Verify canonical voice path",
        "action": "Re-run the current voice path and exact-WAV replay with objective transcript and cleanup evidence.",
        "dependencies": [],
        "verification": "Voice result is classified LOCAL_PASS, PARTIAL or BLOCKED_EXTERNAL from current evidence; no model prose is accepted.",
        "external_blocker": "Stable provider/hardware behavior for the remaining acoustic/model variance is unavailable.",
    },
)


def default_acceptance_tasks() -> dict[str, dict[str, Any]]:
    now = utc_now()
    tasks: dict[str, dict[str, Any]] = {}
    for spec in ACCEPTANCE_TASK_SPECS:
        task = copy.deepcopy(spec)
        task.update(
            {
                "status": "READY" if not spec.get("dependencies") and "external_blocker" not in spec else ("PENDING" if spec.get("dependencies") else "BLOCKED_EXTERNAL"),
                "attempts": 0,
                "updated_at": now,
                "evidence": [],
                "last_result": None,
            }
        )
        tasks[str(spec["id"])] = task
    return tasks


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n", mode=mode)


def safe_text(value: Any, limit: int = 4000) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


CODEX_VERSION_PATTERN = re.compile(r"codex-cli\s+v?(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)
NODE_VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def tree_digest(root: Path, *, suffixes: frozenset[str] | None = None) -> str | None:
    """Compute the bounded, no-symlink tree digest used by runtime identity."""
    try:
        if root.is_symlink() or not root.is_dir():
            return None
        paths: list[Path] = []
        for path in root.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                return None
            if path.is_file() and (suffixes is None or path.suffix in suffixes):
                paths.append(path)
                if len(paths) > 4096:
                    return None
        if not paths:
            return None
        result = hashlib.sha256()
        total = 0
        for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
            content_digest = hashlib.sha256()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source:
                while chunk := source.read(65_536):
                    total += len(chunk)
                    if total > 64 * 1024 * 1024:
                        return None
                    content_digest.update(chunk)
            result.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            result.update(content_digest.digest())
        return result.hexdigest()
    except (OSError, RuntimeError):
        return None


def resolve_codex_cli() -> dict[str, Any]:
    """Resolve the newest verified Codex CLI visible to this process.

    The child must not inherit an accidental older `codex` earlier in PATH
    when a newer interactive installation is also available. An explicit
    ASTER_CODEX_BIN remains authoritative and fails closed if invalid.
    """
    explicit = os.environ.get("ASTER_CODEX_BIN", "").strip()
    raw_candidates: list[str] = []
    if explicit:
        raw_candidates.append(explicit)
    else:
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if entry:
                raw_candidates.append(str(Path(entry) / "codex"))
        located = shutil.which("codex")
        if located:
            raw_candidates.append(located)
        # systemd/user services commonly receive a minimal PATH and therefore
        # miss the interactive NVM installation. Discover the same current
        # user-managed installations explicitly, then select the highest
        # verified semantic version. This is discovery, not a stale version
        # pin; an unavailable path is simply ignored.
        nvm_root = Path.home() / ".nvm" / "versions" / "node"
        try:
            raw_candidates.extend(str(path) for path in sorted(nvm_root.glob("*/bin/codex")))
        except OSError:
            pass
        for directory in (Path.home() / ".local" / "bin", Path("/usr/local/bin"), Path("/snap/bin")):
            raw_candidates.append(str(directory / "codex"))

    paths: list[Path] = []
    seen: set[str] = set()
    for raw in raw_candidates:
        candidate = Path(raw)
        if not candidate.is_absolute():
            located = shutil.which(raw)
            if not located:
                continue
            candidate = Path(located)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        key = str(resolved)
        if key in seen or not resolved.is_file() or not os.access(resolved, os.X_OK):
            continue
        seen.add(key)
        paths.append(resolved)

    tested: list[dict[str, Any]] = []
    for path in paths:
        try:
            version_result = subprocess.run(
                [str(path), "--version"],
                cwd=str(REPOSITORY_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            tested.append({"path": str(path), "status": f"VERSION_PROBE_FAILED: {error}"})
            continue
        version_output = f"{version_result.stdout}\n{version_result.stderr}".strip()
        match = CODEX_VERSION_PATTERN.search(version_output)
        if version_result.returncode != 0 or not match:
            tested.append(
                {
                    "path": str(path),
                    "status": "VERSION_INVALID",
                    "exit_code": version_result.returncode,
                    "output": safe_text(version_output, 500),
                }
            )
            continue
        version = ".".join(match.groups())
        tested.append(
            {
                "path": str(path),
                "version": version,
                "version_sort_key": [int(part) for part in match.groups()],
                "status": "VERIFIED",
            }
        )

    verified = [item for item in tested if item.get("status") == "VERIFIED"]
    if not verified:
        mode = "ASTER_CODEX_BIN" if explicit else "PATH"
        raise RuntimeError(f"no verified Codex CLI executable found via {mode}: {tested}")
    selected = max(verified, key=lambda item: tuple(item["version_sort_key"]))
    return {
        "path": selected["path"],
        "version": selected["version"],
        "selection": "explicit" if explicit else "highest_verified_semver",
        "candidates": tested,
    }


def resolve_node_runtime(base_environment: dict[str, str] | None = None) -> dict[str, Any]:
    """Resolve a supported Node/npm pair for trusted parent-side gates.

    systemd user services intentionally have a minimal PATH.  If that PATH
    selects an older system Node, modern workspace tooling can fail before the
    release gate starts (for example, ``node:util`` lacking ``styleText``).
    Discover user-managed NVM installations as well as PATH candidates and
    select the highest verified semantic version without pinning a stale
    release.  The returned environment only prepends the selected bin
    directory; all other variables remain unchanged.
    """
    environment = dict(base_environment or os.environ)
    path_value = environment.get("PATH", "")
    raw_nodes: list[Path] = []
    for entry in path_value.split(os.pathsep):
        if entry:
            raw_nodes.append(Path(entry) / "node")
    located = shutil.which("node", path=path_value)
    if located:
        raw_nodes.append(Path(located))
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    try:
        raw_nodes.extend(sorted(nvm_root.glob("*/bin/node")))
    except OSError:
        pass

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_nodes:
        try:
            path = raw.resolve(strict=True)
        except OSError:
            continue
        key = str(path)
        if key in seen or not path.is_file() or not os.access(path, os.X_OK):
            continue
        seen.add(key)
        try:
            version_result = subprocess.run(
                [str(path), "--version"],
                cwd=str(REPOSITORY_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            candidates.append({"path": str(path), "status": f"VERSION_PROBE_FAILED: {error}"})
            continue
        version_output = f"{version_result.stdout}\n{version_result.stderr}".strip()
        match = NODE_VERSION_PATTERN.fullmatch(version_output)
        npm_path = path.parent / "npm"
        if version_result.returncode != 0 or not match or not npm_path.is_file() or not os.access(npm_path, os.X_OK):
            candidates.append(
                {
                    "path": str(path),
                    "npm_path": str(npm_path),
                    "status": "VERSION_INVALID_OR_NPM_MISSING",
                    "exit_code": version_result.returncode,
                    "output": safe_text(version_output, 500),
                }
            )
            continue
        version = ".".join(match.groups())
        candidates.append(
            {
                "path": str(path),
                "npm_path": str(npm_path),
                "version": version,
                "version_sort_key": [int(part) for part in match.groups()],
                "status": "VERIFIED",
            }
        )

    verified = [item for item in candidates if item.get("status") == "VERIFIED"]
    if not verified:
        raise RuntimeError(f"no verified Node/npm runtime found: {candidates}")
    selected = max(verified, key=lambda item: tuple(item["version_sort_key"]))
    bin_dir = str(Path(str(selected["path"])).parent)
    existing_path = environment.get("PATH", "")
    environment["PATH"] = bin_dir + (os.pathsep + existing_path if existing_path else "")
    return {
        "node_path": selected["path"],
        "npm_path": selected["npm_path"],
        "node_version": selected["version"],
        "selection": "highest_verified_semver",
        "candidates": candidates,
        "environment": environment,
    }


def command_record(
    command: list[str],
    cwd: Path,
    *,
    timeout: float = 30,
    input_text: str | None = None,
    environment: dict[str, str] | None = None,
    output_directory: Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()

    def persist_output(stdout: str, stderr: str) -> dict[str, str]:
        if output_directory is None:
            return {}
        output_directory.mkdir(parents=True, exist_ok=True)
        stdout_path = output_directory / "command.stdout.log"
        stderr_path = output_directory / "command.stderr.log"
        atomic_write(stdout_path, stdout, mode=0o600)
        atomic_write(stderr_path, stderr, mode=0o600)
        return {"stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            input=input_text,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        record = {
            "command": command,
            "cwd": str(cwd),
            "exit_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 4),
            "stdout": safe_text(stdout),
            "stderr": safe_text(stderr),
            "stdout_sha256": sha256_bytes(stdout.encode()),
            "stderr_sha256": sha256_bytes(stderr.encode()),
            "timed_out": False,
        }
        record.update(persist_output(stdout, stderr))
        return record
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        record = {
            "command": command,
            "cwd": str(cwd),
            "exit_code": 124,
            "duration_seconds": round(time.monotonic() - started, 4),
            "stdout": safe_text(stdout),
            "stderr": safe_text(stderr),
            "stdout_sha256": sha256_bytes(stdout.encode()),
            "stderr_sha256": sha256_bytes(stderr.encode()),
            "timed_out": True,
        }
        record.update(persist_output(stdout, stderr))
        return record
    except OSError as error:
        record = {
            "command": command,
            "cwd": str(cwd),
            "exit_code": 127,
            "duration_seconds": round(time.monotonic() - started, 4),
            "stdout": "",
            "stderr": safe_text(f"{type(error).__name__}: {error}"),
            "stdout_sha256": sha256_bytes(b""),
            "stderr_sha256": sha256_bytes(str(error).encode()),
            "timed_out": False,
        }
        record.update(persist_output("", record["stderr"]))
        return record


def capture_current_runtime_attestation(
    evidence_dir: Path, source_commit: str | None
) -> dict[str, Any]:
    """Capture authenticated current-runtime identity without persisting secrets."""
    started = time.monotonic()
    api_origin = os.environ.get(
        "ASTER_RUNTIME_API_ORIGIN", "http://127.0.0.1:8000"
    ).rstrip("/")
    token_path = Path.home() / ".ai_workspace_provisioning_token"
    records: dict[str, Any] = {
        "timestamp": utc_now(),
        "api_origin": api_origin,
        "source_commit": source_commit,
        "token_path": str(token_path),
        "authenticated_exchange": {},
        "session_revocation": {},
    }
    access_token = ""
    try:
        parsed_origin = urlsplit(api_origin)
        if (
            parsed_origin.scheme != "http"
            or parsed_origin.hostname != "127.0.0.1"
            or parsed_origin.username
            or parsed_origin.password
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
            or not 1 <= (parsed_origin.port or 80) <= 65535
        ):
            raise ValueError("runtime attestation origin must be loopback HTTP")
        api_origin = f"http://127.0.0.1:{parsed_origin.port or 80}"
        records["api_origin"] = api_origin
    except ValueError as error:
        records["error"] = type(error).__name__
        records["session_revocation"] = {
            "status": 0,
            "accepted": False,
            "error": "NO_ACCESS_TOKEN",
        }
        records["duration_seconds"] = round(time.monotonic() - started, 4)
        records["objective_pass"] = False
        write_json(evidence_dir / "runtime-identity-current.json", records, mode=0o600)
        return records

    def request_json(
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 15,
    ) -> tuple[int, bytes, dict[str, Any]]:
        request = urllib.request.Request(
            f"{api_origin}{path}",
            data=body,
            headers=headers or {},
            method=method,
        )
        request_started = time.monotonic()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=timeout) as response:
                payload = response.read()
                return int(response.status), payload, {
                    "status": int(response.status),
                    "duration_seconds": round(time.monotonic() - request_started, 4),
                    "response_bytes": len(payload),
                    "response_sha256": sha256_bytes(payload),
                }
        except urllib.error.HTTPError as error:
            payload = error.read(4096) if hasattr(error, "read") else b""
            return int(error.code), payload, {
                "status": int(error.code),
                "duration_seconds": round(time.monotonic() - request_started, 4),
                "response_bytes": len(payload),
                "response_sha256": sha256_bytes(payload),
                "error": "HTTP_ERROR",
            }
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            return 0, b"", {
                "status": 0,
                "duration_seconds": round(time.monotonic() - request_started, 4),
                "response_bytes": 0,
                "response_sha256": sha256_bytes(b""),
                "error": type(error).__name__,
            }

    try:
        token = token_path.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("provisioning token is empty")
        provision_status, provision_body, provision_record = request_json(
            "POST",
            "/api/v1/users",
            headers={
                "Content-Type": "application/json",
                "X-User-Provisioning-Token": token,
            },
            body=b"{}",
        )
        records["authenticated_exchange"]["provision"] = provision_record
        if provision_status != 201:
            raise RuntimeError("user provisioning was not accepted")
        provisioned = json.loads(provision_body.decode("utf-8"))
        access_token = str(provisioned.get("access_token", ""))
        if not access_token:
            raise RuntimeError("provisioning response did not contain an access token")
        runtime_status, runtime_body, runtime_record = request_json(
            "GET",
            "/api/v1/diagnostics/runtime-identity",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        records["authenticated_exchange"]["runtime_identity"] = runtime_record
        if runtime_status != 200:
            raise RuntimeError("runtime identity request was not accepted")
        runtime_payload = json.loads(runtime_body.decode("utf-8"))
        if not isinstance(runtime_payload, dict):
            raise ValueError("runtime identity response was not an object")
        records["runtime"] = runtime_payload
        expected_backend = tree_digest(
            REPOSITORY_ROOT / "backend" / "app",
            suffixes=frozenset({".py", ".json"}),
        )
        expected_web = tree_digest(REPOSITORY_ROOT / "frontend" / "dist")
        records["expected"] = {
            "source_commit": source_commit,
            "backend_source_sha256": expected_backend,
            "web_bundle_sha256": expected_web,
        }
        records["matches"] = {
            "source_commit": runtime_payload.get("source_commit") == source_commit,
            "backend_source_sha256": runtime_payload.get("backend_source_sha256") == expected_backend,
            "web_bundle_sha256": runtime_payload.get("web_bundle_sha256") == expected_web,
        }
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        records["error"] = type(error).__name__
    finally:
        if access_token:
            revoke_status, _revoke_body, revoke_record = request_json(
                "DELETE",
                "/api/v1/users/me/sessions/current",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            records["session_revocation"] = revoke_record
            records["session_revocation"]["accepted"] = revoke_status == 204
        else:
            records["session_revocation"] = {
                "status": 0,
                "accepted": False,
                "error": "NO_ACCESS_TOKEN",
            }
        records["duration_seconds"] = round(time.monotonic() - started, 4)
        records["objective_pass"] = bool(
            isinstance(records.get("runtime"), dict)
            and isinstance(records.get("matches"), dict)
            and records["matches"]
            and all(records["matches"].values())
            and records.get("session_revocation", {}).get("accepted") is True
        )
        write_json(evidence_dir / "runtime-identity-current.json", records, mode=0o600)
    return records


def application_source_commit(head: str | None = None) -> str | None:
    """Return the source tip, excluding a commit containing only live reports."""
    current = head
    if not isinstance(current, str) or not COMMIT_PATTERN.fullmatch(current):
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPOSITORY_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        current = result.stdout.strip()
    if not isinstance(current, str) or not COMMIT_PATTERN.fullmatch(current):
        return None
    files_result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", current],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    changed = [line.strip() for line in files_result.stdout.splitlines() if line.strip()]
    if not changed or not all(
        any(path.startswith(prefix) for prefix in REPORT_ONLY_PATH_PREFIXES)
        for path in changed
    ):
        return current
    parent_result = subprocess.run(
        ["git", "rev-parse", f"{current}^"],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    parent = parent_result.stdout.strip()
    if not COMMIT_PATTERN.fullmatch(parent):
        return current
    # Multiple report-only commits can be appended while the application
    # source remains unchanged. Walk the complete report-only ancestry so a
    # report tip cannot accidentally become the claimed application tip.
    return application_source_commit(parent)


def report_only_commit_aliases(head: str | None, source_commit: str | None) -> tuple[str, ...]:
    """Return report-only commits between the report tip and source tip.

    Evidence may be captured at a source tip and then have only the live ASTER
    reports committed afterward. Those report commits do not change the
    executable source, but they are still real commits that can appear in
    benchmark/release attestations. Accept only that bounded ancestry; if any
    application commit occurs before the source tip, return no aliases so old
    evidence cannot be promoted.
    """
    if (
        not isinstance(head, str)
        or not COMMIT_PATTERN.fullmatch(head)
        or not isinstance(source_commit, str)
        or not COMMIT_PATTERN.fullmatch(source_commit)
        or head == source_commit
    ):
        return ()
    aliases: list[str] = []
    current = head
    seen: set[str] = set()
    while current != source_commit and current not in seen:
        seen.add(current)
        files_result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", current],
            cwd=str(REPOSITORY_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        changed = [line.strip() for line in files_result.stdout.splitlines() if line.strip()]
        if not changed or not all(
            any(path.startswith(prefix) for prefix in REPORT_ONLY_PATH_PREFIXES)
            for path in changed
        ):
            return ()
        aliases.append(current)
        parent_result = subprocess.run(
            ["git", "rev-parse", f"{current}^"],
            cwd=str(REPOSITORY_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        parent = parent_result.stdout.strip()
        if not COMMIT_PATTERN.fullmatch(parent):
            return ()
        current = parent
    return tuple(aliases) if current == source_commit else ()


def git_snapshot() -> dict[str, Any]:
    def run(args: list[str]) -> tuple[int, str, str]:
        result = subprocess.run(
            ["git", *args],
            cwd=str(REPOSITORY_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    _, commit, _ = run(["rev-parse", "HEAD"])
    _, branch, _ = run(["branch", "--show-current"])
    _, status, _ = run(["status", "--porcelain=v1", "--untracked-files=all"])
    _, upstream, upstream_error = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    ahead = behind = None
    if upstream:
        code, counts, _ = run(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
        if code == 0 and len(counts.split()) == 2:
            ahead, behind = (int(item) for item in counts.split())
    snapshot = {
        "branch": branch,
        "commit": commit,
        "application_source_commit": application_source_commit(commit),
        "upstream": upstream or None,
        "upstream_error": upstream_error or None,
        "ahead": ahead,
        "behind": behind,
        "clean": not bool(status),
        "status_porcelain": status,
        "status": (
            "CLEAN_SYNCED"
            if not status and ahead == 0 and behind == 0
            else "CLEAN_AHEAD"
            if not status and (ahead or 0) > 0 and (behind or 0) == 0
            else "CLEAN_DIVERGED"
            if not status
            else "DIRTY"
        ),
    }
    return snapshot


def current_status() -> dict[str, Any]:
    value = read_json(STATUS_JSON, {})
    return value if isinstance(value, dict) else {}


def current_queue() -> dict[str, Any]:
    value = read_json(QUEUE_JSON, {})
    if not isinstance(value, dict):
        value = {}
    value.setdefault("issues", [])
    return value


def issue_classification(issue: dict[str, Any]) -> str:
    status = str(issue.get("status", "OPEN"))
    if status in {"VERIFIED", "CLOSED"}:
        return "COMPLETE"
    if status == "BLOCKED_EXTERNAL":
        return "BLOCKED_EXTERNAL"
    if status == "REGRESSION":
        return "REGRESSION"
    if status == "VERIFYING":
        return "VERIFYING"
    if status == "FIXED":
        return "VERIFYING"
    if status == "REPRODUCED" or issue.get("reproduction"):
        return "REPRODUCED"
    return "ROOT_CAUSE_IDENTIFIED" if issue.get("root_cause") else "REPRODUCED"


def issue_priority(issue: dict[str, Any]) -> str:
    issue_id = issue.get("id")
    status = issue.get("status")
    if issue_id == "ASTER-027":
        return "P0"
    if issue_id in {"ASTER-026", "ASTER-028"}:
        return "P1"
    if issue_id in {"ASTER-006", "ASTER-007", "ASTER-022", "ASTER-031"}:
        return "P2"
    if status == "BLOCKED_EXTERNAL":
        return "P4"
    return "P3"


def unresolved_issues(queue: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in queue.get("issues", [])
        if isinstance(item, dict) and item.get("status") in UNRESOLVED_STATUSES
    ]


def choose_issue(queue: dict[str, Any]) -> dict[str, Any] | None:
    candidates = unresolved_issues(queue)
    if not candidates:
        return None
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    return sorted(
        candidates,
        key=lambda item: (order[issue_priority(item)], str(item.get("id", ""))),
    )[0]


def _release_dependency_mismatch_proven(
    parent: dict[str, Any] | None,
    task: dict[str, Any] | None,
    evidence_dir: Path | None = None,
) -> bool:
    """Require preserved Expo Doctor output before selecting that repair kind."""
    candidate_paths: list[str] = []
    verification = parent.get("verification") if isinstance(parent, dict) else None
    if isinstance(verification, dict):
        candidate_paths.extend(
            item
            for item in (verification.get("stdout_path"), verification.get("stderr_path"))
            if isinstance(item, str)
        )
    if isinstance(task, dict):
        failure_context = task.get("failure_context")
        if isinstance(failure_context, dict):
            candidate_paths.extend(
                item
                for item in (
                    failure_context.get("parent_stdout_path"),
                    failure_context.get("parent_stderr_path"),
                )
                if isinstance(item, str)
            )
        # Do not search the task's complete historical evidence list here.
        # An older release may legitimately contain an Expo mismatch even
        # though the current failure is a native launch, tool, or test
        # failure.  The repair kind must be derived only from the current
        # parent result and its explicitly linked failure bundle.
    if evidence_dir is not None:
        candidate_paths.extend(
            str(evidence_dir / name) for name in ("command.stdout.log", "command.stderr.log")
        )
    required = (
        "expo                ~57.0.23",
        "expo-image-picker   ~57.0.18",
        "expo-notifications  ~57.0.19",
        "packages out of date",
    )
    for raw_path in dict.fromkeys(candidate_paths):
        path = Path(raw_path)
        try:
            if path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
                if all(marker in content for marker in required):
                    return True
        except OSError:
            continue
    return False


def acceptance_task_records(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = state.get("acceptance_tasks")
    if not isinstance(raw, dict):
        raw = default_acceptance_tasks()
        state["acceptance_tasks"] = raw
    # Add newly introduced task definitions without resetting existing status,
    # evidence, exclusions or retry budgets from an older controller.
    for task_id, spec in default_acceptance_tasks().items():
        current = raw.get(task_id)
        if not isinstance(current, dict):
            raw[task_id] = spec
            continue
        for key, value in spec.items():
            current.setdefault(key, copy.deepcopy(value))
        # A prior controller version classified a stale runtime attestation as
        # FAILED after its parent check completed.  That is a resumable local
        # provenance task, not a permanent decision; migrate it once into the
        # bounded retry state so a current attestation can be captured.
        if (
            task_id == "ASTER-RUNTIME-PROVENANCE-001"
            and current.get("status") == "FAILED"
            and int(current.get("attempts", 0) or 0) < 2
        ):
            current["status"] = "RETRY"
            current["blocker"] = "Previous runtime attestation was historical; recapture authenticated current-source identity."
            current["updated_at"] = utc_now()
    # Dynamic repair tasks are intentionally not part of the static default
    # task definitions above.  Still recover a failed repair inside its own
    # finite budget after restart; otherwise a stale FAILED record forces the
    # original task to create a suffixed duplicate repair.
    for current in raw.values():
        if (
            isinstance(current, dict)
            and current.get("repair_kind")
            and current.get("status") == "FAILED"
            and int(current.get("attempts", 0) or 0) < int(current.get("max_attempts", 2) or 2)
        ):
            current["status"] = "RETRY"
            current["blocker"] = "Previous bounded repair failed locally; retry after the repair implementation was corrected."
            current["updated_at"] = utc_now()
        if (
            isinstance(current, dict)
            and current.get("repair_kind") == "release_dependency_alignment"
            and current.get("status") in {"READY", "PENDING", "RETRY"}
            and not _release_dependency_mismatch_proven(None, current)
        ):
            # A release failure may be caused by a missing executable,
            # packaging defect, or another local problem.  Never let a stale
            # pre-classified Expo repair hide that failure without objective
            # Expo Doctor output proving the dependency mismatch.
            current["status"] = "SUPERSEDED"
            current["blocker"] = (
                "Superseded because preserved evidence does not prove the exact Expo dependency "
                "mismatch; the originating release failure remains authoritative."
            )
            current["updated_at"] = utc_now()
        if (
            isinstance(current, dict)
            and current.get("repair_kind") == "runtime_deployment_repair"
            and current.get("status") == "RETRY"
            and int(current.get("attempts", 0) or 0) >= int(current.get("max_attempts", 2) or 2)
            and not current.get("readiness_retry_granted")
            and isinstance(current.get("last_result"), dict)
            and current["last_result"].get("parent_verification", {}).get("failure_classification")
            == "LOCAL_RUNTIME_HEALTH_FAILED"
        ):
            # One failed attempt used the pre-fix immediate probe and one was
            # interrupted before parent verification. Grant exactly one
            # explicit retry for the now-validated readiness-wait fix; do not
            # reset the cumulative attempt count or create an endless budget.
            current["max_attempts"] = int(current.get("attempts", 0) or 0) + 1
            current["readiness_retry_granted"] = True
            current["blocker"] = "One bounded retry granted for the validated service-readiness probe fix."
            current["updated_at"] = utc_now()
        if (
            isinstance(current, dict)
            and current.get("repair_kind") == "runtime_deployment_repair"
            and current.get("status") == "READY"
            and current.get("last_result") is None
            and not current.get("interrupted_retry_granted")
            and int(current.get("attempts", 0) or 0) >= int(current.get("max_attempts", 2) or 2)
        ):
            # A supervisor can die after launching a bounded child but before
            # persisting that child's terminal result. Preserve consumed
            # attempts and grant exactly one recovery slot; this is not a
            # budget reset or an unlimited retry of a failed repair.
            current["max_attempts"] = int(current.get("attempts", 0) or 0) + 1
            current["interrupted_retry_granted"] = True
            current["blocker"] = (
                "One bounded recovery attempt granted because the prior owner exited before "
                "persisting a terminal repair result; cumulative attempts are preserved."
            )
            current["updated_at"] = utc_now()
    # A pre-fix owner could have created suffixed repairs for the same
    # acceptance failure. Keep one deterministic active repair and retain the
    # other records as historical evidence instead of executing duplicates.
    repair_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for current in raw.values():
        if not isinstance(current, dict):
            continue
        parent_id = current.get("parent_task_id")
        repair_kind = current.get("repair_kind")
        if isinstance(parent_id, str) and isinstance(repair_kind, str):
            repair_groups.setdefault((parent_id, repair_kind), []).append(current)
    active_statuses = {"READY", "PENDING", "RETRY"}
    for group in repair_groups.values():
        active = [task for task in group if task.get("status") in active_statuses]
        if len(active) < 2:
            continue
        keep = sorted(active, key=lambda task: str(task.get("id", "")))[0]
        for duplicate in active:
            if duplicate is keep:
                continue
            duplicate["status"] = "SUPERSEDED"
            duplicate["blocker"] = f"Superseded by the canonical bounded repair {keep.get('id')}; historical evidence retained."
            duplicate["updated_at"] = utc_now()
    # A completed repair is a durable resolution of its parent acceptance
    # task. Reapply that relationship after restart before dependency
    # selection, while retaining the parent's original failed result.
    for current in raw.values():
        if not isinstance(current, dict) or current.get("status") != "COMPLETE":
            continue
        parent_id = current.get("parent_task_id")
        parent = raw.get(parent_id) if isinstance(parent_id, str) else None
        if not isinstance(parent, dict):
            continue
        resolution = parent.get("repair_resolution")
        if not isinstance(resolution, dict):
            continue
        # A completed repair resolves one specific failure occurrence.  Once
        # the parent records a later failure signature, the old resolution is
        # historical and must not reopen or otherwise rewrite that new
        # failure on every restart.
        if (
            resolution.get("failure_signature")
            != parent.get("active_failure_signature")
        ):
            continue
        repair_kind = current.get("repair_kind")
        if repair_kind == "runtime_deployment_repair":
            if parent.get("status") != "COMPLETE" and not parent.get("repair_resolution"):
                parent["status"] = "COMPLETE"
                parent["blocker"] = None
                parent["repair_resolution"] = {
                    "repair_task_id": current.get("id"),
                    "updated_at": current.get("updated_at"),
                    "evidence": current.get("evidence", []),
                }
                parent["updated_at"] = utc_now()
        elif repair_kind == "release_dependency_alignment" and parent.get("status") == "FAILED":
            parent["status"] = "PENDING"
            parent["blocker"] = "Dependency correction completed; run the full current-source release gate."
            parent["updated_at"] = utc_now()
    return raw


def _task_validated_source_commit(task: dict[str, Any]) -> str | None:
    value = task.get("validated_source_commit")
    if isinstance(value, str) and COMMIT_PATTERN.fullmatch(value):
        return value
    last_result = task.get("last_result")
    if isinstance(last_result, dict):
        parent = last_result.get("parent_verification")
        verification = parent.get("verification") if isinstance(parent, dict) else None
        observations = verification.get("observations") if isinstance(verification, dict) else None
        git = observations.get("git") if isinstance(observations, dict) else None
        value = git.get("application_source_commit") if isinstance(git, dict) else None
        if isinstance(value, str) and COMMIT_PATTERN.fullmatch(value):
            return value
    report_commit = task.get("last_report_commit")
    if isinstance(report_commit, dict):
        git = report_commit.get("git")
        value = git.get("application_source_commit") if isinstance(git, dict) else None
        if isinstance(value, str) and COMMIT_PATTERN.fullmatch(value):
            return value
    return None


def refresh_source_bound_acceptance_tasks(
    state: dict[str, Any], source_commit: str | None
) -> bool:
    """Reopen current-source gates when application code has changed.

    Report-only commits do not invalidate application evidence because
    ``source_commit`` is the bounded application ancestry tip. A real source
    change does invalidate runtime/release attestations and must schedule
    those gates again before they can support readiness.
    """
    if not isinstance(source_commit, str) or not COMMIT_PATTERN.fullmatch(source_commit):
        return False
    changed = False
    tasks = acceptance_task_records(state)
    for task_id in (
        "ASTER-RUNTIME-PROVENANCE-001",
        "ASTER-RELEASE-VALIDATION-001",
        "ASTER-ARTIFACT-PROVENANCE-001",
    ):
        task = tasks.get(task_id)
        if not isinstance(task, dict) or task.get("status") not in {
            "COMPLETE", "COMPLETE_WITH_EXTERNAL", "FAILED", "RETRY", "PENDING"
        }:
            continue
        validated = _task_validated_source_commit(task)
        if validated and validated != source_commit:
            task["status"] = "RETRY" if task_id == "ASTER-RUNTIME-PROVENANCE-001" else "PENDING"
            task["blocker"] = (
                f"Application source changed from {validated} to {source_commit}; "
                "current-source evidence must be recaptured."
            )
            task["updated_at"] = utc_now()
            changed = True
            # A failed repair is source-bound too.  Leaving it active after a
            # real application change would make choose_acceptance_task skip
            # the parent forever, even when the new source contains the
            # correction that makes the original gate actionable again.
            for repair in tasks.values():
                if (
                    not isinstance(repair, dict)
                    or repair.get("parent_task_id") != task_id
                    or repair.get("status") not in {"READY", "PENDING", "RETRY", "FAILED"}
                ):
                    continue
                repair_source = _task_validated_source_commit(repair)
                if repair_source and repair_source != source_commit:
                    repair["status"] = "SUPERSEDED"
                    repair["blocker"] = (
                        f"Superseded after application source changed from {repair_source} "
                        f"to {source_commit}; historical repair evidence retained."
                    )
                    repair["updated_at"] = utc_now()
                    changed = True
    # Diagnosis repairs are bound to the application source that produced
    # their originating failure.  An exhausted diagnosis from an older source
    # must not strand a fresh parent gate after a later source-bound repair;
    # retain it as historical evidence and let the parent run again.  A
    # current-source failed diagnosis remains blocking.
    for repair in tasks.values():
        if (
            not isinstance(repair, dict)
            or repair.get("repair_kind") != "acceptance_failure_diagnosis"
            or repair.get("status") != "FAILED"
        ):
            continue
        parent_id = repair.get("parent_task_id")
        parent = tasks.get(str(parent_id)) if isinstance(parent_id, str) else None
        origin = repair.get("originating_failure_source_commit")
        if not isinstance(origin, str) or not COMMIT_PATTERN.fullmatch(origin):
            context = repair.get("failure_context")
            origin = (
                context.get("originating_failure_source_commit")
                if isinstance(context, dict)
                else None
            )
        if not isinstance(origin, str) or not COMMIT_PATTERN.fullmatch(origin):
            origin = _task_validated_source_commit(parent) if isinstance(parent, dict) else None
        if not isinstance(origin, str) or origin == source_commit:
            continue
        repair["status"] = "SUPERSEDED"
        repair["blocker"] = (
            f"Superseded because its originating acceptance failure was bound to {origin}, "
            f"while the current application source is {source_commit}; historical diagnosis "
            "is retained and the parent gate is eligible for fresh validation."
        )
        repair["superseded_by_source_commit"] = source_commit
        repair["updated_at"] = utc_now()
        changed = True
    return changed


def choose_acceptance_task(state: dict[str, Any]) -> dict[str, Any] | None:
    tasks = acceptance_task_records(state)
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    eligible: list[dict[str, Any]] = []
    terminal = {"COMPLETE", "COMPLETE_WITH_EXTERNAL", "BLOCKED_EXTERNAL"}
    for task in tasks.values():
        if not isinstance(task, dict) or task.get("status") not in {"READY", "PENDING", "RETRY"}:
            continue
        child_repairs = [
            candidate for candidate in tasks.values()
            if isinstance(candidate, dict)
            and candidate.get("parent_task_id") == task.get("id")
        ]
        if any(
            candidate.get("status") in {"READY", "PENDING", "RETRY"}
            for candidate in child_repairs
        ):
            # A ready bounded repair owns the next attempt.  Selecting the
            # parent first (both are commonly P1) causes a failed release to
            # rerun indefinitely while its diagnosis/correction waits behind
            # it in lexical priority order.
            continue
        if any(
            candidate.get("status") == "FAILED"
            or (
                candidate.get("status") in {"READY", "PENDING", "RETRY"}
                and candidate.get("max_attempts") is not None
                and int(candidate.get("attempts", 0) or 0) >= int(candidate.get("max_attempts") or 0)
            )
            for candidate in child_repairs
        ):
            # A parent with an exhausted repair must not be blindly rerun.
            # Its objective failure stays visible until a new evidence-backed
            # repair becomes available.
            continue
        max_attempts = task.get("max_attempts")
        if max_attempts is not None:
            try:
                if int(task.get("attempts", 0) or 0) >= int(max_attempts):
                    continue
            except (TypeError, ValueError):
                continue
        dependencies = task.get("dependencies", [])
        if not isinstance(dependencies, list):
            dependencies = []
        if any(
            not isinstance(tasks.get(str(dependency)), dict)
            or tasks[str(dependency)].get("status") not in terminal
            for dependency in dependencies
        ):
            continue
        eligible.append(task)
    return sorted(
        eligible,
        key=lambda item: (order.get(str(item.get("priority")), 9), str(item.get("id", ""))),
    )[0] if eligible else None


def _verified_external_failure(parent: dict[str, Any]) -> dict[str, Any] | None:
    """Accept an external classification only with explicit objective proof."""
    if not isinstance(parent, dict) or parent.get("objective_pass") is not False:
        return None
    verification = parent.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    classification = parent.get("failure_classification") or verification.get("failure_classification")
    if classification != "BLOCKED_EXTERNAL":
        return None
    evidence = parent.get("external_evidence") or verification.get("external_evidence")
    if isinstance(evidence, str):
        evidence_paths = [evidence]
    elif isinstance(evidence, list):
        evidence_paths = [str(item) for item in evidence if str(item).strip()]
    else:
        evidence_paths = []
    if not evidence_paths or not all(Path(item).exists() for item in evidence_paths):
        return None
    unblock_condition = parent.get("unblock_condition") or verification.get("unblock_condition")
    if not isinstance(unblock_condition, str) or not unblock_condition.strip():
        return None
    return {
        "classification": "BLOCKED_EXTERNAL",
        "evidence": evidence_paths,
        "unblock_condition": unblock_condition.strip(),
        "reason": str(parent.get("reason") or "verified external dependency remains unavailable"),
    }


def trusted_loopback_health(evidence_dir: Path, *, max_attempts: int = 15) -> dict[str, Any]:
    """Wait for the restarted service with a bounded, evidence-backed probe."""
    attempts: list[dict[str, Any]] = []
    for number in range(1, max_attempts + 1):
        probe = command_record(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                "http://127.0.0.1:8000/api/v1/health/live",
            ],
            REPOSITORY_ROOT,
            timeout=10,
            output_directory=evidence_dir / f"attempt-{number:02d}",
        )
        probe["attempt"] = number
        attempts.append(probe)
        if probe.get("exit_code") == 0:
            return {
                "command": probe.get("command"),
                "cwd": probe.get("cwd"),
                "exit_code": 0,
                "passed": True,
                "attempt_count": number,
                "attempts": attempts,
            }
        if number < max_attempts:
            _bounded_sleep(1)
    return {
        "command": attempts[-1].get("command") if attempts else [],
        "cwd": str(REPOSITORY_ROOT),
        "exit_code": attempts[-1].get("exit_code", 1) if attempts else 1,
        "passed": False,
        "attempt_count": len(attempts),
        "attempts": attempts,
    }


def enqueue_acceptance_failure_repair(
    state: dict[str, Any],
    failed_task: dict[str, Any],
    *,
    parent: dict[str, Any],
    child: dict[str, Any],
    evidence_dir: Path,
) -> dict[str, Any] | None:
    """Create one bounded actionable repair task for a non-external failure."""
    parent_task_id = str(failed_task.get("id"))
    originating_failure_source_commit = _task_validated_source_commit(failed_task)
    # A repair task failing is itself the bounded diagnosis result; do not
    # manufacture an unbounded chain of repair-of-repair tasks.
    if failed_task.get("repair_kind") or failed_task.get("parent_task_id"):
        return None
    tasks = acceptance_task_records(state)
    failure_context = {
        "task_id": parent_task_id,
        "originating_failure_source_commit": originating_failure_source_commit,
        "evidence_dir": str(evidence_dir),
        "parent_reason": parent.get("reason"),
        "parent_failure_classification": parent.get("failure_classification"),
        "parent_stdout_path": (
            parent.get("verification", {}).get("stdout_path")
            if isinstance(parent.get("verification"), dict)
            else None
        ),
        "parent_stderr_path": (
            parent.get("verification", {}).get("stderr_path")
            if isinstance(parent.get("verification"), dict)
            else None
        ),
        "child_exit_code": child.get("exit_code") if isinstance(child, dict) else None,
        "child_timed_out": child.get("timed_out") if isinstance(child, dict) else None,
    }
    signature = sha256_bytes(json.dumps(failure_context, sort_keys=True, default=str).encode())
    failed_task["active_failure_signature"] = signature
    for existing in tasks.values():
        if not isinstance(existing, dict):
            continue
        if existing.get("failure_signature") == signature:
            return existing
        if (
            existing.get("parent_task_id") == parent_task_id
            and existing.get("status") in {"READY", "PENDING", "RETRY"}
        ):
            return existing

    if parent_task_id == "ASTER-RUNTIME-PROVENANCE-001":
        repair_kind = "runtime_deployment_repair"
        priority = "P0"
        title = "Repair stale runtime deployment after provenance failure"
        action = (
            "Use the trusted parent executor to restart work-station-backend.service, "
            "verify loopback health, and recapture authenticated current-source runtime identity. "
            f"Failure evidence: {evidence_dir}."
        )
        verification = "The restarted backend reports the current source commit and matching backend/web hashes, with session revocation accepted."
    elif parent_task_id == "ASTER-RELEASE-VALIDATION-001" and _release_dependency_mismatch_proven(
        parent, failed_task, evidence_dir
    ):
        repair_kind = "release_dependency_alignment"
        priority = "P1"
        title = "Repair proven Expo SDK dependency mismatch"
        action = (
            "Use preserved Expo Doctor output to align only the proven mismatched packages, "
            "then run the focused mobile check through the trusted parent executor. "
            f"Failure evidence: {evidence_dir}."
        )
        verification = "The correction is committed through the existing validated-change boundary and the focused mobile check exits 0."
    elif parent_task_id == "ASTER-RELEASE-VALIDATION-001":
        repair_kind = "acceptance_failure_diagnosis"
        priority = "P1"
        title = "Diagnose current release validation failure"
        action = (
            "Inspect the complete current release stdout/stderr and parent verification, identify the "
            "first local failure or prove a supported external dependency, and perform only a bounded "
            f"trusted repair. Failure evidence: {evidence_dir}."
        )
        verification = "The first failure has objective root-cause evidence and either a verified repair or a bounded external blocker with an unblock condition."
    else:
        repair_kind = "acceptance_failure_diagnosis"
        priority = "P1"
        title = f"Diagnose failed acceptance task {parent_task_id}"
        action = (
            f"Inspect the objective failure for {parent_task_id}, identify whether it is a local "
            f"defect or a supported external dependency, and run the narrowest permitted repair or proof. "
            f"Failure evidence: {evidence_dir}."
        )
        verification = "The failure has objective root-cause evidence and either a verified repair or a bounded external blocker with an unblock condition."

    base_id = f"ASTER-REPAIR-{parent_task_id}"
    repair_id = base_id
    suffix = 1
    while repair_id in tasks:
        suffix += 1
        repair_id = f"{base_id}-{suffix:03d}"
    now = utc_now()
    repair_task = {
        "id": repair_id,
        "priority": priority,
        "title": title,
        "action": action,
        "dependencies": [],
        "verification": verification,
        "status": "READY",
        "attempts": 0,
        "max_attempts": 2,
        "updated_at": now,
        "evidence": [str(evidence_dir)],
        "last_result": None,
        "parent_task_id": parent_task_id,
        "repair_kind": repair_kind,
        "failure_signature": signature,
        "originating_failure_source_commit": originating_failure_source_commit,
        "failure_context": failure_context,
    }
    tasks[repair_id] = repair_task
    return repair_task


def recover_persisted_acceptance_failure_repairs(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Recreate actionable repairs for failures recorded by an older owner.

    A controller restart must not turn a durable objective failure into an
    invisible dead end.  Only original acceptance tasks are eligible here;
    bounded repair tasks never recursively manufacture more repair tasks.
    """
    tasks = acceptance_task_records(state)
    recovered: list[dict[str, Any]] = []
    for task in list(tasks.values()):
        if not isinstance(task, dict) or task.get("status") != "FAILED":
            continue
        if task.get("repair_kind") or task.get("parent_task_id"):
            continue
        last_result = task.get("last_result")
        if not isinstance(last_result, dict):
            continue
        parent = last_result.get("parent_verification")
        if not isinstance(parent, dict) or parent.get("objective_pass") is not False:
            continue
        evidence_dir_value = parent.get("evidence_dir")
        if not isinstance(evidence_dir_value, str) or not evidence_dir_value:
            evidence = task.get("evidence")
            evidence_dir_value = evidence[-1] if isinstance(evidence, list) and evidence else None
        if not isinstance(evidence_dir_value, str) or not evidence_dir_value:
            continue
        repair = enqueue_acceptance_failure_repair(
            state,
            task,
            parent=parent,
            child=last_result.get("child", {}),
            evidence_dir=Path(evidence_dir_value),
        )
        if repair is not None:
            recovered.append(repair)
    return recovered


def evidence_items(issue: dict[str, Any]) -> list[str]:
    values = issue.get("evidence", [])
    if isinstance(values, list):
        return [str(item) for item in values[:12]]
    return [str(values)] if values else []


def required_action(issue: dict[str, Any]) -> str:
    issue_id = issue.get("id")
    if issue_id == "ASTER-006":
        return (
            "Run controlled generic prompt/context experiments for medium-coding-04. "
            "A production change is admissible only if the exact case improves and "
            "the unrelated canonical cases do not regress; never change the checker."
        )
    if issue_id == "ASTER-007":
        return (
            "Run controlled generic prompt/context experiments for model-comparison-coder-06 "
            "and evaluate the required base-case terminology. Any route or prompt change "
            "must improve both ASTER-006 and ASTER-007 and pass the unchanged 459-case benchmark."
        )
    if issue_id == "ASTER-022":
        return (
            "Independently reproduce the checker-contract counterexamples and document the "
            "separation between semantic validity and exact answer-form scoring. Do not alter "
            "benchmark expectations or checker behavior."
        )
    if issue_id == "ASTER-026":
        return (
            "Re-run the existing authenticated Agent OS advisory-review corpus on the current "
            "runtime, verify hashes and permission grants, and determine whether a local "
            "model/prompt repair is demonstrated. Model prose is not evidence."
        )
    if issue_id == "ASTER-027":
        return (
            "Reproduce the report-provenance defect against current HEAD. Verify current-source, "
            "runtime, artifact, benchmark, security and release evidence by content, preserve "
            "historical roots, and implement or verify the smallest guard/report repair."
        )
    if issue_id == "ASTER-028":
        return (
            "Re-run the canonical voice path and preserved exact-WAV replay with objective "
            "transcript/cleanup/security evidence. Separate LOCAL_PASS, PARTIAL and external "
            "provider/hardware limits; do not upgrade a voice result from model prose."
        )
    if issue_id == "ASTER-031":
        return (
            "Do not repeat the rejected global qwen3 routing experiment. Design a new bounded "
            "strategy only if it is generic and production-safe, then measure both target cases "
            "and the unchanged full benchmark before admitting any source change."
        )
    return (
        "Reproduce the issue, trace the existing implementation, identify root cause, make the "
        "smallest production fix, and run focused plus impacted regression tests."
    )


def verification_criteria(issue: dict[str, Any]) -> str:
    issue_id = issue.get("id")
    if issue_id in {"ASTER-006", "ASTER-007", "ASTER-031"}:
        return (
            "Objective output evidence for both coding cases; unchanged checker/case source; "
            "canonical 459-case benchmark with no unrelated regression; security and git gates pass."
        )
    if issue_id == "ASTER-027":
        return (
            "Positive current-evidence fixture accepted; failed, mismatched, stale, wrong-owner, "
            "wrong-commit and ambiguous-path fixtures rejected; current report fields match the "
            "validated evidence and final report-tip identity is recorded."
        )
    if issue_id == "ASTER-028":
        return (
            "Fresh voice smoke, exact-WAV replay, owner-scoped cleanup and redaction evidence; "
            "honest result classification and unchanged canonical score."
        )
    if issue_id == "ASTER-026":
        return (
            "Raw output hashes match the authenticated run, API permissions prove inference-only "
            "scope, unsupported claims are rejected, and no issue is marked fixed without an "
            "independent semantic proof."
        )
    return "Focused test and affected regression pass with machine-readable evidence and no security regression."


def stop_conditions(issue: dict[str, Any]) -> str:
    return (
        "Stop this iteration if the proof is absent, the change is benchmark-specific, an "
        "unrelated regression appears, a security gate fails, or the limitation is external; "
        "record the exact blocker and leave the queue status unchanged."
    )


def build_prompt(issue: dict[str, Any], observations: dict[str, Any]) -> str:
    issue_id = str(issue.get("id"))
    evidence = evidence_items(issue)
    evidence_block = "\n".join(f"- {item}" for item in evidence) or "- No linked evidence; create it."
    root = issue.get("root_cause") or "Not yet established; trace before changing code."
    benchmark = observations.get("benchmark", {})
    git = observations.get("git", {})
    return f"""ASTER AUTONOMOUS ITERATION — {issue_id}

You are one bounded child execution in the persisted ASTER Master ↔ Personal AI OS loop.
Repository: {REPOSITORY_ROOT}
Current source commit at selection: {git.get('application_source_commit') or git.get('commit', 'unknown')}
Current Git report tip at selection: {git.get('commit', 'unknown')}
Evidence root: {DEFAULT_EVIDENCE_ROOT}

CURRENT ISSUE
- ID: {issue_id}
- Queue status: {issue.get('status', 'OPEN')}
- Workflow classification: {issue_classification(issue)}
- Priority: {issue_priority(issue)}
- Description: {issue.get('description', '')}
- Root cause if known: {root}
- Existing evidence:
{evidence_block}

CURRENT OBSERVATION
- Benchmark: {benchmark.get('score', 'unknown')}/100, {benchmark.get('pass', '?')} PASS, {benchmark.get('partial', '?')} PARTIAL, {benchmark.get('fail', '?')} FAIL
- Runtime health: {observations.get('runtime', {}).get('status', 'unknown')}
- Git: {git.get('status', 'unknown')}

REQUIRED ACTION
{required_action(issue)}

WORKING RULES
- Reuse existing Agent OS, ToolService, DEX gateway, permission, audit, memory, RAG,
  filesystem and runtime infrastructure. Do not create competing frameworks.
- Inspect persisted evidence before probing. Record exact commands and working directories.
- Make the smallest robust production change only when objective evidence supports it.
- Do not edit benchmark expectations, checkers, canonical prompts, or output normalization
  merely to raise the score. Do not claim live communication from synthetic evidence.
- Do not commit; the parent controller owns validation, report persistence and commits.

VERIFICATION CRITERIA
{verification_criteria(issue)}

STOP CONDITIONS
{stop_conditions(issue)}

At the end, print exactly one machine-readable block, even when blocked:
ASTER_LOOP_RESULT
STATUS=<READY|NOT_READY|BLOCKED|FAILED>
ISSUES_REMAINING=<integer>
CURRENT_ISSUE={issue_id}
ACTION=<short action>
VERIFICATION=<PASS|FAIL|PARTIAL|BLOCKED>
NEXT_PROMPT_BEGIN
<a concrete next prompt, or state that the parent must derive it>
NEXT_PROMPT_END
"""


def build_acceptance_task_prompt(
    task: dict[str, Any], observations: dict[str, Any], evidence: str
) -> str:
    source_commit = observed_source_commit(observations) or "unknown"
    dependencies = ", ".join(str(item) for item in task.get("dependencies", [])) or "none"
    return f"""ASTER ACCEPTANCE TASK — {task.get('id')}

This is one bounded child in the existing ASTER controller. Do not create a
new orchestration layer and do not commit source changes from the child.
Repository: {REPOSITORY_ROOT}
Current application source commit: {source_commit}
Evidence directory: {evidence}
Task title: {task.get('title')}
Dependencies: {dependencies}

Required action:
{task.get('action')}

Inspect the current source, persisted reports and linked evidence. Separate
current evidence from historical evidence. Do not claim a runtime, release,
security, callback, DEX, voice or benchmark PASS from prose or file presence.
The trusted parent will independently run the task verification and may keep
the task incomplete even if your inspection is optimistic.

Verification criteria:
{task.get('verification')}

At the end emit exactly one result block with CURRENT_ISSUE set to
{task.get('id')} and a concrete next prompt for the next eligible task:
ASTER_LOOP_RESULT
STATUS=<READY|NOT_READY|BLOCKED|FAILED>
ISSUES_REMAINING=<integer>
CURRENT_ISSUE={task.get('id')}
ACTION=<short action>
VERIFICATION=<PASS|FAIL|PARTIAL|BLOCKED>
NEXT_PROMPT_BEGIN
<exact concrete next prompt>
NEXT_PROMPT_END
"""


def load_or_create_state(evidence_root: Path) -> dict[str, Any]:
    path = evidence_root / "current_state.json"
    value = read_json(path, {})
    if not isinstance(value, dict):
        value = {}
    value.setdefault("schema_version", 1)
    value.setdefault("controller", "aster_next_prompt.py")
    value.setdefault("repository_root", str(REPOSITORY_ROOT))
    value.setdefault("evidence_root", str(evidence_root))
    value.setdefault("iteration", 0)
    value.setdefault("attempt", 0)
    value.setdefault("iterations_completed", 0)
    value.setdefault("in_progress", False)
    value.setdefault("resume_required", False)
    value.setdefault("overall_ready", False)
    value.setdefault("status", "NOT_READY")
    value.setdefault("history", [])
    value.setdefault("issue_workflow", {})
    value.setdefault("next_prompt", "")
    value.setdefault("last_prompt", "")
    value.setdefault("last_result", None)
    value.setdefault("last_failure", None)
    value.setdefault("last_successful_commit", None)
    value.setdefault("last_codex_cli", None)
    value.setdefault("controller_phase", "RUNNING")
    value.setdefault("external_watch", {})
    value.setdefault("acceptance_tasks", default_acceptance_tasks())
    acceptance_task_records(value)
    value.pop(STATE_INTERNAL_BASE_SNAPSHOT, None)
    value[STATE_INTERNAL_BASE_SNAPSHOT] = copy.deepcopy(value)
    return value


def restore_resumable_attempt(state: dict[str, Any]) -> None:
    """Recover a timeout recorded by an older controller version as resumable."""
    result = state.get("last_result")
    child = result.get("child") if isinstance(result, dict) else None
    failure = state.get("last_failure")
    stderr_path = failure.get("stderr_path") if isinstance(failure, dict) else None
    if isinstance(failure, dict) and not failure.get("error_excerpt") and stderr_path:
        try:
            failure["error_excerpt"] = safe_text(
                Path(stderr_path).read_text(encoding="utf-8", errors="replace")[-2000:]
            )
        except OSError:
            pass
    if (
        not state.get("in_progress")
        and isinstance(result, dict)
        and isinstance(child, dict)
        and child.get("timed_out") is True
        and int(result.get("iteration", -1)) == int(state.get("iteration", -2))
    ):
        state["in_progress"] = True
        state["resume_required"] = True
        state["resume_recovered_at"] = utc_now()


def _persistable_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in state.items()
        if key != STATE_INTERNAL_BASE_SNAPSHOT
    }


def _event_order(value: Any) -> tuple[int, int, int, int, str]:
    if not isinstance(value, dict):
        return (-1, -1, -1, -1, "")

    def integer(key: str) -> int:
        try:
            return int(value.get(key, -1))
        except (TypeError, ValueError):
            return -1

    timestamp = str(
        value.get("timestamp")
        or value.get("recorded_at")
        or value.get("updated_at")
        or ""
    )
    return (
        integer("cycle"),
        integer("iteration"),
        integer("attempt"),
        integer("sequence"),
        timestamp,
    )


def _newer_event(disk_value: Any, local_value: Any) -> Any:
    if disk_value is None:
        return copy.deepcopy(local_value)
    if local_value is None:
        return copy.deepcopy(disk_value)
    return copy.deepcopy(
        local_value
        if _event_order(local_value) >= _event_order(disk_value)
        else disk_value
    )


def _merge_history(disk_value: Any, local_value: Any) -> list[Any]:
    values = []
    for collection in (disk_value, local_value):
        if isinstance(collection, list):
            values.extend(copy.deepcopy(collection))
    unique: dict[str, Any] = {}
    for item in values:
        try:
            identity = json.dumps(item, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            identity = repr(item)
        unique.setdefault(identity, item)
    return sorted(unique.values(), key=_event_order)[-MAX_HISTORY:]


def _merge_external_watch(disk_value: Any, local_value: Any) -> dict[str, Any]:
    disk = disk_value if isinstance(disk_value, dict) else {}
    local = local_value if isinstance(local_value, dict) else {}
    merged = copy.deepcopy(disk)
    disk_cycle = _event_order(disk)[0]
    local_cycle = _event_order(local)[0]
    for key, value in local.items():
        if key in {"rejected_candidates", "acquisition_blocked_candidates"}:
            candidates = set()
            for source in (disk.get(key), value):
                if isinstance(source, list):
                    candidates.update(str(item) for item in source)
            merged[key] = sorted(candidates)
        elif key in {"last_candidate_decision", "candidate_decision"}:
            merged[key] = _newer_event(disk.get(key), value)
        elif key == "candidate_priority" and disk.get(key):
            # A stale writer must not replace the current admission order.
            continue
        elif disk_cycle > local_cycle and key in WATCH_CYCLE_METADATA_KEYS:
            # Cycle identity and its associated evidence/action form one
            # durable event. A stale writer must not split that bundle by
            # replacing only the cycle number while leaving newer evidence.
            continue
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _merge_acceptance_tasks(disk_value: Any, local_value: Any) -> dict[str, Any]:
    """Merge task decisions without losing evidence or reopening newer work."""
    disk = disk_value if isinstance(disk_value, dict) else {}
    local = local_value if isinstance(local_value, dict) else {}
    merged = copy.deepcopy(disk)
    terminal = {"COMPLETE", "COMPLETE_WITH_EXTERNAL", "BLOCKED_EXTERNAL"}
    for task_id, local_task in local.items():
        if not isinstance(local_task, dict):
            continue
        disk_task = disk.get(task_id)
        if not isinstance(disk_task, dict):
            merged[task_id] = copy.deepcopy(local_task)
            continue
        disk_status = disk_task.get("status")
        local_status = local_task.get("status")
        if (
            disk_status in terminal
            and local_status not in terminal
            and _event_order(local_task) <= _event_order(disk_task)
        ):
            # A later stale writer may still have READY/PENDING in memory;
            # completed decisions and their evidence are durable facts. A
            # deliberately newer source-freshness invalidation is allowed to
            # reopen the gate and is identified by its newer updated_at.
            selected = copy.deepcopy(disk_task)
        else:
            selected = _newer_event(disk_task, local_task)
        # Task-level timestamps protect status/blocker decisions, while the
        # nested result has its own cycle/attempt timestamp. Merge it
        # independently so a later report/watch writer cannot erase a trusted
        # parent result merely because its task snapshot is stale.
        for key in ("last_result", "last_report_commit"):
            selected[key] = _newer_event(disk_task.get(key), local_task.get(key))
        selected["attempts"] = max(
            int(disk_task.get("attempts", 0) or 0),
            int(local_task.get("attempts", 0) or 0),
        )
        if isinstance(disk_task.get("evidence"), list) or isinstance(local_task.get("evidence"), list):
            evidence = []
            for source in (disk_task.get("evidence"), local_task.get("evidence")):
                if isinstance(source, list):
                    evidence.extend(str(item) for item in source)
            selected["evidence"] = list(dict.fromkeys(evidence))[-20:]
        # Source binding is part of the decision, not cosmetic report data.
        # Preserve the newest valid binding even when a stale task snapshot is
        # otherwise selected.
        selected["validated_source_commit"] = _newer_event(
            disk_task.get("validated_source_commit"),
            local_task.get("validated_source_commit"),
        )
        merged[task_id] = selected
    return merged


def _merge_persisted_state(
    disk_state: dict[str, Any],
    state: dict[str, Any],
    base_state: dict[str, Any] | None,
) -> dict[str, Any]:
    disk = _persistable_state(disk_state)
    local = _persistable_state(state)
    base = _persistable_state(base_state) if isinstance(base_state, dict) else None
    if base is None:
        changes = local
    else:
        changes = {
            key: value
            for key, value in local.items()
            if key != "updated_at" and (key not in base or value != base[key])
        }
    merged = copy.deepcopy(disk)
    for key, value in changes.items():
        if key == "external_watch":
            merged[key] = _merge_external_watch(disk.get(key), value)
        elif key == "acceptance_tasks":
            merged[key] = _merge_acceptance_tasks(disk.get(key), value)
        elif key == "history":
            merged[key] = _merge_history(disk.get(key), value)
        elif key in {"last_result", "last_failure", "last_candidate_decision"}:
            merged[key] = _newer_event(disk.get(key), value)
        else:
            merged[key] = copy.deepcopy(value)
    merged["updated_at"] = utc_now()
    merged["history"] = _merge_history(disk.get("history"), merged.get("history"))
    return merged


def persist_state(state: dict[str, Any], evidence_root: Path) -> None:
    """Persist state without allowing a stale writer to erase newer evidence."""
    evidence_root.mkdir(parents=True, exist_ok=True)
    state_path = evidence_root / "current_state.json"
    lock_path = evidence_root / "state.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        disk_value = read_json(state_path, {})
        disk_state = disk_value if isinstance(disk_value, dict) else {}
        merged = _merge_persisted_state(
            disk_state, state, state.get(STATE_INTERNAL_BASE_SNAPSHOT)
        )
        write_json(state_path, merged)
        state.clear()
        state.update(merged)
        state[STATE_INTERNAL_BASE_SNAPSHOT] = copy.deepcopy(merged)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def discover_latest_benchmark(evidence_root: Path, queue: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    candidates: list[Path] = []
    configured = queue.get("current_benchmark")
    if isinstance(configured, str):
        candidates.append(Path(configured))
    try:
        candidates.extend(evidence_root.glob("**/Work_Station_Benchmark/benchmark-summary.json"))
    except OSError:
        pass
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return {}, None
    path = max(existing, key=lambda item: item.stat().st_mtime_ns)
    value = read_json(path, {})
    return (value if isinstance(value, dict) else {}), str(path)


def discover_latest_release_gate(evidence_root: Path) -> tuple[dict[str, Any], str | None]:
    candidates: list[Path] = []
    for pattern in ("**/release-check-*.json", "**/final-release-gate*.json"):
        try:
            candidates.extend(evidence_root.glob(pattern))
        except OSError:
            pass
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return {}, None
    path = max(existing, key=lambda item: item.stat().st_mtime_ns)
    value = read_json(path, {})
    return (value if isinstance(value, dict) else {}), str(path)


def _write_current_pwa_archive(evidence_dir: Path) -> Path | None:
    """Create an evidence-local archive of the served web bundle.

    The release package deliberately does not duplicate the PWA, because the
    authenticated backend serves it.  Provenance still needs a file-level
    object to hash, so archive the current ``frontend/dist`` tree into the
    task's evidence directory.  Refuse symlinks and preserve relative names;
    this is an attestation input, not a deployment operation.
    """
    configured_source = REPOSITORY_ROOT / "frontend" / "dist"
    allowed_root = REPOSITORY_ROOT / "frontend"
    # Inspect the configured path before resolving it. Resolving first turns a
    # symlink into an ordinary directory and could attest an out-of-tree bundle.
    if configured_source.is_symlink() or not configured_source.is_dir():
        return None
    try:
        source = configured_source.resolve(strict=True)
        allowed = allowed_root.resolve(strict=True)
        source.relative_to(allowed)
    except (OSError, RuntimeError, ValueError):
        return None
    files: list[Path] = []
    try:
        for path in configured_source.rglob("*"):
            if path.is_symlink():
                return None
            if path.is_file():
                resolved = path.resolve(strict=True)
                try:
                    resolved.relative_to(source)
                except ValueError:
                    return None
                files.append(resolved)
    except OSError:
        return None
    if not files:
        return None
    archive = evidence_dir / "artifacts" / "Work_Station_PWA.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, mode="w:gz", compresslevel=9) as handle:
            for path in sorted(files):
                relative = path.relative_to(source)
                info = handle.gettarinfo(
                    str(path), arcname=Path("frontend/dist") / relative
                )
                # Make the evidence archive reproducible apart from its gzip
                # container timestamp and never preserve local ownership.
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                with path.open("rb") as source_handle:
                    handle.addfile(info, source_handle)
    except (OSError, tarfile.TarError, ValueError):
        archive.unlink(missing_ok=True)
        return None
    return archive if archive.is_file() and not archive.is_symlink() else None


def _release_artifact_paths() -> list[tuple[str, str, Path]]:
    package = read_json(REPOSITORY_ROOT / "apps" / "desktop" / "package.json", {})
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str) or not version.strip():
        return []
    bundle_root = REPOSITORY_ROOT / "apps" / "desktop" / "src-tauri" / "target" / "release" / "bundle"
    return [
        (
            "ubuntu-x86_64",
            "Work_Station_Ubuntu.AppImage",
            bundle_root / "appimage" / f"WORK STATION_{version}_amd64.AppImage",
        ),
        (
            "ubuntu-x86_64",
            "Work_Station_Ubuntu.deb",
            bundle_root / "deb" / f"WORK STATION_{version}_amd64.deb",
        ),
    ]


def _safe_artifact_record(
    platform: str, name: str, path: Path, *, built_by: str
) -> dict[str, Any]:
    safe = bool(path.is_absolute() and not path.is_symlink() and path.is_file())
    digest = sha256_file(path) if safe else None
    return {
        "platform": platform,
        "artifact": name,
        "path": str(path),
        "sha256": digest,
        "exists": safe,
        "hash_verified": bool(
            safe and isinstance(digest, str) and SHA256_PATTERN.fullmatch(digest)
        ),
        "built_by": built_by,
    }


def capture_release_artifact_manifest(
    *,
    evidence_dir: Path,
    source_commit: str,
    release_document: dict[str, Any],
    release_path: str,
    git: dict[str, Any],
    served_web_bundle_sha256: str | None,
) -> tuple[dict[str, Any], bool, Path]:
    """Capture the trusted release outputs before a later attestation."""
    release_log = release_document.get("output_path")
    release_log_hash = (
        sha256_file(Path(release_log))
        if isinstance(release_log, str) and Path(release_log).is_file()
        else None
    )
    release_source = release_document.get("application_source_commit") or release_document.get("commit")
    frontend_bundle_sha256 = tree_digest(REPOSITORY_ROOT / "frontend" / "dist")
    pwa_path = _write_current_pwa_archive(evidence_dir / "release-artifacts")
    records = [
        _safe_artifact_record(platform, name, path, built_by=release_path)
        for platform, name, path in _release_artifact_paths()
    ]
    if pwa_path is not None:
        pwa_record = _safe_artifact_record(
            "web-pwa", "Work_Station_PWA.tar.gz", pwa_path, built_by=release_path
        )
        pwa_record["source_tree_sha256"] = frontend_bundle_sha256
        records.append(pwa_record)
    names = {str(item.get("artifact")) for item in records}
    release_ok = bool(
        release_document.get("status") == "PASS"
        and release_document.get("exit_code") == 0
        and isinstance(release_path, str)
        and Path(release_path).is_file()
        and isinstance(release_document.get("output_sha256"), str)
        and release_document.get("output_sha256") == release_log_hash
        and release_source == source_commit
    )
    passed = bool(
        release_ok
        and git.get("clean") is True
        and git.get("application_source_commit") == source_commit
        and frontend_bundle_sha256
        and served_web_bundle_sha256 == frontend_bundle_sha256
        and names == CURRENT_RELEASE_ARTIFACT_NAMES
        and all(item.get("exists") and item.get("hash_verified") for item in records)
        and next(
            (
                item.get("source_tree_sha256") == frontend_bundle_sha256
                for item in records
                if item.get("artifact") == "Work_Station_PWA.tar.gz"
            ),
            False,
        )
    )
    manifest = {
        "schema_version": 1,
        "timestamp": utc_now(),
        "status": "PASS" if passed else "FAIL",
        "objective_pass": passed,
        "application_source_commit": source_commit,
        "release_gate": release_path,
        "release_gate_output_sha256": release_log_hash,
        "release_source_commit": release_source,
        "frontend_bundle_sha256": frontend_bundle_sha256,
        "served_web_bundle_sha256": served_web_bundle_sha256,
        "git": {
            "HEAD": git.get("commit"),
            "application_source_commit": git.get("application_source_commit"),
            "upstream": git.get("upstream"),
            "clean": git.get("clean"),
        },
        "artifacts": records,
    }
    manifest_path = evidence_dir / "release-artifact-manifest.json"
    write_json(manifest_path, manifest)
    return manifest, passed, manifest_path


def build_current_artifact_attestation(
    *,
    evidence_dir: Path,
    source_commit: str,
    release_document: dict[str, Any],
    release_path: str,
    artifact_paths: list[tuple[str, str, Path]],
    pwa_path: Path | None,
    git: dict[str, Any],
    release_manifest: dict[str, Any] | None = None,
    release_manifest_path: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Build and verify an attestation from the current release outputs.

    This helper accepts explicit paths so tests can use isolated fixtures.  It
    never treats a path's presence as proof: every record must be a regular
    non-symlink file and its digest must be computed at attestation time.
    """
    release_log = release_document.get("output_path")
    release_log_hash = (
        sha256_file(Path(release_log))
        if isinstance(release_log, str) and Path(release_log).is_file()
        else None
    )
    release_source = release_document.get("application_source_commit") or release_document.get("commit")
    release_ok = bool(
        release_document.get("status") == "PASS"
        and release_document.get("exit_code") == 0
        and isinstance(release_path, str)
        and Path(release_path).is_file()
        and isinstance(release_document.get("output_sha256"), str)
        and release_document.get("output_sha256") == release_log_hash
        and release_source == source_commit
    )
    records: list[dict[str, Any]] = [
        _safe_artifact_record(platform, name, path, built_by=release_path)
        for platform, name, path in artifact_paths
    ]
    if pwa_path is not None:
        records.append(
            _safe_artifact_record(
                "web-pwa", "Work_Station_PWA.tar.gz", pwa_path, built_by=release_path
            )
        )
    record_by_name = {str(record.get("artifact")): record for record in records}
    manifest_records = (
        release_manifest.get("artifacts")
        if isinstance(release_manifest, dict)
        else None
    )
    persisted_manifest = (
        read_json(Path(release_manifest_path), None)
        if isinstance(release_manifest_path, str)
        and Path(release_manifest_path).is_file()
        and not Path(release_manifest_path).is_symlink()
        else None
    )
    manifest_ok = bool(
        isinstance(persisted_manifest, dict)
        and isinstance(release_manifest, dict)
        and persisted_manifest == release_manifest
    )
    if isinstance(persisted_manifest, dict):
        release_manifest = persisted_manifest
        manifest_records = release_manifest.get("artifacts")
    manifest_by_name = (
        {
            str(record.get("artifact")): record
            for record in manifest_records
            if isinstance(record, dict) and record.get("artifact")
        }
        if isinstance(manifest_records, list)
        else {}
    )
    manifest_ok = bool(
        manifest_ok
        and isinstance(release_manifest, dict)
        and release_manifest.get("status") == "PASS"
        and release_manifest.get("objective_pass") is True
        and release_manifest.get("application_source_commit") == source_commit
        and release_manifest.get("release_gate") == release_path
        and release_document.get("artifact_manifest") == release_manifest_path
        and isinstance(release_manifest_path, str)
        and Path(release_manifest_path).is_file()
        and set(manifest_by_name) == CURRENT_RELEASE_ARTIFACT_NAMES
        and release_manifest.get("frontend_bundle_sha256")
        == release_manifest.get("served_web_bundle_sha256")
        and release_manifest.get("frontend_bundle_sha256")
        == tree_digest(REPOSITORY_ROOT / "frontend" / "dist")
    )
    for name in CURRENT_RELEASE_ARTIFACT_NAMES:
        current = record_by_name.get(name)
        manifest = manifest_by_name.get(name)
        if not isinstance(current, dict) or not isinstance(manifest, dict):
            manifest_ok = False
            continue
        if (
            current.get("path") != manifest.get("path")
            or current.get("sha256") != manifest.get("sha256")
            or current.get("hash_verified") is not True
            or manifest.get("hash_verified") is not True
        ):
            manifest_ok = False
    pwa_manifest = manifest_by_name.get("Work_Station_PWA.tar.gz")
    if not isinstance(pwa_manifest, dict) or pwa_path is None:
        manifest_ok = False
    elif pwa_manifest.get("source_tree_sha256") != tree_digest(REPOSITORY_ROOT / "frontend" / "dist"):
        manifest_ok = False
    artifact_hashes = {
        name: bool(
            record_by_name.get(name, {}).get("hash_verified") is True
            and manifest_by_name.get(name, {}).get("hash_verified") is True
            and record_by_name.get(name, {}).get("sha256")
            == manifest_by_name.get(name, {}).get("sha256")
        )
        for name in sorted(CURRENT_RELEASE_ARTIFACT_NAMES)
    }
    git_clean = git.get("clean") is True
    git_source = git.get("application_source_commit")
    passed = bool(
        release_ok
        and git_clean
        and git_source == source_commit
        and set(record_by_name) == CURRENT_RELEASE_ARTIFACT_NAMES
        and all(record["exists"] and record["hash_verified"] for record in records)
        and manifest_ok
    )
    attestation = {
        "timestamp": utc_now(),
        "status": (
            "VERIFIED_CURRENT_SOURCE_LOCAL_ARTIFACTS_WITH_EXTERNAL_PLATFORM_GAPS"
            if passed
            else "NOT_VERIFIED_CURRENT_SOURCE_ARTIFACTS"
        ),
        "application_commit": source_commit,
        "final_report_commit": source_commit,
        "release_gate": release_path,
        "release_gate_output_sha256": release_log_hash,
        "release_source_commit": release_source,
        "release_manifest": release_manifest_path,
        "release_manifest_sha256": (
            sha256_file(Path(release_manifest_path))
            if isinstance(release_manifest_path, str)
            else None
        ),
        "git_refs": {
            "HEAD": git.get("commit"),
            "application_source_commit": git_source,
            "upstream": git.get("upstream"),
        },
        "working_tree_clean": git_clean,
        "staging_clean": git_clean,
        "artifact_hashes_verified": artifact_hashes,
        "artifacts": records,
        "external_platform_gaps": [
            {
                "platform": "desktop-gui",
                "status": "BLOCKED_EXTERNAL",
                "reason": "Headless release validation cannot replace the separately required native-window acceptance evidence.",
            },
            {
                "platform": "android-native",
                "status": "BLOCKED_EXTERNAL",
                "reason": "No supported Android SDK/device capability is currently configured.",
            },
        ],
        "objective_pass": passed,
    }
    return attestation, passed


def discover_latest_artifact_attestation(
    evidence_root: Path, configured_path: str | None
) -> tuple[dict[str, Any], str | None]:
    candidates: list[Path] = []
    if isinstance(configured_path, str):
        candidates.append(Path(configured_path))
    for pattern in ("**/artifact-attestation*.json", "**/final-release-attestation.json"):
        try:
            candidates.extend(evidence_root.glob(pattern))
        except OSError:
            pass
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return {}, None
    path = max(existing, key=lambda item: item.stat().st_mtime_ns)
    value = read_json(path, {})
    return (value if isinstance(value, dict) else {}), str(path)


def _provenance_status(
    *,
    structural_valid: bool,
    evidence_commit: Any,
    current_commit: str | None,
    alternate_commits: tuple[str, ...] = (),
) -> str:
    if not structural_valid:
        return "FAIL"
    accepted = {value for value in (current_commit, *alternate_commits) if value}
    if not isinstance(evidence_commit, str) or evidence_commit not in accepted:
        return "HISTORICAL_PASS_REQUIRES_CURRENT_PROVENANCE"
    return "PASS"


def validate_current_provenance(
    evidence_root: Path,
    observations: dict[str, Any],
    status: dict[str, Any],
    current_commit: str | None,
) -> dict[str, Any]:
    """Validate evidence content and identity without trusting file presence."""
    report_tip_commit = observations.get("git", {}).get("commit")
    alternate_commits = report_only_commit_aliases(report_tip_commit, current_commit)
    benchmark_observation = observations.get("benchmark", {})
    benchmark_path = benchmark_observation.get("path")
    benchmark_summary = read_json(Path(benchmark_path), {}) if isinstance(benchmark_path, str) else {}
    benchmark_results_path = (
        Path(benchmark_path).with_name("benchmark-results.json")
        if isinstance(benchmark_path, str)
        else None
    )
    benchmark_results = read_json(benchmark_results_path, {}) if benchmark_results_path else {}
    rows = benchmark_results.get("results") if isinstance(benchmark_results, dict) else None
    counts: dict[str, int] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("result") in {"PASS", "PARTIAL", "FAIL"}:
                result = str(row["result"])
                counts[result] = counts.get(result, 0) + 1
    reported_counts = benchmark_summary.get("counts") if isinstance(benchmark_summary, dict) else None
    normalized_reported_counts: dict[str, int] | None = None
    if isinstance(reported_counts, dict):
        try:
            normalized_reported_counts = {
                key: int(reported_counts.get(key, 0))
                for key in ("PASS", "PARTIAL", "FAIL")
            }
        except (TypeError, ValueError):
            normalized_reported_counts = None
    benchmark_structural = (
        isinstance(benchmark_summary, dict)
        and benchmark_summary.get("status") == "BENCHMARK COMPLETE"
        and isinstance(rows, list)
        and normalized_reported_counts is not None
        and len(rows) == benchmark_summary.get("number_of_tests")
        and normalized_reported_counts == {
            key: counts.get(key, 0) for key in ("PASS", "PARTIAL", "FAIL")
        }
    )
    benchmark_commit = benchmark_summary.get("git_commit") if isinstance(benchmark_summary, dict) else None
    benchmark_status = _provenance_status(
        structural_valid=benchmark_structural,
        evidence_commit=benchmark_commit,
        current_commit=current_commit,
        alternate_commits=alternate_commits,
    )

    runtime_observation = observations.get("runtime", {})
    runtime_path = runtime_observation.get("evidence")
    runtime_document = read_json(Path(runtime_path), {}) if isinstance(runtime_path, str) else {}
    runtime_payload = runtime_document.get("runtime") if isinstance(runtime_document, dict) else None
    if not isinstance(runtime_payload, dict):
        runtime_payload = runtime_document if isinstance(runtime_document, dict) else {}
    expected_backend = tree_digest(
        REPOSITORY_ROOT / "backend" / "app", suffixes=frozenset({".py", ".json"})
    )
    expected_web = tree_digest(REPOSITORY_ROOT / "frontend" / "dist")
    recorded_runtime_commit = runtime_payload.get("source_commit")
    recorded_backend = runtime_payload.get("backend_source_sha256")
    recorded_web = runtime_payload.get("web_bundle_sha256")
    stored_matches = runtime_document.get("matches") if isinstance(runtime_document, dict) else {}
    runtime_structural = (
        isinstance(recorded_runtime_commit, str)
        and bool(COMMIT_PATTERN.fullmatch(recorded_runtime_commit))
        and isinstance(recorded_backend, str)
        and bool(SHA256_PATTERN.fullmatch(recorded_backend))
        and isinstance(recorded_web, str)
        and bool(SHA256_PATTERN.fullmatch(recorded_web))
        and expected_backend is not None
        and expected_web is not None
    )
    runtime_content_matches = {
        "source_commit": recorded_runtime_commit == current_commit,
        "backend_source_sha256": recorded_backend == expected_backend,
        "web_bundle_sha256": recorded_web == expected_web,
        "declared_matches": isinstance(stored_matches, dict) and bool(stored_matches) and all(value is True for value in stored_matches.values()),
    }
    runtime_status = _provenance_status(
        structural_valid=runtime_structural
        and runtime_content_matches["backend_source_sha256"]
        and runtime_content_matches["web_bundle_sha256"]
        and runtime_content_matches["declared_matches"],
        evidence_commit=recorded_runtime_commit,
        current_commit=current_commit,
        alternate_commits=alternate_commits,
    )

    release_document, release_path = discover_latest_release_gate(evidence_root)
    release_log = release_document.get("output_path") if isinstance(release_document, dict) else None
    release_log_hash = sha256_file(Path(release_log)) if isinstance(release_log, str) else None
    release_structural = (
        isinstance(release_document, dict)
        and release_document.get("exit_code") == 0
        and str(release_document.get("status", "")).startswith("PASS")
        and isinstance(release_log, str)
        and Path(release_log).is_file()
        and isinstance(release_document.get("output_sha256"), str)
        and release_document.get("output_sha256") == release_log_hash
    )
    release_commit = release_document.get("commit") if isinstance(release_document, dict) else None
    release_status = _provenance_status(
        structural_valid=release_structural,
        evidence_commit=release_commit,
        current_commit=current_commit,
        alternate_commits=alternate_commits,
    )
    security_check = release_document.get("checks", {}).get("security") if isinstance(release_document.get("checks"), dict) else None
    security_status = release_status if isinstance(security_check, str) and security_check.startswith("PASS") else "FAIL"

    configured_attestation_path = None
    configured_release = status.get("release", {})
    if isinstance(configured_release, dict) and isinstance(configured_release.get("final_report_tip_attestation"), str):
        configured_attestation_path = configured_release["final_report_tip_attestation"]
    attestation, attestation_path = discover_latest_artifact_attestation(
        evidence_root, configured_attestation_path
    )
    artifact_hashes = attestation.get("artifact_hashes_verified") if isinstance(attestation, dict) else None
    artifact_records = attestation.get("artifacts") if isinstance(attestation, dict) else None
    validated_artifacts: list[dict[str, Any]] = []
    if isinstance(artifact_records, list):
        for record in artifact_records:
            if not isinstance(record, dict):
                continue
            artifact_path = record.get("path")
            expected_hash = record.get("sha256")
            actual_hash = None
            path_is_safe = (
                isinstance(artifact_path, str)
                and bool(Path(artifact_path).is_absolute())
                and not Path(artifact_path).is_symlink()
                and Path(artifact_path).is_file()
            )
            if path_is_safe:
                actual_hash = sha256_file(Path(artifact_path))
            validated_artifacts.append(
                {
                    "artifact": record.get("artifact"),
                    "path": artifact_path,
                    "expected_sha256": expected_hash,
                    "actual_sha256": actual_hash,
                    "exists": path_is_safe,
                    "hash_verified": (
                        record.get("hash_verified") is True
                        and isinstance(expected_hash, str)
                        and bool(SHA256_PATTERN.fullmatch(expected_hash))
                        and actual_hash == expected_hash
                    ),
                }
            )
    artifact_records_valid = bool(validated_artifacts) and all(
        item["exists"] and item["hash_verified"] for item in validated_artifacts
    )
    artifact_structural = (
        isinstance(artifact_hashes, dict)
        and bool(artifact_hashes)
        and all(value is True for value in artifact_hashes.values())
        and (artifact_records_valid if artifact_records is not None else True)
    )
    artifact_commit = (
        attestation.get("final_report_commit")
        if isinstance(attestation, dict)
        else None
    )
    if not artifact_commit and isinstance(attestation, dict):
        artifact_commit = attestation.get("commit") or attestation.get("source_commit")
    artifact_status = _provenance_status(
        structural_valid=artifact_structural,
        evidence_commit=artifact_commit,
        current_commit=current_commit,
        alternate_commits=alternate_commits,
    )

    component_statuses = {
        "source_commit": "PASS" if isinstance(current_commit, str) and bool(COMMIT_PATTERN.fullmatch(current_commit)) else "FAIL",
        "runtime": runtime_status,
        "backend_hash": "PASS" if runtime_content_matches["backend_source_sha256"] else "FAIL",
        "web_artifact_hash": "PASS" if runtime_content_matches["web_bundle_sha256"] else "FAIL",
        "benchmark": benchmark_status,
        "security": security_status,
        "release": release_status,
        "artifact": artifact_status,
    }
    return {
        "status": "PASS" if all(value == "PASS" for value in component_statuses.values()) else "NOT_PROVEN",
        "current_source_commit": current_commit,
        "components": component_statuses,
        "runtime": {
            "evidence": runtime_path,
            "recorded_source_commit": recorded_runtime_commit,
            "recorded_backend_source_sha256": recorded_backend,
            "recorded_web_bundle_sha256": recorded_web,
            "expected_backend_source_sha256": expected_backend,
            "expected_web_bundle_sha256": expected_web,
            "content_matches": runtime_content_matches,
        },
        "benchmark": {
            "evidence": benchmark_path,
            "results": str(benchmark_results_path) if benchmark_results_path else None,
            "recorded_source_commit": benchmark_commit,
            "summary_sha256": sha256_file(Path(benchmark_path)) if isinstance(benchmark_path, str) else None,
            "results_sha256": sha256_file(benchmark_results_path) if benchmark_results_path else None,
            "structural_valid": benchmark_structural,
            "counts": normalized_reported_counts,
        },
        "release": {
            "evidence": release_path,
            "recorded_source_commit": release_commit,
            "log": release_log,
            "log_sha256": release_log_hash,
            "structural_valid": release_structural,
            "security_check": security_check,
        },
        "artifact": {
            "evidence": attestation_path,
            "recorded_source_commit": artifact_commit,
            "hashes_verified": artifact_hashes,
            "structural_valid": artifact_structural,
            "artifacts": validated_artifacts,
        },
        "historical_evidence_rejected": any(
            value == "HISTORICAL_PASS_REQUIRES_CURRENT_PROVENANCE"
            for value in component_statuses.values()
        ),
    }


def discover_runtime(
    evidence_root: Path,
    status: dict[str, Any],
    current_commit: str | None,
    report_tip_commit: str | None = None,
) -> dict[str, Any]:
    candidates: list[Path] = []
    configured = status.get("current_runtime")
    if isinstance(configured, str):
        candidates.append(Path(configured))
    try:
        candidates.extend(evidence_root.glob("**/runtime-identity*.json"))
    except OSError:
        pass
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return {"status": "UNAVAILABLE", "evidence": None}
    path = max(existing, key=lambda item: item.stat().st_mtime_ns)
    value = read_json(path, {})
    if not isinstance(value, dict):
        return {"status": "INVALID", "evidence": str(path)}
    matches = value.get("matches", {})
    identity_ok = bool(matches) and all(matches.values())
    recorded_source = (
        value.get("runtime", {}).get("source_commit")
        if isinstance(value.get("runtime"), dict)
        else value.get("source_commit")
    )
    accepted_source_commits = {
        value
        for value in (
            current_commit,
            *report_only_commit_aliases(report_tip_commit, current_commit),
        )
        if value
    }
    source_matches = bool(recorded_source and recorded_source in accepted_source_commits)
    if not identity_ok:
        runtime_status = "FAIL"
    elif not source_matches:
        runtime_status = "HISTORICAL_PASS_REQUIRES_CURRENT_ATTESTATION"
    else:
        runtime_status = "PASS"
    return {
        "status": runtime_status,
        "evidence": str(path),
        "source_commit": recorded_source,
        "current_commit": current_commit,
        "source_matches_current": source_matches,
        "matches": matches,
    }


def parse_test_metrics(evidence_root: Path, status: dict[str, Any]) -> dict[str, Any]:
    logs: list[Path] = []
    try:
        logs = list(evidence_root.glob("**/full-regression.log"))
    except OSError:
        pass
    result: dict[str, Any] = {}
    if logs:
        path = max(logs, key=lambda item: item.stat().st_mtime_ns)
        text = path.read_text(encoding="utf-8", errors="replace")
        backend = re.search(r"(\d+) passed, (\d+) skipped.*in .*s", text)
        web = re.search(r"Test Files .*?\n\s+Tests\s+(\d+) passed", text)
        mobile_matches = list(re.finditer(r"Test Files .*?\n\s+Tests\s+(\d+) passed", text))
        result = {
            "evidence": str(path),
            "backend": {
                "passed": int(backend.group(1)),
                "skipped": int(backend.group(2)),
            }
            if backend
            else {},
            "web": {"passed": int(web.group(1))} if web else {},
            "mobile": {"passed": int(mobile_matches[-1].group(1))} if len(mobile_matches) > 1 else {},
        }
    fallback = status.get("acceptance", {})
    result.setdefault("backend", status.get("tests", {}).get("backend", {}))
    result.setdefault("web", status.get("tests", {}).get("web", {}))
    result.setdefault("mobile", status.get("tests", {}).get("mobile", {}))
    result["status"] = "PASS" if result.get("backend", {}).get("passed") and not result.get("backend", {}).get("failed") else "UNKNOWN"
    result["acceptance_hint"] = fallback.get("full_regression") if isinstance(fallback, dict) else None
    return result


def observe(evidence_root: Path) -> dict[str, Any]:
    status = current_status()
    queue = current_queue()
    benchmark, benchmark_path = discover_latest_benchmark(evidence_root, queue)
    git = git_snapshot()
    synchronize_queue_benchmark(
        queue,
        {
            "benchmark": {"path": benchmark_path, "commit": benchmark.get("git_commit")},
            "git": git,
        },
    )
    # Queue synchronization is itself persisted state. Re-read Git after it
    # so the observation cannot claim CLEAN while a newly selected benchmark
    # pointer is still an uncommitted tracked change.
    git = git_snapshot()
    counts = benchmark.get("counts", {}) if isinstance(benchmark.get("counts"), dict) else {}
    source_commit = git.get("application_source_commit") or git.get("commit")
    runtime = discover_runtime(
        evidence_root,
        status,
        source_commit,
        git.get("commit"),
    )
    health = command_record(
        ["curl", "--fail", "--silent", "--show-error", "--max-time", "3", "http://127.0.0.1:8000/api/v1/health/live"],
        REPOSITORY_ROOT,
        timeout=5,
    )
    runtime["health_command"] = health
    observations = {
        "timestamp": utc_now(),
        "git": git,
        "benchmark": {
            "path": benchmark_path,
            "commit": benchmark.get("git_commit"),
            "score": benchmark.get("total_score"),
            "pass": counts.get("PASS"),
            "partial": counts.get("PARTIAL"),
            "fail": counts.get("FAIL"),
            "mean": benchmark.get("average_latency_seconds"),
            "p95": benchmark.get("p95_latency_seconds"),
        },
        "runtime": runtime,
        "tests": parse_test_metrics(evidence_root, status),
        "queue": {
            "total": len(queue.get("issues", [])),
            "unresolved": len(unresolved_issues(queue)),
            "local_unresolved": len([
                issue for issue in unresolved_issues(queue)
                if issue.get("status") != "BLOCKED_EXTERNAL"
            ]),
        },
    }
    observations["provenance"] = validate_current_provenance(
        evidence_root, observations, status, source_commit
    )
    return observations


def classify_queue(queue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(issue.get("id")): {
            "classification": issue_classification(issue),
            "priority": issue_priority(issue),
            "queue_status": issue.get("status"),
        }
        for issue in queue.get("issues", [])
        if isinstance(issue, dict) and issue.get("id")
    }


def readiness(queue: dict[str, Any], observations: dict[str, Any]) -> tuple[bool, str]:
    unresolved = unresolved_issues(queue)
    benchmark = observations.get("benchmark", {})
    if unresolved:
        return False, "UNRESOLVED_LOCAL_OR_REVIEW_ITEMS"
    if benchmark.get("fail", 0) or benchmark.get("partial", 0):
        return False, "CANONICAL_NONPASS_REMAINS"
    if observations.get("provenance", {}).get("status") != "PASS":
        return False, "CURRENT_EVIDENCE_PROVENANCE_NOT_PROVEN"
    if observations.get("runtime", {}).get("status") != "PASS":
        return False, "RUNTIME_IDENTITY_NOT_PROVEN"
    if observations.get("git", {}).get("status") != "CLEAN_SYNCED":
        return False, "GIT_NOT_CLEAN_AND_SYNCHRONIZED"
    return True, "ALL_REQUIRED_LOCAL_GATES_PROVEN"


def canonical_nonpass_remains(observations: dict[str, Any]) -> bool:
    benchmark = observations.get("benchmark", {})
    try:
        return int(benchmark.get("fail", 0) or 0) > 0 or int(benchmark.get("partial", 0) or 0) > 0
    except (TypeError, ValueError):
        return True


def terminal_queue_requires_external_watch(
    state: dict[str, Any], queue: dict[str, Any], observations: dict[str, Any]
) -> bool:
    """Keep a concrete, unresolved acceptance opportunity alive after queue drain."""
    ready, _ = readiness(queue, observations)
    return (
        choose_issue(queue) is None
        and not ready
        and canonical_nonpass_remains(observations)
        and bool(str(state.get("next_prompt", "") or "").strip())
    )


def _safe_model_reference(reference: str) -> bool:
    # Ollama quantized tags conventionally contain uppercase tokens such as
    # q4_K_M. Keep the reference grammar strict while accepting those valid
    # tags; path separators, whitespace, and shell metacharacters remain
    # invalid.
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*:[A-Za-z0-9][A-Za-z0-9._-]*", reference))


def _model_registry_parts(reference: str) -> tuple[str, str] | None:
    if not _safe_model_reference(reference):
        return None
    repository, tag = reference.split(":", 1)
    return repository, tag


def parse_ollama_model_names(stdout: str) -> list[str]:
    names: list[str] = []
    for line in stdout.splitlines():
        value = line.strip()
        if not value or value.upper().startswith("NAME"):
            continue
        name = value.split()[0]
        if _safe_model_reference(name):
            names.append(name)
    return names


def ollama_models_root() -> Path:
    configured = os.environ.get("OLLAMA_MODELS", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path("/usr/share/ollama/.ollama/models")


def _manifest_blob_verification(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    descriptors: list[tuple[str, Any]] = [("config", manifest.get("config"))]
    descriptors.extend(("layer", layer) for layer in manifest.get("layers", []))
    records: list[dict[str, Any]] = []
    for kind, descriptor in descriptors:
        if not isinstance(descriptor, dict):
            records.append(
                {
                    "kind": kind,
                    "descriptor_valid": False,
                    "verified": False,
                }
            )
            continue
        digest = descriptor.get("digest")
        size = descriptor.get("size")
        digest_valid = bool(
            isinstance(digest, str)
            and digest.startswith("sha256:")
            and SHA256_PATTERN.fullmatch(digest.split(":", 1)[-1])
        )
        size_valid = isinstance(size, int) and not isinstance(size, bool) and size >= 0
        path = (
            ollama_models_root() / "blobs" / digest.replace(":", "-", 1)
            if digest_valid
            else None
        )
        actual_sha256 = sha256_file(path) if path and path.is_file() else None
        actual_size = path.stat().st_size if path and path.is_file() else None
        expected_sha256 = digest.split(":", 1)[-1] if digest_valid else None
        verified = bool(
            digest_valid
            and size_valid
            and actual_sha256 == expected_sha256
            and actual_size == size
        )
        records.append(
            {
                "kind": kind,
                "media_type": descriptor.get("mediaType"),
                "digest": digest,
                "expected_sha256": expected_sha256,
                "actual_sha256": actual_sha256,
                "expected_size_bytes": size,
                "actual_size_bytes": actual_size,
                "blob_path": str(path) if path else None,
                "descriptor_valid": digest_valid and size_valid,
                "verified": verified,
            }
        )
    return records


def local_ollama_candidate(reference: str) -> dict[str, Any]:
    """Verify a local Ollama candidate without trusting list output alone."""
    listed = command_record(["ollama", "list"], REPOSITORY_ROOT, timeout=10)
    names = parse_ollama_model_names(str(listed.get("stdout", "")))
    listed_exact = reference in names
    show = command_record(["ollama", "show", reference], REPOSITORY_ROOT, timeout=10) if listed_exact else {}
    parts = _model_registry_parts(reference)
    manifest_path = None
    manifest: dict[str, Any] = {}
    if parts:
        manifest_path = ollama_models_root() / "manifests" / "registry.ollama.ai" / "library" / parts[0] / parts[1]
        value = read_json(manifest_path, {})
        manifest = value if isinstance(value, dict) else {}
    model_layer = next(
        (
            layer
            for layer in manifest.get("layers", [])
            if isinstance(layer, dict) and layer.get("mediaType") == "application/vnd.ollama.image.model"
        ),
        None,
    )
    blob_path = None
    blob_hash = None
    expected_hash = None
    if isinstance(model_layer, dict) and isinstance(model_layer.get("digest"), str):
        expected_hash = model_layer["digest"].replace(":", "-")
        blob_path = ollama_models_root() / "blobs" / expected_hash
        if blob_path.is_file():
            blob_hash = sha256_file(blob_path)
    artifact_verification = _manifest_blob_verification(manifest) if manifest else []
    config_records = [item for item in artifact_verification if item.get("kind") == "config"]
    layer_records = [item for item in artifact_verification if item.get("kind") == "layer"]
    metadata_ok = bool(
        listed_exact
        and show.get("exit_code") == 0
        and manifest.get("schemaVersion") == 2
        and isinstance(model_layer, dict)
        and len(config_records) == 1
        and layer_records
    )
    config_integrity_ok = len(config_records) == 1 and config_records[0].get("verified") is True
    all_layer_integrity_ok = bool(layer_records) and all(
        item.get("verified") is True for item in layer_records
    )
    integrity_ok = config_integrity_ok and all_layer_integrity_ok
    return {
        "reference": reference,
        "list": listed,
        "listed_exact": listed_exact,
        "show": show,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "manifest_sha256": sha256_file(manifest_path) if manifest_path and manifest_path.is_file() else None,
        "model_layer_bytes": model_layer.get("size") if isinstance(model_layer, dict) else None,
        "model_layer_digest": model_layer.get("digest") if isinstance(model_layer, dict) else None,
        "blob_path": str(blob_path) if blob_path else None,
        "blob_sha256": blob_hash,
        "manifest_artifacts": artifact_verification,
        "metadata_verified": metadata_ok,
        "config_integrity_verified": config_integrity_ok,
        "all_layer_integrity_verified": all_layer_integrity_ok,
        "blob_integrity_verified": bool(
            blob_path
            and blob_path.is_file()
            and blob_hash == str(model_layer.get("digest", "")).split(":", 1)[-1]
        ),
        "all_manifest_artifacts_verified": integrity_ok,
        "available": metadata_ok and integrity_ok,
    }


def remote_model_manifest(reference: str) -> dict[str, Any]:
    parts = _model_registry_parts(reference)
    if not parts:
        return {"reference": reference, "status": "INVALID_REFERENCE"}
    repository, tag = parts
    url = f"https://registry.ollama.ai/v2/library/{repository}/manifests/{tag}"
    result = command_record(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--max-time", "10", url],
        REPOSITORY_ROOT,
        timeout=15,
    )
    payload = read_json_from_text(str(result.get("stdout", "")), {})
    model_layer = next(
        (
            layer
            for layer in payload.get("layers", [])
            if isinstance(layer, dict) and layer.get("mediaType") == "application/vnd.ollama.image.model"
        ),
        None,
    ) if isinstance(payload, dict) else None
    total_blob_bytes = None
    descriptors = (
        [payload.get("config")] + list(payload.get("layers", []))
        if isinstance(payload, dict) and isinstance(payload.get("layers"), list)
        else []
    )
    descriptor_sizes = [
        item.get("size") for item in descriptors if isinstance(item, dict)
    ]
    if (
        descriptors
        and len(descriptor_sizes) == len(descriptors)
        and all(
            isinstance(size, int) and not isinstance(size, bool) and size >= 0
            for size in descriptor_sizes
        )
    ):
        total_blob_bytes = sum(descriptor_sizes)
    return {
        "reference": reference,
        "url": url,
        "command": result,
        "manifest": payload if isinstance(payload, dict) else {},
        "model_layer_bytes": model_layer.get("size") if isinstance(model_layer, dict) else None,
        "model_layer_digest": model_layer.get("digest") if isinstance(model_layer, dict) else None,
        "total_blob_bytes": total_blob_bytes,
        "status": "AVAILABLE" if result.get("exit_code") == 0 and isinstance(model_layer, dict) else "UNAVAILABLE",
    }


def read_json_from_text(value: str, fallback: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def remote_range_probe(manifest: dict[str, Any]) -> dict[str, Any]:
    started_at = utc_now()
    digest = manifest.get("model_layer_digest")
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or not SHA256_PATTERN.fullmatch(digest.split(":", 1)[-1])
    ):
        return {"status": "UNAVAILABLE", "reason": "manifest has no model layer digest"}
    repository, tag = _model_registry_parts(str(manifest.get("reference", ""))) or ("", "")
    if not repository or not tag:
        return {"status": "UNAVAILABLE", "reason": "invalid model reference"}
    url = f"https://registry.ollama.ai/v2/library/{repository}/blobs/{digest}"
    result = command_record(
        [
            "curl", "--fail", "--silent", "--show-error", "--location", "--max-time", "15",
            "--range", f"0-{WATCH_RANGE_BYTES - 1}", "-o", "/dev/null",
            "-w", "%{http_code}\\t%{size_download}\\t%{time_total}\\t%{speed_download}\\n", url,
        ],
        REPOSITORY_ROOT,
        timeout=20,
    )
    raw = str(result.get("stdout", "")).strip().split("\t")
    try:
        http_code = int(raw[0])
        bytes_downloaded = int(float(raw[1]))
        duration = float(raw[2])
        throughput = float(raw[3])
    except (IndexError, TypeError, ValueError):
        return {"status": "UNAVAILABLE", "command": result, "reason": "range probe output invalid"}
    size = manifest.get("total_blob_bytes") or manifest.get("model_layer_bytes")
    estimated_hours = float(size) / throughput / 3600 if isinstance(size, (int, float)) and throughput > 0 else None
    return {
        "status": "AVAILABLE" if result.get("exit_code") == 0 and http_code in {200, 206} else "UNAVAILABLE",
        "command": result,
        "http_code": http_code,
        "bytes_downloaded": bytes_downloaded,
        "duration_seconds": duration,
        "throughput_bytes_per_second": throughput,
        "estimated_download_seconds": estimated_hours * 3600 if estimated_hours is not None else None,
        "estimated_download_hours": estimated_hours,
        "url": url,
        "fresh_probe_started_at": started_at,
        "sha256_addressed": True,
    }


def watch_candidate_references(state: dict[str, Any]) -> list[str]:
    del state
    references = list(HOST_CONTROLLER_CANDIDATES)
    assert references == list(HOST_CONTROLLER_CANDIDATES)
    return references


def discover_watch_candidates(
    state: dict[str, Any], *, max_estimated_hours: float,
    max_download_seconds: int = DEFAULT_MAX_DOWNLOAD_SECONDS,
) -> dict[str, Any]:
    all_references = watch_candidate_references(state)
    previous_watch = state.get("external_watch") if isinstance(state.get("external_watch"), dict) else {}
    rejected_candidates = {
        str(reference)
        for reference in previous_watch.get("rejected_candidates", [])
        if isinstance(reference, str) and _safe_model_reference(reference)
    }
    acquisition_blocked_candidates = {
        str(reference)
        for reference in previous_watch.get("acquisition_blocked_candidates", [])
        if isinstance(reference, str) and _safe_model_reference(reference)
    }
    excluded_candidates = rejected_candidates | acquisition_blocked_candidates
    references = [reference for reference in all_references if reference not in excluded_candidates]
    local: list[dict[str, Any]] = []
    remote: list[dict[str, Any]] = []
    for reference in references:
        local_result = local_ollama_candidate(reference)
        local.append(local_result)
        manifest = remote_model_manifest(reference)
        probe = remote_range_probe(manifest) if manifest.get("status") == "AVAILABLE" else {}
        estimated_hours = probe.get("estimated_download_hours")
        estimated_seconds = probe.get("estimated_download_seconds")
        qualifies = bool(
            manifest.get("status") == "AVAILABLE"
            and probe.get("status") == "AVAILABLE"
            and isinstance(estimated_hours, (int, float))
            and estimated_hours <= max_estimated_hours
            and isinstance(estimated_seconds, (int, float))
            and estimated_seconds <= max_download_seconds
        )
        record = {"manifest": manifest, "probe": probe}
        record["qualification"] = {
            "max_estimated_hours": max_estimated_hours,
            "max_download_seconds": max_download_seconds,
            "estimate_within_hours_limit": bool(
                isinstance(estimated_hours, (int, float))
                and estimated_hours <= max_estimated_hours
            ),
            "estimate_within_pull_limit": bool(
                isinstance(estimated_seconds, (int, float))
                and estimated_seconds <= max_download_seconds
            ),
            "qualifies": qualifies,
        }
        remote.append(record)
    policy = {
        "candidate_references": references,
        "all_candidate_references": all_references,
        "constructed_exactly": all_references == list(HOST_CONTROLLER_CANDIDATES),
        "rejected_references": sorted(rejected_candidates),
        "acquisition_blocked_references": sorted(acquisition_blocked_candidates),
        "excluded_references": sorted(excluded_candidates),
        "excluded_reference": next(iter(sorted(excluded_candidates)), None),
        "excluded_reference_absent": not excluded_candidates,
    }
    download_policy = {
        "max_estimated_hours": max_estimated_hours,
        "max_download_seconds": max_download_seconds,
        "both_limits_required": True,
    }
    local_candidate = next(
        (item["reference"] for item in local if item.get("available") is True),
        None,
    )
    if local_candidate:
        return {
            "status": "CANDIDATE_AVAILABLE",
            "candidate": local_candidate,
            "candidate_state": "AVAILABLE_LOCAL_INTEGRITY_VERIFIED",
            "candidate_policy": policy,
            "local": local,
            "remote": remote,
            "download_policy": download_policy,
        }
    eligible = next(
        (
            item["manifest"]["reference"]
            for item in remote
            if item.get("qualification", {}).get("qualifies") is True
        ),
        None,
    )
    if eligible:
        return {
            "status": "DOWNLOAD_ELIGIBLE",
            "candidate": eligible,
            "candidate_state": "NOT_CACHED_DOWNLOAD_ELIGIBLE",
            "candidate_policy": policy,
            "local": local,
            "remote": remote,
            "download_policy": download_policy,
        }
    return {
        "status": "NOT_READY",
        "candidate": references[0] if references else None,
        "candidate_state": "NOT_CACHED_DOWNLOAD_TOO_SLOW_OR_UNAVAILABLE",
        "candidate_policy": policy,
        "local": local,
        "remote": remote,
        "download_policy": download_policy,
    }


CATALOG_CANDIDATE_ADMISSION_SCRIPT = r"""
import asyncio
import json
import os

import httpx

from app.ai.catalog import ModelCatalog, public_model_id
from app.hardware import detect_hardware
from app.runtimes.ollama import OllamaModelDiscoveryRuntime


async def main() -> None:
    reference = os.environ["ASTER_CANDIDATE_REFERENCE"]
    origin = os.environ.get("OLLAMA_ORIGIN", "http://127.0.0.1:11434")
    async with httpx.AsyncClient(base_url=origin, timeout=15.0) as client:
        runtime = OllamaModelDiscoveryRuntime(client, (reference,))
        catalog = ModelCatalog((runtime,), hardware_inventory=detect_hardware())
        model_id = public_model_id("ollama-local", reference)
        models = await catalog.list_models()
        model = next((item for item in models if item.model_id == model_id), None)
        if model is None:
            print(json.dumps({"candidate_reference": reference, "model_id": model_id, "found": False}))
            return
        value = lambda item: item.value if hasattr(item, "value") else item
        print(
            json.dumps(
                {
                    "candidate_reference": reference,
                    "model_id": model.model_id,
                    "found": True,
                    "installed": model.installed,
                    "verified": model.verified,
                    "runnable_now": model.runnable_now,
                    "availability": value(model.availability),
                    "eligibility_status": value(model.eligibility_status),
                    "eligibility_reasons": [value(item) for item in model.eligibility_reasons],
                    "performance_class": value(model.performance_class),
                    "hardware_class": value(model.hardware_class),
                    "required_vram_bytes": model.required_vram_bytes,
                    "required_ram_bytes": model.required_ram_bytes,
                    "capabilities": [value(item) for item in model.capabilities],
                },
                sort_keys=True,
            )
        )


asyncio.run(main())
""".strip()


def catalog_candidate_admission(reference: str) -> dict[str, Any]:
    if not _safe_model_reference(reference):
        return {"status": "INVALID_REFERENCE", "admitted": False}
    virtualenv_python = REPOSITORY_ROOT / "backend" / ".venv" / "bin" / "python"
    executable = virtualenv_python if virtualenv_python.is_file() else Path(sys.executable)
    environment = os.environ.copy()
    environment["ASTER_CANDIDATE_REFERENCE"] = reference
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(executable), "-c", CATALOG_CANDIDATE_ADMISSION_SCRIPT],
            cwd=str(REPOSITORY_ROOT / "backend"),
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        command = {
            "command": [str(executable), "-c", "<existing catalog admission script>"],
            "cwd": str(REPOSITORY_ROOT / "backend"),
            "exit_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 4),
            "stdout": safe_text(stdout),
            "stderr": safe_text(stderr),
            "stdout_sha256": sha256_bytes(stdout.encode()),
            "stderr_sha256": sha256_bytes(stderr.encode()),
            "timed_out": False,
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "status": "UNAVAILABLE",
            "admitted": False,
            "error": safe_text(error),
            "duration_seconds": round(time.monotonic() - started, 4),
        }
    payload = read_json_from_text(stdout.strip(), {})
    admitted = bool(
        completed.returncode == 0
        and isinstance(payload, dict)
        and payload.get("candidate_reference") == reference
        and payload.get("found") is True
        and payload.get("installed") is True
        and payload.get("verified") is True
        and payload.get("runnable_now") is True
        and payload.get("eligibility_status") == "runnable_now"
    )
    return {
        "status": "ADMITTED" if admitted else "REJECTED",
        "admitted": admitted,
        "catalog_record": payload if isinstance(payload, dict) else {},
        "command": command,
        "production_configuration_modified": False,
        "production_routing_modified": False,
    }


ASTER_FOCUSED_TASKS = (
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
)


def trusted_parent_focused_gate(
    evidence_dir: Path,
    candidate: str,
    source_commit: str | None,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run objective candidate verification outside the restricted Codex child.

    The existing PostgreSQL integration harness creates the disposable backend
    and configures a candidate-only catalog.  The child remains an inspection
    and handoff process; this trusted parent subprocess owns runtime evidence.
    """
    output = evidence_dir / "focused-candidate-gate.json"
    environment = os.environ.copy()
    environment.update(
        {
            "ASTER_FOCUSED_CANDIDATE": candidate,
            "ASTER_FOCUSED_OUTPUT": str(output),
            "ASTER_APPLICATION_SOURCE_COMMIT": source_commit or "unknown",
            "OLLAMA_LOCAL_MODEL_ALLOWLIST": json.dumps(
                [candidate, "nomic-embed-text:latest"], separators=(",", ":")
            ),
            "OLLAMA_TASK_MODEL_PREFERENCES": json.dumps(
                {task: candidate for task in ASTER_FOCUSED_TASKS},
                separators=(",", ":"),
            ),
            "OLLAMA_EMBEDDING_MODEL": "nomic-embed-text:latest",
        }
    )
    command = [
        str(REPOSITORY_ROOT / "scripts" / "postgres_integration_check.sh"),
        "--aster-focused-candidate-gate",
    ]
    result = command_record(
        command,
        REPOSITORY_ROOT,
        timeout=min(max(int(timeout_seconds), 1), 900),
        environment=environment,
    )
    report = read_json(output, {})
    report_summary = (
        {
            key: report.get(key)
            for key in (
                "run_status",
                "candidate_reference",
                "candidate_model_id",
                "source_commit",
                "matrix_complete",
                "objective_pass_count",
                "all_eight_objective_pass",
                "initialization_error",
            )
        }
        if isinstance(report, dict)
        else {}
    )
    return {
        "command": result,
        "output": str(output),
        "output_sha256": sha256_file(output) if output.is_file() else None,
        "report_summary": report_summary,
        "run_status": report.get("run_status") if isinstance(report, dict) else None,
        "objective_pass_count": (
            report.get("objective_pass_count") if isinstance(report, dict) else None
        ),
        "all_eight_objective_pass": (
            report.get("all_eight_objective_pass") is True
            if isinstance(report, dict)
            else False
        ),
        "trusted_parent": True,
        "child_runtime_access_required": False,
        "production_routing_modified": False,
    }


def focused_objective_gate(evidence_dir: Path, candidate: str) -> dict[str, Any]:
    path = evidence_dir / "focused-candidate-gate.json"
    value = read_json(path, {})
    records = value.get("records", []) if isinstance(value, dict) else []
    expected_pairs = {
        (route, test_id)
        for route in FOCUSED_ADMISSION_ROUTES
        for test_id in FOCUSED_ADMISSION_TESTS
    }
    observed_pairs = [
        (str(item.get("route")), str(item.get("test_id")))
        for item in records
        if isinstance(item, dict)
    ] if isinstance(records, list) else []
    objective_values = [
        item.get("objective_pass")
        for item in records
        if isinstance(item, dict)
    ] if isinstance(records, list) else []
    exact_matrix = bool(
        len(observed_pairs) == 8
        and len(set(observed_pairs)) == 8
        and set(observed_pairs) == expected_pairs
    )
    all_objective_pass = bool(
        exact_matrix
        and len(objective_values) == 8
        and all(item is True for item in objective_values)
    )
    admitted = bool(
        isinstance(value, dict)
        and value.get("run_status") == "complete"
        and value.get("candidate_reference") == candidate
        and value.get("matrix_complete") is True
        and all_objective_pass
    )
    return {
        "status": "PASS" if admitted else "FAIL",
        "admitted": admitted,
        "evidence": str(path),
        "evidence_sha256": sha256_file(path) if path.is_file() else None,
        "record_count": len(observed_pairs),
        "unique_record_count": len(set(observed_pairs)),
        "exact_eight_record_matrix": exact_matrix,
        "objective_pass_values": objective_values,
        "objective_pass_count": sum(item is True for item in objective_values),
        "all_eight_objective_pass": all_objective_pass,
        "candidate_matches": isinstance(value, dict) and value.get("candidate_reference") == candidate,
    }


def _candidate_focus_prompt(prompt: Any) -> bool:
    return isinstance(prompt, str) and prompt.startswith("ASTER CANDIDATE ADMISSION — ")


def build_external_watch_prompt(candidate: str | None, observations: dict[str, Any], evidence: str) -> str:
    candidate_label = candidate or "NONE (all configured candidates excluded or unavailable)"
    return f"""ASTER EXTERNAL GAP WATCH — bounded model acquisition

Candidate priority: {candidate_label}
Repository: {REPOSITORY_ROOT}
Current source commit: {observations.get('git', {}).get('application_source_commit') or observations.get('git', {}).get('commit', 'unknown')}
Evidence: {evidence}

The local issue queue is terminal, but readiness is false because the unchanged canonical benchmark still has non-pass coder cases. Wait for a complete, integrity-verified candidate; do not claim readiness from queue emptiness.

Required next action when a candidate is locally available:
1. Verify its Ollama manifest, model blob SHA-256, metadata, hardware admission, and runtime stability.
2. Run both unchanged coder cases through explicit candidate, generic coding, generic code-generation, and task-aware routes.
3. Accept only objective PASS for both cases, then run genericity, unchanged 459-case benchmark, security, provenance, release, artifact, and Git gates.

Download policy: bounded only; never start a pull when the measured estimate exceeds the configured limit. Preserve production routing and fail closed on incomplete or ambiguous evidence.
"""


def build_candidate_focus_prompt(candidate: str, observations: dict[str, Any], evidence: str) -> str:
    return f"""ASTER CANDIDATE ADMISSION — {candidate}

This is one bounded child task in the existing ASTER controller.
Repository: {REPOSITORY_ROOT}
Current source commit: {observations.get('git', {}).get('application_source_commit') or observations.get('git', {}).get('commit', 'unknown')}
Candidate evidence: {evidence}

Use the existing model catalog, hardware admission, routing, Ollama runtime, Agent OS, and benchmark harness. Do not create another routing layer.

Run BOTH unchanged canonical coder cases through the explicit candidate, generic coding route, generic code-generation route, and automatic task-aware route:
- medium-coding-04: Write a Python list comprehension producing squares of 0 through 4. Expression only. Objective requirements: x * x and range(5).
- model-comparison-coder-06: A recursive function has no terminating condition. What is the standard recursion term for the missing condition? Name it only. Objective answer: base case.

The trusted parent controller executes the runtime gate in the existing disposable PostgreSQL/backend boundary. Inspect the resulting focused-candidate-gate.json and its raw-output files; do not attempt runtime commands from this child. Capture and review selected model, raw output, SHA-256, latency, configuration, hardware telemetry, source commit, and objective checks. Do not edit prompts, expectations, checkers, semantic verification, or normalize output. Do not admit this candidate or claim readiness from prose alone.

Only if both objective cases PASS may the parent proceed to genericity and full canonical validation. End with the standard ASTER_LOOP_RESULT block and an exact next prompt.
"""


def prompt_source_commit(prompt: str) -> str | None:
    match = re.search(
        r"(?:Required|Current) (?:application )?source commit(?: at selection)?:\s*`?([0-9a-f]{40,64})",
        prompt,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def observed_source_commit(observations: dict[str, Any]) -> str | None:
    git = observations.get("git", {})
    return git.get("application_source_commit") or git.get("commit")


def apply_child_handoff(
    state: dict[str, Any],
    parsed: dict[str, Any],
    fallback_prompt: str,
    *,
    source_commit: str | None = None,
) -> None:
    next_prompt = parsed.get("next_prompt", "") if parsed.get("parsed") else ""
    state["child_suggested_next_prompt"] = next_prompt
    state["next_prompt"] = next_prompt or fallback_prompt
    state["next_prompt_source"] = "child_structured_result" if next_prompt else "controller_derived"
    if source_commit:
        state["next_prompt_source_commit"] = source_commit
    state["controller_phase"] = "CANDIDATE_FOCUSED_GATE" if next_prompt else "WATCH_EXTERNAL_GAP"


def update_queue_observation(
    queue: dict[str, Any], issue_id: str | None, iteration: int, result: dict[str, Any], evidence_dir: Path
) -> None:
    if not issue_id:
        return
    for issue in queue.get("issues", []):
        if isinstance(issue, dict) and issue.get("id") == issue_id:
            issue.setdefault("controller_observations", []).append(
                {
                    "timestamp": utc_now(),
                    "iteration": iteration,
                    "classification": result.get("classification"),
                    "child_status": result.get("status"),
                    "verification": result.get("verification"),
                    "action": result.get("action"),
                    "evidence_dir": str(evidence_dir),
                    "queue_status_preserved": True,
                }
            )
            issue["controller_observations"] = issue["controller_observations"][-20:]
            break
    queue["updated_at"] = utc_now()
    write_json(QUEUE_JSON, queue)


def synchronize_queue_benchmark(
    queue: dict[str, Any], observations: dict[str, Any]
) -> None:
    """Keep the queue's benchmark pointer aligned with the latest valid report."""
    benchmark = observations.get("benchmark", {})
    benchmark_path = benchmark.get("path")
    commit = benchmark.get("commit") or observations.get("git", {}).get("application_source_commit")
    if not isinstance(benchmark_path, str) or not benchmark_path:
        return
    changed = False
    if queue.get("current_benchmark") != benchmark_path:
        queue["current_benchmark"] = benchmark_path
        changed = True
    if isinstance(commit, str) and queue.get("current_application_commit") != commit:
        queue["current_application_commit"] = commit
        changed = True
    results_path = Path(benchmark_path).with_name("benchmark-results.json")
    results = read_json(results_path, {})
    if isinstance(results, dict) and isinstance(results.get("results"), list):
        non_pass = sorted(
            str(item.get("test_id"))
            for item in results["results"]
            if isinstance(item, dict)
            and item.get("result") != "PASS"
            and item.get("test_id")
        )
        if queue.get("current_nonpass_ids") != non_pass:
            queue["current_nonpass_ids"] = non_pass
            changed = True
    if changed:
        queue["updated_at"] = utc_now()
        write_json(QUEUE_JSON, queue)


def render_reports(
    state: dict[str, Any], queue: dict[str, Any], observations: dict[str, Any], *, evidence_root: Path
) -> dict[str, Any]:
    synchronize_queue_benchmark(queue, observations)
    selected = choose_issue(queue)
    selected_task = choose_acceptance_task(state) if selected is None else None
    ready, readiness_reason = readiness(queue, observations)
    local_unresolved = [
        issue for issue in unresolved_issues(queue) if issue.get("status") != "BLOCKED_EXTERNAL"
    ]
    external_blocked = [
        issue for issue in queue.get("issues", [])
        if isinstance(issue, dict) and issue.get("status") == "BLOCKED_EXTERNAL"
    ]
    fixed = [
        issue for issue in queue.get("issues", [])
        if isinstance(issue, dict) and issue.get("status") in {"VERIFIED", "CLOSED"}
    ]
    benchmark = observations.get("benchmark", {})
    provenance = observations.get("provenance", {})
    tests = observations.get("tests", {})
    git = observations.get("git", {})
    runtime = observations.get("runtime", {})
    artifact_evidence_path = provenance.get("artifact", {}).get("evidence") if isinstance(provenance.get("artifact"), dict) else None
    if provenance.get("status") == "PASS" and isinstance(git.get("application_source_commit"), str):
        state["last_successful_commit"] = git["application_source_commit"]
    last_result = state.get("last_result") or {}
    last_child = last_result.get("child") if isinstance(last_result, dict) else None
    if isinstance(last_child, dict) and isinstance(last_child.get("codex_resolution"), dict):
        state["last_codex_cli"] = last_child["codex_resolution"]
    # The report is generated from this observed source commit. A later
    # report-only commit is intentionally represented separately by the Git
    # history; this prevents self-referential commit claims.
    state["report_tip_commit"] = git.get("commit")
    current_issue = selected.get("id") if selected else (selected_task.get("id") if selected_task else None)
    watch = state.get("external_watch") if isinstance(state.get("external_watch"), dict) else {}
    watch_action = watch.get("current_action") if isinstance(watch.get("current_action"), str) else None
    current_action = (
        required_action(selected)
        if selected
        else (str(selected_task.get("action")) if selected_task else (watch_action or "Run final comprehensive validation and release gate."))
    )
    remaining_internal_ids = [
        str(issue.get("id"))
        for issue in queue.get("issues", [])
        if isinstance(issue, dict)
        and issue.get("status") not in {"VERIFIED", "CLOSED", "BLOCKED_EXTERNAL"}
        and issue.get("id")
    ]
    state.update(
        {
            "overall_ready": ready,
            "status": "READY" if ready else "NOT_READY",
            "readiness_reason": readiness_reason,
            "current_issue": current_issue,
            "current_action": current_action,
            "controller_phase": state.get("controller_phase", "RUNNING"),
            "observations": observations,
            "issue_workflow": classify_queue(queue),
            "issues_remaining": len(local_unresolved),
            "issues_fixed": len(fixed),
            "issues_blocked_externally": len(external_blocked),
            "next_prompt": state.get("next_prompt")
            or (
                build_prompt(selected, observations)
                if selected
                else (
                    build_acceptance_task_prompt(selected_task, observations, str(evidence_root))
                    if selected_task
                    else ""
                )
            ),
            "acceptance_tasks": state.get("acceptance_tasks", {}),
        }
    )
    persist_state(state, evidence_root)

    status = current_status()
    status.update(
        {
            "schema_version": max(4, int(status.get("schema_version", 0) or 0)),
            "iteration": state.get("iteration", 0),
            "updated_at": utc_now(),
            "status": "READY" if ready else "NOT_READY",
            "overall_ready": ready,
            "current_issue": [current_issue] if current_issue else [],
            "current_commit": git.get("commit"),
            "application_source_commit": git.get("application_source_commit"),
            "current_runtime": runtime.get("evidence"),
            "current_artifact": artifact_evidence_path,
            "latest_canonical": benchmark.get("path"),
            "remaining_internal_issues": remaining_internal_ids,
            "evidence_root": str(evidence_root),
            "issue_queue": str(QUEUE_JSON.relative_to(REPOSITORY_ROOT)),
            "controller_state": str(evidence_root / "current_state.json"),
            "report_generated_from_commit": git.get("commit"),
            "report_tip_commit": state.get("report_tip_commit") or git.get("commit"),
            "evidence_provenance": provenance,
            "controller": {
                "iteration": state.get("iteration", 0),
                "iterations_completed": state.get("iterations_completed", 0),
                "phase": state.get("controller_phase", "RUNNING"),
                "readiness_reason": readiness_reason,
                "current_action": state.get("current_action"),
                "last_successful_commit": state.get("last_successful_commit"),
                "last_codex_cli": state.get("last_codex_cli"),
                "last_result": last_result,
                "external_watch": state.get("external_watch", {}),
                "acceptance_tasks": state.get("acceptance_tasks", {}),
            },
            "issues": queue.get("issues", []),
            "issue_counts": {
                "remaining_local": len(local_unresolved),
                "fixed": len(fixed),
                "blocked_external": len(external_blocked),
                "total": len(queue.get("issues", [])),
            },
        }
    )
    write_json(STATUS_JSON, status, mode=0o644)

    status_lines = [
        "# ASTER / Personal AI OS master-student status",
        "",
        f"Iteration {state.get('iteration', 0)}. Overall readiness is **{'TRUE' if ready else 'NOT READY'}**.",
        "",
        f"Current application source commit: `{git.get('application_source_commit') or git.get('commit', 'unknown')}`. Report tip commit: `{git.get('commit', 'unknown')}`. Evidence root: `{evidence_root}`.",
        "",
        "## Current measured state",
        "",
        f"- Canonical benchmark: **{benchmark.get('score', 'unknown')}/100**, {benchmark.get('pass', '?')} PASS, {benchmark.get('partial', '?')} PARTIAL, {benchmark.get('fail', '?')} FAIL; mean {benchmark.get('mean', '?')}s, P95 {benchmark.get('p95', '?')}s.",
        f"- Issues: {len(local_unresolved)} locally unresolved, {len(fixed)} fixed/verified, {len(external_blocked)} externally blocked.",
        f"- Current issue/action: `{current_issue or 'NONE'}` — {state.get('current_action')}",
        f"- Controller phase: **{state.get('controller_phase', 'RUNNING')}**.",
        f"- Runtime identity: **{runtime.get('status', 'UNKNOWN')}**; evidence `{runtime.get('evidence', 'none')}`.",
        f"- Git: **{git.get('status', 'UNKNOWN')}** at `{git.get('commit', 'unknown')}`.",
        "",
        "## Queue classifications",
        "",
        "| Issue | Queue status | Workflow | Priority |",
        "| --- | --- | --- | --- |",
    ]
    for issue in queue.get("issues", []):
        if not isinstance(issue, dict):
            continue
        status_lines.append(
            f"| {issue.get('id')} | {issue.get('status')} | {issue_classification(issue)} | {issue_priority(issue)} |"
        )
    status_lines += [
        "",
        "## Acceptance task lane",
        "",
        "| Task | Status | Dependencies | Blocker |",
        "| --- | --- | --- | --- |",
    ]
    for task in state.get("acceptance_tasks", {}).values():
        if not isinstance(task, dict):
            continue
        status_lines.append(
            f"| {task.get('id')} | {task.get('status')} | {', '.join(str(item) for item in task.get('dependencies', [])) or 'none'} | {task.get('blocker') or task.get('external_blocker') or ''} |"
        )
    status_lines += [
        "",
        "The issue queue and machine-readable controller state are authoritative. A child model response cannot mark an issue complete without objective evidence.",
        "",
        f"Next prompt is persisted at `{evidence_root / 'current_state.json'}` and is generated from the selected issue and observed evidence.",
        "",
    ]
    atomic_write(STATUS_MD, "\n".join(status_lines), mode=0o644)

    voice_issue = next(
        (
            issue
            for issue in queue.get("issues", [])
            if isinstance(issue, dict) and issue.get("id") == "ASTER-028"
        ),
        None,
    )
    if isinstance(voice_issue, dict) and voice_issue.get("status") == "BLOCKED_EXTERNAL":
        voice_status = "BLOCKED_EXTERNAL: ASTER-028 installed voice-model/acoustic variability is preserved with exact-WAV evidence."
    elif isinstance(voice_issue, dict) and voice_issue.get("status") not in {"VERIFIED", "CLOSED"}:
        voice_status = "PARTIAL: ASTER-028 preserves reproducible exact-WAV lexical variability."
    else:
        voice_status = str(status.get("acceptance", {}).get("voice", "UNKNOWN"))

    progress = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source": {
            "state": str(evidence_root / "current_state.json"),
            "status": str(STATUS_JSON),
            "queue": str(QUEUE_JSON),
            "observations": observations,
        },
        "overall_readiness": "TRUE" if ready else "NOT READY",
        "benchmark_score": benchmark.get("score"),
        "pass": benchmark.get("pass"),
        "partial": benchmark.get("partial"),
        "fail": benchmark.get("fail"),
        "iterations_completed": state.get("iterations_completed", 0),
        "issues_remaining": len(local_unresolved),
        "issues_fixed": len(fixed),
        "issues_blocked_externally": len(external_blocked),
        "current_issue": current_issue,
        "current_action": state.get("current_action"),
        "controller_phase": state.get("controller_phase", "RUNNING"),
        "external_watch": state.get("external_watch", {}),
        "last_verification": last_result.get("verification") or "NONE",
        "backend_tests": tests.get("backend", {}),
        "web_tests": tests.get("web", {}),
        "mobile_tests": tests.get("mobile", {}),
        "security_status": "PASS_WITH_RECORDED_RISK" if status.get("security", {}).get("npm_audit_findings") == 0 else "RECORDED",
        "runtime_identity_status": runtime.get("status"),
        "evidence_provenance": provenance.get("status", "NOT_PROVEN"),
        "benchmark_provenance": provenance.get("components", {}).get("benchmark", "NOT_PROVEN"),
        "artifact_provenance": provenance.get("components", {}).get("artifact", "NOT_PROVEN"),
        "release_provenance": provenance.get("components", {}).get("release", "NOT_PROVEN"),
        "aster_to_ai_os": str(status.get("acceptance", {}).get("master_student", "UNKNOWN")),
        "ai_os_to_aster": "BLOCKED_EXTERNAL: parent MCP/reverse callback is not available; no bidirectional verification claimed.",
        "dex": str(status.get("acceptance", {}).get("dex", "UNKNOWN")),
        "voice": voice_status,
        "performance_mean": benchmark.get("mean"),
        "performance_p95": benchmark.get("p95"),
        "git_status": git.get("status"),
        "current_commit": git.get("commit"),
        "application_source_commit": git.get("application_source_commit"),
        "last_successful_commit": state.get("last_successful_commit"),
        "codex_cli": state.get("last_codex_cli"),
        "report_tip_commit": state.get("report_tip_commit"),
        "last_artifact_verification": artifact_evidence_path,
        "last_failure": state.get("last_failure"),
        "next_action": state.get("current_action"),
        "current_task": current_issue if selected_task else None,
        "acceptance_tasks": state.get("acceptance_tasks", {}),
        "readiness_reason": readiness_reason,
    }
    write_json(PROGRESS_JSON, progress, mode=0o644)

    terminal = "\n".join(
        [
            "========================================================",
            "ASTER / PERSONAL AI OS AUTONOMOUS STATUS",
            "========================================================",
            f"READINESS      : {'READY' if ready else 'NOT READY'}",
            f"BENCHMARK      : {benchmark.get('score', 'unknown')} / 100",
            f"PASS           : {benchmark.get('pass', '?')}",
            f"PARTIAL        : {benchmark.get('partial', '?')}",
            f"FAIL           : {benchmark.get('fail', '?')}",
            f"ISSUES         : {len(local_unresolved)}",
            f"CURRENT        : {current_issue or 'NONE'}",
            f"PHASE          : {state.get('controller_phase', 'RUNNING')}",
            f"CANDIDATE      : {watch.get('candidate', 'NONE')}",
            f"ASTER→AI OS    : {progress['aster_to_ai_os'][:90]}",
            f"AI OS→ASTER    : {progress['ai_os_to_aster'][:90]}",
            f"DEX            : {progress['dex'][:90]}",
            f"VOICE          : {progress['voice'][:90]}",
            f"SECURITY       : {progress['security_status']}",
            f"PROVENANCE     : {progress['evidence_provenance']}",
            f"BACKEND        : {tests.get('backend', {}).get('passed', '?')} PASS",
            f"WEB            : {tests.get('web', {}).get('passed', '?')} PASS",
            f"MOBILE         : {tests.get('mobile', {}).get('passed', '?')} PASS",
            f"PERFORMANCE    : mean {benchmark.get('mean', '?')}s / P95 {benchmark.get('p95', '?')}s",
            f"GIT            : {git.get('status', 'UNKNOWN')}",
            f"COMMIT         : {git.get('commit', 'unknown')}",
            f"SOURCE         : {git.get('application_source_commit') or git.get('commit', 'unknown')}",
            f"NEXT ACTION    : {state.get('current_action')}",
            "========================================================",
        ]
    )
    atomic_write(PROGRESS_MD, terminal + "\n", mode=0o644)
    return progress


RESULT_HEADER = re.compile(r"ASTER_LOOP_RESULT\s*\n(?P<body>.*?)(?:\nNEXT_PROMPT_BEGIN\n|\Z)", re.DOTALL)


def parse_result_text(text: str) -> dict[str, Any]:
    match = RESULT_HEADER.search(text)
    if not match:
        return {
            "parsed": False,
            "status": "FAILED",
            "verification": "FAIL",
            "classification": "FAILED",
            "reason": "ASTER_LOOP_RESULT block missing",
            "next_prompt": "",
        }
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    prompt_match = re.search(r"\nNEXT_PROMPT_BEGIN\n(?P<prompt>.*?)\nNEXT_PROMPT_END", text, re.DOTALL)
    status = fields.get("STATUS", "FAILED").upper()
    verification = fields.get("VERIFICATION", "FAIL").upper()
    if status not in {"READY", "NOT_READY", "BLOCKED", "WAITING", "FAILED"}:
        status = "FAILED"
    if verification not in {"PASS", "FAIL", "PARTIAL", "BLOCKED"}:
        verification = "FAIL"
    return {
        "parsed": True,
        "status": status,
        "issues_remaining": fields.get("ISSUES_REMAINING"),
        "current_issue": fields.get("CURRENT_ISSUE"),
        "action": fields.get("ACTION", ""),
        "verification": verification,
        "classification": "BLOCKED_EXTERNAL" if status in {"BLOCKED", "WAITING"} else status,
        "next_prompt": prompt_match.group("prompt").strip() if prompt_match else "",
    }


def terminate_process_group(process: subprocess.Popen[Any], grace_seconds: float = 10) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=grace_seconds)


def run_codex_child(prompt: str, iteration_dir: Path, *, timeout_seconds: int, evidence_root: Path) -> dict[str, Any]:
    iteration_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = iteration_dir / "prompt.txt"
    stdout_path = iteration_dir / "codex.stdout.jsonl"
    stderr_path = iteration_dir / "codex.stderr.log"
    last_message_path = iteration_dir / "codex.last-message.txt"
    atomic_write(prompt_path, prompt, mode=0o600)
    try:
        codex_cli = resolve_codex_cli()
    except RuntimeError as error:
        atomic_write(stdout_path, "", mode=0o600)
        atomic_write(stderr_path, f"{type(error).__name__}: {error}\n", mode=0o600)
        return {
            "command": [],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 127,
            "duration_seconds": 0,
            "timed_out": False,
            "error": str(error),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "stderr_tail": safe_text(str(error)),
            "last_message_path": str(last_message_path),
            "stdout_sha256": sha256_file(stdout_path),
            "stderr_sha256": sha256_file(stderr_path),
            "last_message_sha256": None,
            "codex_resolution": {"status": "FAILED", "error": str(error)},
            "parsed": parse_result_text(""),
        }
    command = [
        codex_cli["path"],
        "exec",
        "--json",
        "--color",
        "never",
        "--sandbox",
        "workspace-write",
        "--config",
        'approval_policy="never"',
        "--cd",
        str(REPOSITORY_ROOT),
        "--add-dir",
        str(evidence_root),
        "--output-last-message",
        str(last_message_path),
        "-",
    ]
    started = time.monotonic()
    return_code: int | None = None
    timed_out = False
    error: str | None = None
    try:
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr, prompt_path.open("r", encoding="utf-8") as stdin:
            process = subprocess.Popen(
                command,
                cwd=str(REPOSITORY_ROOT),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                close_fds=True,
            )
            try:
                return_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_process_group(process)
                return_code = 124
    except OSError as exc:
        return_code = 127
        error = f"{type(exc).__name__}: {exc}"
    duration = round(time.monotonic() - started, 4)
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    last_message = last_message_path.read_text(encoding="utf-8", errors="replace") if last_message_path.exists() else ""
    parse_source = last_message if last_message.strip() else stdout
    parsed = parse_result_text(parse_source)
    return {
        "command": command,
        "cwd": str(REPOSITORY_ROOT),
        "exit_code": return_code,
        "duration_seconds": duration,
        "timed_out": timed_out,
        "error": error,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_tail": safe_text(stderr[-2000:]),
        "last_message_path": str(last_message_path),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "last_message_sha256": sha256_file(last_message_path),
        "codex_executable": codex_cli["path"],
        "codex_version": codex_cli["version"],
        "codex_resolution": codex_cli,
        "parsed": parsed,
    }


def changed_paths() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        value = line[3:]
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        paths.append(value)
    return paths


def safe_commit_if_validated(result: dict[str, Any], iteration: int) -> dict[str, Any]:
    verification = result.get("parsed", {}).get("verification")
    exit_code = result.get("exit_code")
    paths = changed_paths()
    if exit_code != 0 or verification != "PASS":
        return {"attempted": False, "reason": "child_not_zero_and_verified", "paths": paths}
    allowed_prefixes = ("backend/", "frontend/", "shared/", "apps/", "scripts/", "reports/", "docs/")
    forbidden_fragments = ("/.env", ".pem", ".key", ".p12", ".pfx", "node_modules/", "/dist/", "__pycache__/")
    unsafe = [
        path for path in paths
        if not path.startswith(allowed_prefixes) or any(fragment in path for fragment in forbidden_fragments)
    ]
    if unsafe:
        return {"attempted": False, "reason": "unsafe_or_unexpected_changed_path", "paths": paths, "unsafe": unsafe}
    check = command_record(["git", "diff", "--check"], REPOSITORY_ROOT, timeout=30)
    if check.get("exit_code") != 0 or not paths:
        return {"attempted": False, "reason": "diff_check_failed_or_no_paths", "paths": paths, "diff_check": check}
    subprocess.run(["git", "add", "--", *paths], cwd=str(REPOSITORY_ROOT), check=True)
    staged_check = command_record(["git", "diff", "--cached", "--check"], REPOSITORY_ROOT, timeout=30)
    if staged_check.get("exit_code") != 0:
        subprocess.run(["git", "reset", "--", *paths], cwd=str(REPOSITORY_ROOT), check=False)
        return {"attempted": False, "reason": "staged_diff_check_failed", "paths": paths, "diff_check": staged_check}
    commit = subprocess.run(
        ["git", "commit", "-m", f"ASTER autonomous iteration {iteration}: validated change"],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if commit.returncode != 0:
        return {"attempted": True, "committed": False, "paths": paths, "exit_code": commit.returncode, "stderr": safe_text(commit.stderr)}
    push = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "attempted": True,
        "committed": True,
        "commit": git_snapshot().get("commit"),
        "paths": paths,
        "push": {
            "exit_code": push.returncode,
            "stdout": safe_text(push.stdout),
            "stderr": safe_text(push.stderr),
            "status": "PUSHED" if push.returncode == 0 else "PUSH_BLOCKED_EXTERNAL",
        },
    }


def safe_commit_parent_repair(repair_kind: str, *, evidence_dir: Path) -> dict[str, Any]:
    """Commit only the narrowly expected files from a trusted parent repair."""
    paths = changed_paths()
    allowed = {"apps/mobile/package.json", "package-lock.json"}
    unexpected = [path for path in paths if path not in allowed]
    if unexpected or not paths:
        return {
            "attempted": False,
            "committed": False,
            "reason": "unexpected_or_missing_repair_paths",
            "repair_kind": repair_kind,
            "paths": paths,
            "unexpected": unexpected,
        }
    check = command_record(["git", "diff", "--check"], REPOSITORY_ROOT, timeout=30, output_directory=evidence_dir / "git-diff-check")
    if check.get("exit_code") != 0:
        return {
            "attempted": False,
            "committed": False,
            "reason": "repair_diff_check_failed",
            "repair_kind": repair_kind,
            "paths": paths,
            "diff_check": check,
        }
    subprocess.run(["git", "add", "--", *paths], cwd=str(REPOSITORY_ROOT), check=True)
    staged_check = command_record(
        ["git", "diff", "--cached", "--check"],
        REPOSITORY_ROOT,
        timeout=30,
        output_directory=evidence_dir / "git-staged-diff-check",
    )
    if staged_check.get("exit_code") != 0:
        subprocess.run(["git", "reset", "--", *paths], cwd=str(REPOSITORY_ROOT), check=False)
        return {
            "attempted": True,
            "committed": False,
            "reason": "repair_staged_diff_check_failed",
            "repair_kind": repair_kind,
            "paths": paths,
            "diff_check": staged_check,
        }
    commit = subprocess.run(
        ["git", "commit", "-m", "ASTER repair: align Expo SDK mobile dependencies"],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if commit.returncode != 0:
        return {
            "attempted": True,
            "committed": False,
            "reason": "repair_commit_failed",
            "repair_kind": repair_kind,
            "paths": paths,
            "exit_code": commit.returncode,
            "stdout": safe_text(commit.stdout),
            "stderr": safe_text(commit.stderr),
        }
    push = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "attempted": True,
        "committed": True,
        "repair_kind": repair_kind,
        "paths": paths,
        "commit": git_snapshot().get("commit"),
        "push": {
            "exit_code": push.returncode,
            "stdout": safe_text(push.stdout),
            "stderr": safe_text(push.stderr),
            "status": "PUSHED" if push.returncode == 0 else "PUSH_BLOCKED_EXTERNAL",
        },
    }


WATCH_REPORT_PATHS = frozenset(
    {
        "reports/ASTER_AI_OS_PROGRESS.json",
        "reports/ASTER_AI_OS_PROGRESS.md",
        "reports/ASTER_AI_OS_MASTER_STUDENT_STATUS.json",
        "reports/ASTER_AI_OS_MASTER_STUDENT_STATUS.md",
        "reports/ASTER_AI_OS_ISSUE_QUEUE.json",
    }
)


def persist_watch_report_commit(cycle: int) -> dict[str, Any]:
    """Commit only controller-owned report changes made by a watch cycle.

    A continuous watcher must not leave its tracked dashboard dirty, but it
    must also never absorb an unrelated user/source change.  The allowlist is
    deliberately narrower than the normal validated-child commit policy.
    """
    paths = changed_paths()
    unexpected = [path for path in paths if path not in WATCH_REPORT_PATHS]
    if unexpected:
        return {
            "attempted": False,
            "reason": "unexpected_changes_refused",
            "paths": paths,
            "unexpected": unexpected,
        }
    report_paths = [path for path in paths if path in WATCH_REPORT_PATHS]
    if not report_paths:
        return {"attempted": False, "reason": "no_watch_report_changes", "paths": paths}

    check = command_record(["git", "diff", "--check"], REPOSITORY_ROOT, timeout=30)
    if check.get("exit_code") != 0:
        return {
            "attempted": False,
            "reason": "report_diff_check_failed",
            "paths": report_paths,
            "diff_check": check,
        }
    subprocess.run(["git", "add", "--", *report_paths], cwd=str(REPOSITORY_ROOT), check=True)
    staged_check = command_record(["git", "diff", "--cached", "--check"], REPOSITORY_ROOT, timeout=30)
    if staged_check.get("exit_code") != 0:
        subprocess.run(["git", "reset", "--", *report_paths], cwd=str(REPOSITORY_ROOT), check=False)
        return {
            "attempted": False,
            "reason": "staged_report_diff_check_failed",
            "paths": report_paths,
            "diff_check": staged_check,
        }
    commit = subprocess.run(
        ["git", "commit", "-m", f"Persist ASTER external watch cycle {cycle:04d}"],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if commit.returncode != 0:
        return {
            "attempted": True,
            "committed": False,
            "paths": report_paths,
            "exit_code": commit.returncode,
            "stderr": safe_text(commit.stderr),
        }
    push = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    snapshot = git_snapshot()
    return {
        "attempted": True,
        "committed": True,
        "commit": snapshot.get("commit"),
        "paths": report_paths,
        "push": {
            "exit_code": push.returncode,
            "stdout": safe_text(push.stdout),
            "stderr": safe_text(push.stderr),
            "status": "PUSHED" if push.returncode == 0 else "PUSH_BLOCKED_EXTERNAL",
        },
        "git": snapshot,
    }


def mark_terminal_queue_state(state: dict[str, Any]) -> None:
    """Record a genuine controller terminal observation with no watch gap."""
    state["in_progress"] = False
    state["resume_required"] = False
    state["child_suggested_next_prompt"] = ""
    state["controller_phase"] = "FINAL_VALIDATION"
    state["next_prompt"] = "Run final comprehensive validation and release gate; stop only after all evidence passes."


def _bounded_sleep(seconds: float) -> None:
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        interval = min(remaining, 60.0)
        time.sleep(interval)
        remaining -= interval


def diagnose_acceptance_failure(
    task: dict[str, Any], *, evidence_dir: Path, evidence_root: Path
) -> dict[str, Any]:
    """Collect a bounded, parent-side diagnosis for an objective failure.

    Diagnosis tasks are deliberately evidence-only.  They may inspect the
    current failure bundle and harmless local runtime state, but they never
    execute a child-provided command or turn a model claim into a repair.
    This gives the existing repair lane a real trusted executor while keeping
    unresolved product failures visible.
    """
    context = task.get("failure_context")
    context = context if isinstance(context, dict) else {}
    current_root = context.get("evidence_dir")
    current_root_path = Path(current_root) if isinstance(current_root, str) else None
    evidence_paths: list[Path] = []
    for raw in (
        context.get("parent_stdout_path"),
        context.get("parent_stderr_path"),
    ):
        if isinstance(raw, str):
            evidence_paths.append(Path(raw))
    if current_root_path is not None:
        evidence_paths.extend(
            current_root_path / name
            for name in ("command.stdout.log", "command.stderr.log")
        )

    evidence_root_resolved = evidence_root.resolve()
    linked_logs: list[dict[str, Any]] = []
    log_text: list[str] = []
    for path in dict.fromkeys(evidence_paths):
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(evidence_root_resolved) or not resolved.is_file():
                continue
            content = resolved.read_text(encoding="utf-8", errors="replace")[:2_000_000]
        except (OSError, RuntimeError):
            continue
        linked_logs.append(
            {
                "path": str(resolved),
                "sha256": sha256_file(resolved),
                "bytes": resolved.stat().st_size,
            }
        )
        log_text.append(content)

    combined = "\n".join(log_text)
    patterns = (
        ("native_window", re.compile(r"(?:production-binary|appimage) did not open its expected native window|lost its owned visible native window")),
        ("command_not_found", re.compile(r"(?:command not found|No such file or directory)")),
        ("sandbox_network", re.compile(r"RTM_NEWADDR|Operation not permitted|permission denied", re.IGNORECASE)),
        ("test_failure", re.compile(r"(?:FAILED|failed|ERROR|error:)")),
    )
    matched_kind = None
    first_failure = None
    for kind, pattern in patterns:
        match = pattern.search(combined)
        if match:
            matched_kind = kind
            first_failure = match.group(0)
            break

    probes: list[dict[str, Any]] = []
    if matched_kind == "native_window":
        for command in (
            ["ps", "-eo", "pid,ppid,pgid,stat,etime,cmd"],
            ["xwininfo", "-root", "-tree"],
            ["busctl", "--user", "status", "com.workstation.personalai.SingleInstance"],
        ):
            if shutil.which(command[0]) is None:
                probes.append({"command": command, "status": "NOT_INSTALLED"})
                continue
            probes.append(
                command_record(
                    command,
                    REPOSITORY_ROOT,
                    timeout=15,
                    output_directory=evidence_dir / "diagnosis" / command[0],
                )
            )

    process_output = ""
    for probe in probes:
        if probe.get("command", [None])[0] == "ps" and isinstance(probe.get("stdout_path"), str):
            try:
                process_output = Path(probe["stdout_path"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    existing_desktop_process = bool(
        re.search(r"(?:^|\s)work-station-desktop(?:\s|$)", process_output)
    )
    tested_executable_match = re.search(
        r"desktop launch executable:\s*(?P<command>.+)", combined
    )
    tested_executable = (
        tested_executable_match.group("command").strip()
        if tested_executable_match
        else None
    )
    ipc_text = "\n".join(
        str(probe.get("stdout", ""))
        for probe in probes
        if probe.get("command", [None])[0] == "busctl"
    )
    explicit_single_instance_rejection = bool(
        re.search(
            r"NameTaken|single.?instance.*(?:owned|already|rejected)|already running",
            combined,
            re.IGNORECASE,
        )
    )
    causal_native_conflict = bool(
        matched_kind == "native_window"
        and tested_executable
        and explicit_single_instance_rejection
        and ipc_text
    )

    if matched_kind == "native_window":
        classification = (
            "LOCAL_DESKTOP_NATIVE_LAUNCH_CONFLICT"
            if causal_native_conflict
            else "SUSPECTED_DESKTOP_NATIVE_LAUNCH_CONFLICT"
            if existing_desktop_process
            else "LOCAL_DESKTOP_NATIVE_LAUNCH_FAILED"
        )
        reason = (
            "release native-window smoke has causal single-instance evidence"
            if causal_native_conflict
            else "release native-window smoke has only a suspected existing-instance conflict; causal IPC evidence is missing"
            if existing_desktop_process
            else "release native-window smoke failed without an existing desktop instance"
        )
        repair_action = (
            "Run the desktop smoke in the supported isolated D-Bus/X11 launch context and require the tested artifact's own visible window; do not terminate the user's process."
        )
    elif matched_kind == "command_not_found":
        classification = "LOCAL_EXECUTABLE_OR_ENVIRONMENT_FAILURE"
        reason = "release evidence contains a missing executable or path failure"
        repair_action = "Restore the required executable through the existing runtime environment and rerun the focused check."
    elif matched_kind == "sandbox_network":
        classification = "EXTERNAL_SANDBOX_RESTRICTION_REQUIRES_CONFIRMATION"
        reason = "release evidence contains a host permission or sandbox restriction"
        repair_action = "Re-run only when the supported host capability is available; preserve fail-closed behavior."
    elif matched_kind == "test_failure":
        classification = "LOCAL_TEST_OR_RUNTIME_FAILURE"
        reason = "release evidence contains an objective test/runtime failure"
        repair_action = "Trace the first failing test or command and apply the smallest verified local correction."
    else:
        classification = "LOCAL_FAILURE_ROOT_CAUSE_UNCONFIRMED"
        reason = "current failure evidence does not identify a supported root cause"
        repair_action = "Collect a more specific bounded diagnostic before attempting a repair."

    diagnosis = {
        "timestamp": utc_now(),
        "task_id": str(task.get("id")),
        "parent_task_id": task.get("parent_task_id"),
        "evidence_scope": "current_failure_bundle_only",
        "linked_logs": linked_logs,
        "matched_failure_kind": matched_kind,
        "first_failure_marker": first_failure,
        "probes": probes,
        "existing_desktop_process": existing_desktop_process,
        "tested_executable": tested_executable,
        "explicit_single_instance_rejection": explicit_single_instance_rejection,
        "causal_native_conflict": causal_native_conflict,
        "failure_classification": classification,
        "reason": reason,
        "repair_action": repair_action,
        "objective_root_cause": matched_kind is not None and (
            matched_kind != "native_window" or causal_native_conflict
        ),
    }
    diagnosis_path = evidence_dir / "diagnosis.json"
    write_json(diagnosis_path, diagnosis)
    diagnosis["evidence_path"] = str(diagnosis_path)
    return diagnosis


def _watch_action(candidate: str | None, candidate_state: str) -> str:
    if not candidate:
        return (
            "No eligible coding candidate is currently available; preserve all "
            "exclusions and continue the bounded external watch."
        )
    if candidate_state in {
        "AVAILABLE_LOCAL",
        "AVAILABLE_LOCAL_INTEGRITY_VERIFIED",
        "INSTALLED_RUNNABLE_NOW",
    }:
        return f"Run the focused objective gate for locally available candidate {candidate}."
    if candidate_state == "NOT_CACHED_DOWNLOAD_ELIGIBLE":
        return f"Start the bounded download for admissible candidate {candidate}, then verify its manifest and blob hash."
    return f"Wait for a reliable bounded download window for candidate {candidate}; no production route change is permitted."


def _watch_result_block(result: dict[str, Any], state: dict[str, Any]) -> str:
    return "\n".join(
        [
            "ASTER_LOOP_RESULT",
            f"STATUS={result.get('status', 'FAILED')}",
            f"ISSUES_REMAINING={state.get('issues_remaining', '?')}",
            f"CURRENT_ISSUE={result.get('issue') or state.get('current_issue') or 'NONE'}",
            f"ACTION={result.get('action') or state.get('current_action') or 'none'}",
            f"VERIFICATION={result.get('verification', 'FAIL')}",
            "NEXT_PROMPT_BEGIN",
            state.get("next_prompt") or "Controller will derive the next prompt from persisted state.",
            "NEXT_PROMPT_END",
        ]
    )


def verify_acceptance_task(
    task: dict[str, Any], *, evidence_dir: Path, evidence_root: Path
) -> dict[str, Any]:
    """Run the trusted parent-side proof for one non-queue acceptance task."""
    task_id = str(task.get("id"))
    repair_kind = task.get("repair_kind")
    if repair_kind == "runtime_deployment_repair":
        restart = command_record(
            ["systemctl", "--user", "restart", "work-station-backend.service"],
            REPOSITORY_ROOT,
            timeout=60,
            output_directory=evidence_dir / "runtime-repair" / "restart",
        )
        health = trusted_loopback_health(evidence_dir / "runtime-repair" / "health")
        observations = observe(evidence_root)
        source_commit = observed_source_commit(observations)
        attestation = capture_current_runtime_attestation(
            evidence_dir / "runtime-repair" / "attestation", source_commit
        )
        observations_after = observe(evidence_root)
        runtime = observations_after.get("runtime", {})
        runtime_evidence = runtime.get("evidence")
        passed = bool(
            restart.get("exit_code") == 0
            and health.get("exit_code") == 0
            and attestation.get("objective_pass") is True
            and runtime.get("status") == "PASS"
            and isinstance(runtime_evidence, str)
            and Path(runtime_evidence).is_file()
            and runtime.get("source_matches_current") is True
            and isinstance(runtime.get("matches"), dict)
            and runtime["matches"].get("backend_source_sha256") is True
            and runtime["matches"].get("web_bundle_sha256") is True
        )
        if passed:
            failure_classification = "OBJECTIVE_PASS"
            reason = "runtime deployment restarted and current authenticated identity is content-verified"
        elif restart.get("exit_code") != 0:
            failure_classification = "LOCAL_RUNTIME_RESTART_FAILED"
            reason = "trusted runtime repair could not restart the backend service"
        elif health.get("exit_code") != 0:
            failure_classification = "LOCAL_RUNTIME_HEALTH_FAILED"
            reason = "backend restart completed but loopback health did not pass"
        else:
            failure_classification = "LOCAL_RUNTIME_PROVENANCE_FAILED"
            reason = "backend restart completed but current runtime identity remains unproven"
        verification = {
            "command": [
                "systemctl --user restart work-station-backend.service",
                "curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/api/v1/health/live",
                "controller.observe",
                "controller.validate_current_provenance",
            ],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 0 if passed else 1,
            "restart": restart,
            "health": health,
            "observations_before_attestation": observations,
            "fresh_attestation": attestation,
            "observations": observations_after,
            "failure_classification": failure_classification,
        }
    elif repair_kind == "release_dependency_alignment":
        proof_paths: list[str] = []
        proof_parts: list[str] = []
        last_result = task.get("last_result") if isinstance(task.get("last_result"), dict) else {}
        previous_parent = last_result.get("parent_verification") if isinstance(last_result, dict) else {}
        previous_verification = previous_parent.get("verification") if isinstance(previous_parent, dict) else {}
        candidate_paths = []
        if isinstance(previous_verification, dict):
            candidate_paths.extend(
                item for item in (
                    previous_verification.get("stdout_path"),
                    previous_verification.get("stderr_path"),
                ) if isinstance(item, str)
            )
        for item in task.get("evidence", []):
            if not isinstance(item, str):
                continue
            root = Path(item)
            candidate_paths.extend(
                str(root / name)
                for name in ("command.stdout.log", "command.stderr.log")
            )
        for raw_path in dict.fromkeys(candidate_paths):
            path = Path(raw_path)
            try:
                if path.is_file():
                    proof_paths.append(str(path))
                    proof_parts.append(path.read_text(encoding="utf-8", errors="replace")[:2_000_000])
            except OSError:
                continue
        proof_text = "\n".join(proof_parts)
        expected_mismatches = (
            "expo                ~57.0.23",
            "expo-image-picker   ~57.0.18",
            "expo-notifications  ~57.0.19",
            "packages out of date",
        )
        proof_passed = bool(proof_paths) and all(item in proof_text for item in expected_mismatches)
        node_runtime = resolve_node_runtime()
        npm_install = {
            "command": [],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 1,
            "reason": "exact current Expo mismatch was not proven from preserved release output",
        }
        mobile_check = {
            "command": [],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 1,
            "reason": "not run until preserved release evidence proves the exact mismatch",
        }
        production_commit: dict[str, Any] = {
            "attempted": False,
            "committed": False,
            "reason": "mobile correction not attempted",
        }
        if proof_passed:
            npm_install = command_record(
                [
                    "npm",
                    "install",
                    "--workspace",
                    "@work-station/mobile",
                    "expo@~57.0.23",
                    "expo-image-picker@~57.0.18",
                    "expo-notifications@~57.0.19",
                ],
                REPOSITORY_ROOT,
                timeout=1800,
                environment=node_runtime["environment"],
                output_directory=evidence_dir / "release-repair" / "npm-install",
            )
            if npm_install.get("exit_code") == 0:
                mobile_check = command_record(
                    [str(REPOSITORY_ROOT / "scripts/mobile_check.sh"), "--skip-native-android"],
                    REPOSITORY_ROOT,
                    timeout=1800,
                    environment=node_runtime["environment"],
                    output_directory=evidence_dir / "release-repair" / "mobile-check",
                )
            if npm_install.get("exit_code") == 0 and mobile_check.get("exit_code") == 0:
                production_commit = safe_commit_parent_repair(
                    "release_dependency_alignment", evidence_dir=evidence_dir / "release-repair"
                )
        passed = bool(
            proof_passed
            and npm_install.get("exit_code") == 0
            and mobile_check.get("exit_code") == 0
            and production_commit.get("committed") is True
            and production_commit.get("push", {}).get("exit_code") == 0
        )
        if passed:
            failure_classification = "OBJECTIVE_PASS"
            reason = "proven Expo SDK mismatch was aligned and focused mobile validation passed"
        elif not proof_passed:
            failure_classification = "LOCAL_RELEASE_EVIDENCE_INSUFFICIENT"
            reason = "preserved release evidence did not prove the exact dependency mismatch"
        elif npm_install.get("exit_code") != 0:
            failure_classification = "LOCAL_RELEASE_DEPENDENCY_REPAIR_FAILED"
            reason = "bounded npm workspace dependency alignment failed"
        elif mobile_check.get("exit_code") != 0:
            failure_classification = "LOCAL_MOBILE_VALIDATION_FAILED"
            reason = "Expo dependency alignment completed but focused mobile validation failed"
        else:
            failure_classification = "LOCAL_REPAIR_COMMIT_FAILED"
            reason = "mobile validation passed but the guarded repair commit/push did not complete"
        verification = {
            "command": [
                "preserved release output proof",
                "npm install --workspace @work-station/mobile expo@~57.0.23 expo-image-picker@~57.0.18 expo-notifications@~57.0.19",
                "scripts/mobile_check.sh --skip-native-android",
            ],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 0 if passed else 1,
            "failure_evidence_paths": proof_paths,
            "mismatch_proof": {
                "required_strings": expected_mismatches,
                "passed": proof_passed,
            },
            "node_runtime": node_runtime,
            "npm_install": npm_install,
            "mobile_check": mobile_check,
            "production_commit": production_commit,
            "failure_classification": failure_classification,
        }
    elif repair_kind == "acceptance_failure_diagnosis":
        diagnosis = diagnose_acceptance_failure(
            task, evidence_dir=evidence_dir, evidence_root=evidence_root
        )
        # A diagnosis is not a product acceptance pass.  It is complete only
        # when the parent-side executor has established a specific cause; the
        # original acceptance task remains failed until a separate correction
        # and verification actually pass.
        passed = diagnosis.get("objective_root_cause") is True
        verification = {
            "command": ["controller.diagnose_acceptance_failure"],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 0 if passed else 1,
            "diagnosis": diagnosis,
            "failure_classification": diagnosis.get("failure_classification"),
        }
        reason = (
            "trusted parent diagnosis established an objective failure cause"
            if passed
            else "trusted parent diagnosis could not establish an objective failure cause"
        )
    elif task_id == "ASTER-STATE-MERGE-001":
        command = [
            str(REPOSITORY_ROOT / "backend/.venv/bin/pytest"),
            "-q",
            "scripts/test_aster_autonomous_watch.py",
            "-k",
            "persist_state_cycle_bundle_survives_both_write_orders",
            "--disable-warnings",
        ]
        verification = command_record(command, REPOSITORY_ROOT, timeout=120)
        passed = verification.get("exit_code") == 0
        reason = "isolated cycle/evidence bundle regression passed" if passed else "state merge regression failed"
    elif task_id == "ASTER-RUNTIME-PROVENANCE-001":
        source_commit = observed_source_commit(observe(evidence_root))
        attestation = capture_current_runtime_attestation(evidence_dir, source_commit)
        observations = observe(evidence_root)
        runtime = observations.get("runtime", {})
        runtime_evidence = runtime.get("evidence")
        passed = bool(
            attestation.get("objective_pass") is True
            and runtime.get("status") == "PASS"
            and isinstance(runtime_evidence, str)
            and Path(runtime_evidence).is_file()
            and runtime.get("source_matches_current") is True
            and isinstance(runtime.get("matches"), dict)
            and runtime["matches"].get("backend_source_sha256") is True
            and runtime["matches"].get("web_bundle_sha256") is True
        )
        verification = {
            "command": ["controller.observe", "controller.validate_current_provenance"],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 0 if passed else 1,
            "observations": observations,
            "runtime_evidence": runtime_evidence,
            "fresh_attestation": attestation,
        }
        reason = "authenticated current runtime identity is content-verified" if passed else "current runtime identity is not fully proven"
    elif task_id == "ASTER-ARTIFACT-PROVENANCE-001":
        observations = observe(evidence_root)
        source_commit = observed_source_commit(observations)
        release_document, release_path = discover_latest_release_gate(evidence_root)
        source_commit = source_commit or git_snapshot().get("application_source_commit")
        manifest_path_value = (
            release_document.get("artifact_manifest")
            if isinstance(release_document, dict)
            else None
        )
        release_manifest = (
            read_json(Path(manifest_path_value), {})
            if isinstance(manifest_path_value, str)
            else {}
        )
        manifest_records = release_manifest.get("artifacts") if isinstance(release_manifest, dict) else []
        manifest_by_name = {
            str(item.get("artifact")): item
            for item in manifest_records
            if isinstance(item, dict) and item.get("artifact")
        } if isinstance(manifest_records, list) else {}
        default_paths = {
            name: path
            for _platform, name, path in _release_artifact_paths()
        }
        artifact_paths = []
        for name in ("Work_Station_Ubuntu.AppImage", "Work_Station_Ubuntu.deb"):
            record = manifest_by_name.get(name, {})
            path_value = record.get("path") if isinstance(record, dict) else None
            artifact_paths.append(
                (
                    "ubuntu-x86_64",
                    name,
                    Path(path_value) if isinstance(path_value, str) else default_paths.get(name, Path("/nonexistent")),
                )
            )
        pwa_record = manifest_by_name.get("Work_Station_PWA.tar.gz", {})
        pwa_value = pwa_record.get("path") if isinstance(pwa_record, dict) else None
        pwa = Path(pwa_value) if isinstance(pwa_value, str) else None
        git = git_snapshot()
        if (
            isinstance(source_commit, str)
            and COMMIT_PATTERN.fullmatch(source_commit)
            and isinstance(release_path, str)
        ):
            attestation, passed = build_current_artifact_attestation(
                evidence_dir=evidence_dir,
                source_commit=source_commit,
                release_document=release_document,
                release_path=release_path,
                artifact_paths=artifact_paths,
                pwa_path=pwa,
                git=git,
                release_manifest=release_manifest,
                release_manifest_path=manifest_path_value,
            )
        else:
            attestation = {
                "timestamp": utc_now(),
                "status": "NOT_VERIFIED_CURRENT_SOURCE_ARTIFACTS",
                "reason": "Current source or successful release evidence is unavailable.",
                "release_gate": release_path,
                "objective_pass": False,
            }
            passed = False
        attestation_path = evidence_dir / "artifact-attestation-current.json"
        write_json(attestation_path, attestation)
        verification = {
            "command": [
                "controller.observe",
                "controller.discover_latest_release_gate",
                "controller.read_release_artifact_manifest",
                "controller.build_current_artifact_attestation",
                "sha256_file(manifest-bound AppImage, Debian package, PWA archive)",
            ],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 0 if passed else 1,
            "observations": observations,
            "release_gate": release_path,
            "release_document": release_document,
            "attestation": str(attestation_path),
            "artifact_paths": [str(path) for _, _, path in artifact_paths],
            "pwa_path": str(pwa) if pwa else None,
            "release_manifest": manifest_path_value,
            "failure_classification": None if passed else "LOCAL_ARTIFACT_PROVENANCE_FAILED",
        }
        reason = (
            "current release gate is bound to recomputed AppImage, Debian and PWA hashes"
            if passed
            else "current release evidence or one or more artifact hashes could not be verified"
        )
    elif task_id == "ASTER-RELEASE-VALIDATION-001":
        node_runtime = resolve_node_runtime()
        verification = command_record(
            [str(REPOSITORY_ROOT / "scripts/release_check.sh"), "--with-runtime", "--require-clean"],
            REPOSITORY_ROOT,
            timeout=1800,
            environment=node_runtime["environment"],
            output_directory=evidence_dir,
        )
        verification["runtime"] = {
            "node_path": node_runtime["node_path"],
            "npm_path": node_runtime["npm_path"],
            "node_version": node_runtime["node_version"],
            "selection": node_runtime["selection"],
            "candidates": node_runtime["candidates"],
        }
        release_git = git_snapshot()
        release_output_path = verification.get("stdout_path")
        release_stderr_path = verification.get("stderr_path")
        release_evidence = {
            "timestamp": utc_now(),
            "status": "PASS" if verification.get("exit_code") == 0 else "FAIL",
            "exit_code": verification.get("exit_code"),
            "commit": release_git.get("commit"),
            "application_source_commit": release_git.get("application_source_commit"),
            "output_path": release_output_path,
            "output_sha256": sha256_file(Path(release_output_path)) if isinstance(release_output_path, str) else None,
            "stderr_path": release_stderr_path,
            "stderr_sha256": sha256_file(Path(release_stderr_path)) if isinstance(release_stderr_path, str) else None,
            "checks": {
                "security": "PASS; release reached and passed the security audit"
                if verification.get("exit_code") == 0
                else "NOT_PROVEN; release stopped before all gates completed",
            },
            "runtime": verification["runtime"],
        }
        release_evidence_path = evidence_dir / "release-check-current.json"
        write_json(release_evidence_path, release_evidence)
        verification["release_evidence"] = str(release_evidence_path)
        manifest_passed = False
        if verification.get("exit_code") == 0:
            post_release_observations = observe(evidence_root)
            runtime_evidence_path = post_release_observations.get("runtime", {}).get("evidence")
            runtime_document = (
                read_json(Path(runtime_evidence_path), {})
                if isinstance(runtime_evidence_path, str)
                else {}
            )
            runtime_payload = runtime_document.get("runtime") if isinstance(runtime_document, dict) else {}
            served_web_bundle_sha256 = (
                runtime_payload.get("web_bundle_sha256")
                if isinstance(runtime_payload, dict)
                else None
            )
            manifest, manifest_passed, manifest_path = capture_release_artifact_manifest(
                evidence_dir=evidence_dir,
                source_commit=str(release_git.get("application_source_commit") or ""),
                release_document=release_evidence,
                release_path=str(release_evidence_path),
                git=release_git,
                served_web_bundle_sha256=served_web_bundle_sha256,
            )
            release_evidence["artifact_manifest"] = str(manifest_path)
            release_evidence["artifact_manifest_sha256"] = sha256_file(manifest_path)
            release_evidence["served_web_bundle_sha256"] = served_web_bundle_sha256
            write_json(release_evidence_path, release_evidence)
            verification["post_release_observations"] = post_release_observations
            verification["artifact_manifest"] = str(manifest_path)
            verification["artifact_manifest_sha256"] = sha256_file(manifest_path)
            verification["artifact_manifest_objective_pass"] = manifest_passed
        passed = verification.get("exit_code") == 0 and manifest_passed
        reason = (
            "current-source release gate and source-bound artifact manifest passed"
            if passed
            else "release gate or source-bound artifact manifest produced a current failure/blocker"
        )
    else:
        verification = {
            "command": [],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 1,
            "reason": "no trusted local executor is available for this external task",
        }
        passed = False
        reason = "external dependency remains unavailable"
    record = {
        "timestamp": utc_now(),
        "task_id": task_id,
        "reason": reason,
        "objective_pass": passed,
        "failure_classification": "OBJECTIVE_PASS" if passed else verification.get("failure_classification"),
        "verification": verification,
        "evidence_dir": str(evidence_dir),
    }
    write_json(evidence_dir / "parent-verification.json", record)
    return record


def acceptance_task_iteration(
    state: dict[str, Any],
    *,
    evidence_root: Path,
    cycle: int,
    execute_codex: bool,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    task = choose_acceptance_task(state)
    if task is None:
        return None
    task_id = str(task.get("id"))
    cycle_root = evidence_root / "autonomous-loop" / "acceptance" / f"cycle-{cycle:04d}"
    cycle_root.mkdir(parents=True, exist_ok=True)
    observations = observe(evidence_root)
    source_commit = observed_source_commit(observations)
    prompt = build_acceptance_task_prompt(task, observations, str(cycle_root))
    state.setdefault("external_watch", {})["cycle"] = cycle
    state["external_watch"]["status"] = "ACCEPTANCE_TASK"
    state["external_watch"]["evidence"] = str(cycle_root)
    state["external_watch"]["current_action"] = str(task.get("action"))
    state["current_issue"] = task_id
    state["current_action"] = str(task.get("action"))
    state["controller_phase"] = "ACCEPTANCE_TASK"
    state["last_prompt"] = prompt
    state["next_prompt"] = prompt
    state["next_prompt_source"] = "controller_derived"
    state["next_prompt_source_commit"] = source_commit
    # persist_state replaces the caller's mapping with the merged durable
    # snapshot; refresh the task reference before recording the result so a
    # detached pre-persist object cannot lose the decision.
    task = state["acceptance_tasks"][task_id]
    task["attempts"] = int(task.get("attempts", 0) or 0) + 1
    task["updated_at"] = utc_now()
    persist_state(state, evidence_root)
    write_json(
        cycle_root / "before.json",
        {"timestamp": utc_now(), "cycle": cycle, "task": task, "observations": observations, "git": git_snapshot()},
    )
    if execute_codex:
        child = run_codex_child(prompt, cycle_root, timeout_seconds=timeout_seconds, evidence_root=evidence_root)
    else:
        child = {
            "command": [],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 0,
            "timed_out": False,
            "parsed": parse_result_text(
                "ASTER_LOOP_RESULT\nSTATUS=NOT_READY\nISSUES_REMAINING=0\n"
                f"CURRENT_ISSUE={task_id}\nACTION=observe\nVERIFICATION=PARTIAL\n"
                "NEXT_PROMPT_BEGIN\nParent verification required.\nNEXT_PROMPT_END\n"
            ),
        }
    parsed = child.get("parsed", {}) if isinstance(child, dict) else {}
    prompt_source_ok = prompt_source_commit(prompt) == source_commit
    child_identity_ok = bool(
        child.get("exit_code") == 0
        and parsed.get("parsed") is True
        and parsed.get("current_issue") == task_id
        and prompt_source_ok
    )
    parent = verify_acceptance_task(task, evidence_dir=cycle_root, evidence_root=evidence_root)
    post_repair_observations = parent.get("verification", {}).get("observations")
    production_commit = parent.get("verification", {}).get("production_commit")
    if isinstance(production_commit, dict) and production_commit.get("commit"):
        # A trusted repair may change application source (for example, the
        # Expo manifest correction). Re-observe immediately so stale runtime
        # and release decisions cannot be selected in the same owner cycle.
        refreshed = observe(evidence_root)
        refreshed_source = observed_source_commit(refreshed)
        if refresh_source_bound_acceptance_tasks(state, refreshed_source):
            persist_state(state, evidence_root)
        post_repair_observations = refreshed
        if refreshed_source:
            source_commit = refreshed_source
    objective_pass = bool(child_identity_ok and parent.get("objective_pass") is True)
    task["last_result"] = {
        "timestamp": utc_now(),
        "cycle": cycle,
        "child_identity_verified": child_identity_ok,
        "parent_verification": parent,
        "child": child,
    }
    task["validated_source_commit"] = source_commit
    task.setdefault("evidence", []).append(str(cycle_root))
    task["evidence"] = task["evidence"][-20:]
    external_failure = _verified_external_failure(parent) if child_identity_ok else None
    repair_task = None
    if objective_pass:
        task["status"] = "COMPLETE"
        task["blocker"] = None
    elif external_failure is not None:
        task["status"] = "BLOCKED_EXTERNAL"
        task["blocker"] = (
            f"{external_failure['reason']}; unblock condition: "
            f"{external_failure['unblock_condition']}"
        )
    else:
        task["status"] = "FAILED"
        if task.get("repair_kind") or task.get("parent_task_id"):
            task["blocker"] = (
                "Bounded repair attempt failed objective verification; preserve the evidence and "
                "do not manufacture an unbounded repair-of-repair chain."
            )
        else:
            repair_task = enqueue_acceptance_failure_repair(
                state,
                task,
                parent=parent,
                child=child,
                evidence_dir=cycle_root,
            )
            task["blocker"] = (
                "Objective verification failed; dispatched bounded local diagnosis/repair task "
                f"{repair_task.get('id') if repair_task else 'already-present-or-unavailable'}."
            )
    if objective_pass and task.get("parent_task_id"):
        parent_task = state["acceptance_tasks"].get(str(task["parent_task_id"]))
        if isinstance(parent_task, dict):
            if task.get("repair_kind") == "runtime_deployment_repair":
                parent_task["status"] = "COMPLETE"
                parent_task["blocker"] = None
            elif task.get("repair_kind") == "release_dependency_alignment":
                parent_task["status"] = "PENDING"
                parent_task["blocker"] = "Dependency correction completed; run the full current-source release gate."
            elif task.get("repair_kind") == "acceptance_failure_diagnosis":
                diagnosis = parent.get("verification", {}).get("diagnosis", {})
                classification = diagnosis.get("failure_classification", "LOCAL_FAILURE_ROOT_CAUSE_UNCONFIRMED")
                parent_task["status"] = "FAILED"
                parent_task["blocker"] = (
                    f"Bounded diagnosis established {classification}; no correction was applied. "
                    "Preserve the objective failure and wait for the explicitly identified repair condition."
                )
                parent_task["failure_diagnosis"] = {
                    "classification": classification,
                    "reason": diagnosis.get("reason"),
                    "repair_action": diagnosis.get("repair_action"),
                    "evidence": [diagnosis.get("evidence_path")] if diagnosis.get("evidence_path") else [],
                }
            parent_task["repair_resolution"] = {
                "repair_task_id": task.get("id"),
                "updated_at": utc_now(),
                "evidence": task.get("evidence", []),
                "failure_signature": task.get("failure_signature"),
                "validated_source_commit": task.get("validated_source_commit"),
            }
            parent_task["updated_at"] = utc_now()
    task["updated_at"] = utc_now()
    task_status = str(task.get("status"))
    task_blocker = task.get("blocker")
    task_decision = copy.deepcopy(task)
    next_task = choose_acceptance_task(state)
    next_prompt = (
        build_acceptance_task_prompt(next_task, post_repair_observations or parent.get("verification", {}).get("observations", observations), str(cycle_root))
        if next_task
        else build_external_watch_prompt(None, post_repair_observations or parent.get("verification", {}).get("observations", observations), str(cycle_root))
    )
    state["next_prompt"] = next_prompt
    state["next_prompt_source"] = "controller_derived"
    state["next_prompt_source_commit"] = source_commit
    state["child_suggested_next_prompt"] = parsed.get("next_prompt", "") if parsed.get("parsed") else ""
    state["last_result"] = {
        "timestamp": utc_now(),
        "cycle": cycle,
        "issue": task_id,
        "status": "NOT_READY",
        "action": task.get("action"),
        "verification": "PASS" if objective_pass else ("BLOCKED" if external_failure else "FAIL"),
        "objective_pass": objective_pass,
        "classification": task.get("status"),
        "repair_task": copy.deepcopy(repair_task) if repair_task else None,
        "child": child,
        "parent_verification": parent,
        "evidence_dir": str(cycle_root),
    }
    state["iterations_completed"] = int(state.get("iterations_completed", 0)) + (1 if objective_pass else 0)
    state["in_progress"] = False
    state["resume_required"] = False
    state["history"].append(
        {
            "timestamp": utc_now(),
            "iteration": int(state.get("iteration", 0)),
            "attempt": int(state.get("attempt", 0)),
            "issue": task_id,
            "status": "NOT_READY",
            "verification": "PASS" if objective_pass else ("BLOCKED" if external_failure else "FAIL"),
            "classification": task.get("status"),
            "evidence": str(cycle_root),
            "resumable": False,
        }
    )
    persist_state(state, evidence_root)
    queue = current_queue()
    observations_after = observe(evidence_root)
    render_reports(state, queue, observations_after, evidence_root=evidence_root)
    report_commit = persist_watch_report_commit(cycle)
    # render_reports persists and replaces the state mapping; reapply the
    # terminal decision to the current mapping before the final checkpoint.
    current_task = state["acceptance_tasks"][task_id]
    for key in (
        "status",
        "blocker",
        "attempts",
        "evidence",
        "last_result",
        "validated_source_commit",
        "active_failure_signature",
        "updated_at",
    ):
        if key in task_decision:
            current_task[key] = copy.deepcopy(task_decision[key])
    current_task["status"] = task_status
    current_task["blocker"] = task_blocker
    current_task["last_report_commit"] = report_commit
    current_task["updated_at"] = utc_now()
    write_json(cycle_root / "result.json", state["last_result"] | {"report_commit": report_commit})
    persist_state(state, evidence_root)
    return state["last_result"]


def watch_external_iteration(
    state: dict[str, Any],
    *,
    evidence_root: Path,
    cycle: int,
    execute_codex: bool,
    timeout_seconds: int,
    max_download_seconds: int,
    max_estimated_hours: float,
) -> dict[str, Any]:
    """Run one bounded external-gap observation or candidate handoff."""
    effective_max_download_seconds = min(
        max_download_seconds, DEFAULT_MAX_DOWNLOAD_SECONDS
    )
    effective_max_estimated_hours = min(
        max_estimated_hours, DEFAULT_MAX_ESTIMATED_DOWNLOAD_HOURS
    )
    queue = current_queue()
    observations_before = observe(evidence_root)
    watch_root = evidence_root / "autonomous-loop" / "watch-external" / f"cycle-{cycle:04d}"
    watch_root.mkdir(parents=True, exist_ok=True)
    candidate_hint = watch_candidate_references(state)[0]
    before = {
        "timestamp": utc_now(),
        "cycle": cycle,
        "phase": "WATCH_EXTERNAL_GAP",
        "candidate_hint": candidate_hint,
        "observations": observations_before,
        "git": git_snapshot(),
    }
    write_json(watch_root / "before.json", before)
    previous_watch = (
        state.get("external_watch")
        if isinstance(state.get("external_watch"), dict)
        else {}
    )
    rejected_candidates = {
        str(reference)
        for reference in previous_watch.get("rejected_candidates", [])
        if isinstance(reference, str) and _safe_model_reference(reference)
    }
    acquisition_blocked_candidates = {
        str(reference)
        for reference in previous_watch.get("acquisition_blocked_candidates", [])
        if isinstance(reference, str) and _safe_model_reference(reference)
    }
    # Recover durable candidate decisions from evidence if an older watcher
    # cycle was interrupted after writing its report but before the rejection
    # list was copied into current_state.json.
    for prior_result_path in sorted(
        (evidence_root / "autonomous-loop" / "watch-external").glob(
            "cycle-*/result.json"
        )
    ):
        prior_result = read_json(prior_result_path, {})
        trusted = prior_result.get("trusted_parent_focused_gate", {}) if isinstance(prior_result, dict) else {}
        focused = prior_result.get("focused_objective_gate", {}) if isinstance(prior_result, dict) else {}
        prior_candidate = prior_result.get("candidate") if isinstance(prior_result, dict) else None
        if (
            isinstance(prior_candidate, str)
            and _safe_model_reference(prior_candidate)
            and isinstance(trusted, dict)
            and trusted.get("run_status") == "complete"
            and isinstance(focused, dict)
            and focused.get("exact_eight_record_matrix") is True
            and focused.get("admitted") is not True
        ):
            rejected_candidates.add(prior_candidate)
        prior_candidate_state = prior_result.get("candidate_state") if isinstance(prior_result, dict) else None
        prior_download = prior_result.get("discovery", {}).get("download") if isinstance(prior_result, dict) and isinstance(prior_result.get("discovery"), dict) else None
        if (
            isinstance(prior_candidate, str)
            and _safe_model_reference(prior_candidate)
            and prior_candidate_state == "DOWNLOAD_FAILED_OR_INTEGRITY_FAILED"
            and isinstance(prior_download, dict)
            and prior_download.get("exit_code") != 0
        ):
            acquisition_blocked_candidates.add(prior_candidate)
    if previous_watch.get("candidate_state") == "DOWNLOAD_FAILED_OR_INTEGRITY_FAILED":
        prior_candidate = previous_watch.get("candidate")
        if isinstance(prior_candidate, str) and _safe_model_reference(prior_candidate):
            acquisition_blocked_candidates.add(prior_candidate)
    if rejected_candidates or acquisition_blocked_candidates:
        state.setdefault("external_watch", {})["rejected_candidates"] = sorted(
            rejected_candidates
        )
        state["external_watch"]["acquisition_blocked_candidates"] = sorted(
            acquisition_blocked_candidates
        )
    discovery = discover_watch_candidates(
        state,
        max_estimated_hours=effective_max_estimated_hours,
        max_download_seconds=effective_max_download_seconds,
    )
    write_json(watch_root / "candidate-discovery.json", discovery)
    discovered_candidate = discovery.get("candidate")
    candidate = (
        str(discovered_candidate)
        if isinstance(discovered_candidate, str) and discovered_candidate
        else None
    )
    candidate_state = str(discovery.get("candidate_state") or "UNKNOWN")
    persisted_rejected_candidates = sorted(
        {
            str(reference)
            for reference in previous_watch.get("rejected_candidates", [])
            if isinstance(reference, str) and _safe_model_reference(reference)
        }
    )
    persisted_acquisition_blocked_candidates = sorted(
        {
            str(reference)
            for reference in previous_watch.get("acquisition_blocked_candidates", [])
            if isinstance(reference, str) and _safe_model_reference(reference)
        }
    )
    state["external_watch"] = {
        "status": discovery.get("status"),
        "cycle": cycle,
        "candidate": candidate,
        "candidate_state": candidate_state,
        "candidate_priority": watch_candidate_references(state),
        "rejected_candidates": persisted_rejected_candidates,
        "acquisition_blocked_candidates": persisted_acquisition_blocked_candidates,
        "excluded_candidate": None,
        "excluded_candidate_absent": True,
        "max_download_seconds": effective_max_download_seconds,
        "max_estimated_download_hours": effective_max_estimated_hours,
        "requested_max_download_seconds": max_download_seconds,
        "requested_max_estimated_download_hours": max_estimated_hours,
        "genericity_and_canonical_validation_allowed": bool(
            previous_watch.get("candidate") == candidate
            and previous_watch.get(
                "genericity_and_canonical_validation_allowed"
            )
            is True
        ),
        "evidence": str(watch_root),
    }

    if discovery.get("status") == "DOWNLOAD_ELIGIBLE":
        state["controller_phase"] = "CANDIDATE_DOWNLOADING"
        state["external_watch"]["download_policy"] = "bounded"
        persist_state(state, evidence_root)
        download = command_record(
            ["ollama", "pull", candidate],
            REPOSITORY_ROOT,
            timeout=effective_max_download_seconds,
        )
        discovery["download"] = download
        verified = local_ollama_candidate(candidate)
        discovery["post_download_verification"] = verified
        write_json(watch_root / "candidate-discovery.json", discovery)
        if download.get("exit_code") != 0 or not verified.get("available"):
            blocked = list(state["external_watch"].get("acquisition_blocked_candidates", []))
            if candidate not in blocked:
                blocked.append(candidate)
            state["controller_phase"] = "WATCH_EXTERNAL_GAP"
            state["external_watch"].update(
                {
                    "status": "CANDIDATE_REJECTED",
                    "candidate_state": "DOWNLOAD_FAILED_OR_INTEGRITY_FAILED",
                    "acquisition_blocked_candidates": sorted(set(blocked)),
                    "download": download,
                    "post_download_verification": verified,
                }
            )
            candidate_state = "DOWNLOAD_FAILED_OR_INTEGRITY_FAILED"
        else:
            discovery["status"] = "CANDIDATE_AVAILABLE"
            candidate_state = "AVAILABLE_LOCAL_INTEGRITY_VERIFIED"
            state["external_watch"].update({"status": discovery["status"], "candidate_state": candidate_state})

    catalog_admission: dict[str, Any] = {}
    if (
        discovery.get("status") == "CANDIDATE_AVAILABLE"
        and candidate_state in {
            "AVAILABLE_LOCAL",
            "AVAILABLE_LOCAL_INTEGRITY_VERIFIED",
        }
    ):
        catalog_admission = catalog_candidate_admission(candidate)
        discovery["catalog_hardware_admission"] = catalog_admission
        write_json(watch_root / "candidate-discovery.json", discovery)
        if catalog_admission.get("admitted") is True:
            candidate_state = "INSTALLED_RUNNABLE_NOW"
            state["external_watch"].update(
                {
                    "status": "CANDIDATE_AVAILABLE",
                    "candidate_state": candidate_state,
                    "catalog_hardware_admission": catalog_admission,
                }
            )
        else:
            discovery["status"] = "CANDIDATE_REJECTED"
            candidate_state = "CATALOG_HARDWARE_ADMISSION_FAILED"
            state["external_watch"].update(
                {
                    "status": discovery["status"],
                    "candidate_state": candidate_state,
                    "catalog_hardware_admission": catalog_admission,
                }
            )

    if discovery.get("status") == "CANDIDATE_AVAILABLE" and candidate_state == "INSTALLED_RUNNABLE_NOW":
        state["controller_phase"] = "CANDIDATE_FOCUSED_GATE"
        state["current_issue"] = None
        state["current_action"] = _watch_action(candidate, candidate_state)
        current_source_commit = observed_source_commit(observations_before)
        trusted_gate = (
            trusted_parent_focused_gate(
                watch_root,
                candidate,
                current_source_commit,
                timeout_seconds=timeout_seconds,
            )
            if execute_codex
            else {
                "trusted_parent": False,
                "run_status": "NOT_RUN",
                "all_eight_objective_pass": False,
                "output": str(watch_root / "focused-candidate-gate.json"),
            }
        )
        focused_gate = focused_objective_gate(watch_root, candidate)
        state["external_watch"]["trusted_parent_gate"] = {
            key: value
            for key, value in trusted_gate.items()
            if key not in {"command", "report_summary"}
        }
        persisted_child_prompt = (
            state.get("next_prompt")
            if state.get("next_prompt_source") == "child_structured_result"
            else None
        )
        persisted_prompt_source = state.get("next_prompt_source_commit")
        if persisted_child_prompt:
            persisted_prompt_source = persisted_prompt_source or prompt_source_commit(
                str(persisted_child_prompt)
            )
        prior_focused_gate_passed = (
            isinstance(state.get("external_watch"), dict)
            and state["external_watch"].get(
                "genericity_and_canonical_validation_allowed"
            )
            is True
        )
        prompt_is_current = bool(
            persisted_child_prompt
            and (
                _candidate_focus_prompt(persisted_child_prompt)
                or prior_focused_gate_passed
            )
            and current_source_commit
            and persisted_prompt_source == current_source_commit
        )
        prompt = (
            str(persisted_child_prompt)
            if prompt_is_current
            else build_candidate_focus_prompt(candidate, observations_before, str(watch_root))
        )
        state["next_prompt"] = prompt
        state["last_prompt"] = prompt
        state["next_prompt_source_commit"] = current_source_commit
        state["in_progress"] = True
        state["resume_required"] = True
        persist_state(state, evidence_root)
        if execute_codex:
            child = run_codex_child(prompt, watch_root, timeout_seconds=timeout_seconds, evidence_root=evidence_root)
        else:
            child = {
                "command": [],
                "cwd": str(REPOSITORY_ROOT),
                "exit_code": 0,
                "duration_seconds": 0,
                "timed_out": False,
                "parsed": {
                    "parsed": True,
                    "status": "WAITING",
                    "verification": "WAITING",
                    "classification": "OBSERVE_ONLY",
                    "action": "Watch dry-run; no Codex child executed.",
                    "next_prompt": prompt,
                },
            }
        parsed = child.get("parsed", {})
        child["focused_objective_gate"] = focused_gate
        child["trusted_parent_focused_gate"] = trusted_gate
        if execute_codex and focused_gate.get("admitted") is not True:
            parsed = {
                **parsed,
                "parsed": True,
                "status": "NOT_READY",
                "verification": "PARTIAL",
                "classification": "NOT_READY",
                "action": (
                    "Focused candidate admission stopped before genericity or canonical "
                    "validation because all eight objective_pass values were not true."
                ),
                "next_prompt": prompt,
            }
            child["parsed"] = parsed
        temporary_failure = child.get("exit_code") != 0 and bool(child.get("timed_out") or child.get("exit_code") == 127)
        if child.get("exit_code") != 0:
            state["last_failure"] = {
                "timestamp": utc_now(),
                "classification": "BLOCKED_EXTERNAL" if temporary_failure else "FAILED",
                "exit_code": child.get("exit_code"),
                "timed_out": child.get("timed_out"),
                "stderr_path": child.get("stderr_path"),
                "error_excerpt": child.get("stderr_tail"),
            }
        result = {
            "timestamp": utc_now(),
            "iteration": int(state.get("iteration", 0)),
            "attempt": int(state.get("attempt", 0)),
            "cycle": cycle,
            "issue": None,
            "status": parsed.get("status", "FAILED"),
            "action": parsed.get("action", _watch_action(candidate, candidate_state)),
            "verification": parsed.get("verification", "FAIL"),
            "classification": parsed.get("classification", "FAILED"),
            "child": child,
            "candidate": candidate,
            "catalog_hardware_admission": catalog_admission,
            "trusted_parent_focused_gate": trusted_gate,
            "focused_objective_gate": focused_gate,
            "evidence_dir": str(watch_root),
            "before": before,
            "after": {"observations": observe(evidence_root), "git": git_snapshot()},
        }
        write_json(watch_root / "result.json", result)
        state["last_result"] = result
        state["in_progress"] = temporary_failure
        state["resume_required"] = temporary_failure
        apply_child_handoff(state, parsed, prompt, source_commit=current_source_commit)
        state["external_watch"].update(
            {
                "status": "CANDIDATE_FOCUSED_GATE",
                "candidate_state": "FOCUSED_GATE_RESULT_CAPTURED",
                "all_eight_objective_pass": focused_gate.get("all_eight_objective_pass") is True,
                "genericity_and_canonical_validation_allowed": focused_gate.get("admitted") is True,
                "current_action": state.get("current_action"),
                "child_result": str(watch_root / "result.json"),
            }
        )
        definitive_candidate_rejection = bool(
            execute_codex
            and trusted_gate.get("run_status") == "complete"
            and focused_gate.get("exact_eight_record_matrix") is True
            and focused_gate.get("admitted") is not True
        )
        if definitive_candidate_rejection:
            rejected = list(state["external_watch"].get("rejected_candidates", []))
            if candidate not in rejected:
                rejected.append(candidate)
            state["external_watch"].update(
                {
                    "status": "CANDIDATE_REJECTED",
                    "candidate_state": "FOCUSED_GATE_OBJECTIVE_FAILED",
                    "rejected_candidates": sorted(set(rejected)),
                    "rejection_reason": "complete trusted 8-record gate did not objectively pass all records",
                }
            )
            child_suggested_prompt = state.get("next_prompt")
            if isinstance(child_suggested_prompt, str) and child_suggested_prompt.strip():
                state["child_suggested_next_prompt"] = child_suggested_prompt
            state["next_prompt_source"] = "controller_derived"
            next_candidates = [
                reference
                for reference in HOST_CONTROLLER_CANDIDATES
                if reference not in set(rejected)
            ]
            next_candidate = next_candidates[0] if next_candidates else candidate
            state["current_action"] = _watch_action(
                next_candidate, "NOT_CACHED_DOWNLOAD_TOO_SLOW_OR_UNAVAILABLE"
            )
            state["external_watch"]["current_action"] = state["current_action"]
            state["next_prompt"] = build_external_watch_prompt(
                next_candidate, result["after"]["observations"], str(watch_root)
            )
        state["history"].append(
            {
                "timestamp": result["timestamp"],
                "iteration": int(state.get("iteration", 0)),
                "attempt": int(state.get("attempt", 0)),
                "issue": None,
                "status": result["status"],
                "verification": result["verification"],
                "classification": result["classification"],
                "evidence": str(watch_root),
                "resumable": temporary_failure,
            }
        )
        persist_state(state, evidence_root)
        render_reports(state, queue, result["after"]["observations"], evidence_root=evidence_root)
        report_commit = persist_watch_report_commit(cycle)
        result["report_commit"] = report_commit
        state["external_watch"]["report_commit"] = report_commit
        write_json(watch_root / "result.json", result)
        state["last_result"] = result
        persist_state(state, evidence_root)
        return result

    state["controller_phase"] = "WATCH_EXTERNAL_GAP"
    state["current_action"] = _watch_action(candidate, candidate_state)
    next_prompt_candidate = candidate
    if candidate_state == "DOWNLOAD_FAILED_OR_INTEGRITY_FAILED":
        excluded = set(state.get("external_watch", {}).get("rejected_candidates", []))
        excluded.update(state.get("external_watch", {}).get("acquisition_blocked_candidates", []))
        next_candidate = next(
            (
                reference
                for reference in HOST_CONTROLLER_CANDIDATES
                if reference not in excluded
            ),
            candidate,
        )
        state["current_action"] = _watch_action(
            next_candidate, "NOT_CACHED_DOWNLOAD_TOO_SLOW_OR_UNAVAILABLE"
        )
        state["external_watch"]["current_action"] = state["current_action"]
        next_prompt_candidate = next_candidate
    state["in_progress"] = False
    state["resume_required"] = False
    state["child_suggested_next_prompt"] = ""
    state["next_prompt_source"] = "controller_derived"
    state["next_prompt_source_commit"] = observed_source_commit(observations_before)
    state["next_prompt"] = build_external_watch_prompt(
        next_prompt_candidate, observations_before, str(watch_root)
    )
    retry_seconds = float(state.get("external_watch", {}).get("watch_interval_seconds", DEFAULT_WATCH_INTERVAL_SECONDS))
    next_retry = datetime.fromtimestamp(time.time() + max(0.0, retry_seconds), timezone.utc).isoformat()
    state["external_watch"]["next_retry_at"] = next_retry
    state["external_watch"]["current_action"] = state["current_action"]
    result = {
        "timestamp": utc_now(),
        "iteration": int(state.get("iteration", 0)),
        "attempt": int(state.get("attempt", 0)),
        "cycle": cycle,
        "issue": None,
        "status": "NOT_READY",
        "action": state["current_action"],
        "verification": "PARTIAL",
        "classification": "NOT_READY",
        "candidate": candidate,
        "candidate_state": candidate_state,
        "discovery": discovery,
        "evidence_dir": str(watch_root),
        "before": before,
        "after": {"observations": observe(evidence_root), "git": git_snapshot()},
        "next_retry_at": next_retry,
    }
    write_json(watch_root / "result.json", result)
    state["last_result"] = result
    state["history"].append(
        {
            "timestamp": result["timestamp"],
            "iteration": int(state.get("iteration", 0)),
            "attempt": int(state.get("attempt", 0)),
            "issue": None,
            "status": result["status"],
            "verification": result["verification"],
            "classification": result["classification"],
            "evidence": str(watch_root),
            "resumable": False,
        }
    )
    persist_state(state, evidence_root)
    render_reports(state, queue, result["after"]["observations"], evidence_root=evidence_root)
    report_commit = persist_watch_report_commit(cycle)
    result["report_commit"] = report_commit
    state["external_watch"]["report_commit"] = report_commit
    write_json(watch_root / "result.json", result)
    state["last_result"] = result
    persist_state(state, evidence_root)
    return result


def run_external_watch(args: argparse.Namespace, state: dict[str, Any], evidence_root: Path) -> int:
    state["controller_phase"] = "WATCH_EXTERNAL_GAP"
    state.setdefault("external_watch", {})["watch_interval_seconds"] = args.watch_interval_seconds
    state["external_watch"]["max_watch_cycles"] = args.max_watch_cycles
    state["external_watch"]["max_download_seconds"] = args.max_download_seconds
    state["external_watch"]["max_estimated_download_hours"] = args.max_estimated_download_hours
    persist_state(state, evidence_root)
    start_cycle = int(state.get("external_watch", {}).get("cycle", 0) or 0)
    for offset in range(args.max_watch_cycles):
        queue = current_queue()
        observations = observe(evidence_root)
        if refresh_source_bound_acceptance_tasks(
            state, observed_source_commit(observations)
        ):
            # Source-bound runtime/release decisions were invalidated by a
            # real application change; checkpoint before selecting the next
            # child so a restart cannot lose the revalidation requirement.
            persist_state(state, evidence_root)
        if readiness(queue, observations)[0]:
            state["controller_phase"] = "READY"
            persist_state(state, evidence_root)
            render_reports(state, queue, observations, evidence_root=evidence_root)
            return 0
        cycle = start_cycle + offset + 1
        # Queue exhaustion is not completion.  Run one persisted local
        # acceptance task before probing external model availability.  When a
        # task completes and unlocks another task, the next child is launched
        # in the same bounded owner loop without manual prompt relay.
        acceptance_task_records(state)
        recovered_repairs = recover_persisted_acceptance_failure_repairs(state)
        if recovered_repairs:
            # This migration is intentionally persisted before selection.  It
            # makes a failed result from an older controller actionable after
            # restart instead of allowing the watch lane to hide it.
            persist_state(state, evidence_root)
        task = choose_acceptance_task(state)
        if task is not None:
            result = acceptance_task_iteration(
                state,
                evidence_root=evidence_root,
                cycle=cycle,
                execute_codex=not args.no_codex,
                timeout_seconds=args.timeout_seconds,
            )
            if result is not None:
                print(_watch_result_block(result, state))
            if state.get("overall_ready"):
                return 0
            if choose_acceptance_task(state) is not None and offset + 1 < args.max_watch_cycles:
                continue
        result = watch_external_iteration(
            state,
            evidence_root=evidence_root,
            cycle=cycle,
            execute_codex=not args.no_codex,
            timeout_seconds=args.timeout_seconds,
            max_download_seconds=args.max_download_seconds,
            max_estimated_hours=args.max_estimated_download_hours,
        )
        print(_watch_result_block(result, state))
        if state.get("overall_ready"):
            return 0
        if offset + 1 < args.max_watch_cycles:
            _bounded_sleep(args.watch_interval_seconds)
    print_terminal()
    return 0


def iteration_once(
    state: dict[str, Any], *, evidence_root: Path, execute_codex: bool, timeout_seconds: int, auto_commit: bool
) -> dict[str, Any]:
    queue = current_queue()
    observations_before = observe(evidence_root)
    selected = choose_issue(queue)
    if selected is None:
        state["iteration"] = max(int(state.get("iteration", 0)), int(state.get("iterations_completed", 0)))
        mark_terminal_queue_state(state)
        render_reports(state, queue, observations_before, evidence_root=evidence_root)
        return {"status": "READY" if state.get("overall_ready") else "NOT_READY", "verification": "PARTIAL", "action": state.get("current_action")}

    if state.get("in_progress"):
        iteration = int(state.get("iteration", 0))
    else:
        iteration = int(state.get("iteration", 0)) + 1
        state["iteration"] = iteration
        state["in_progress"] = True
    iteration_root = evidence_root / "autonomous-loop" / f"iteration-{iteration:04d}"
    iteration_root.mkdir(parents=True, exist_ok=True)
    previous_result = iteration_root / "result.json"
    if state.get("in_progress") and previous_result.exists():
        attempt = int(state.get("attempt", 1) or 1) + 1
        iteration_dir = iteration_root / f"attempt-{attempt:04d}"
    else:
        attempt = 1
        iteration_dir = iteration_root
    iteration_dir.mkdir(parents=True, exist_ok=True)
    state["attempt"] = attempt
    prompt = state.get("next_prompt") or build_prompt(selected, observations_before)
    state["last_prompt"] = prompt
    state["current_issue"] = selected.get("id")
    state["current_action"] = required_action(selected)
    persist_state(state, evidence_root)

    before = {
        "timestamp": utc_now(),
        "issue": selected,
        "observations": observations_before,
        "git": git_snapshot(),
    }
    write_json(iteration_dir / "before.json", before)
    if execute_codex:
        child = run_codex_child(prompt, iteration_dir, timeout_seconds=timeout_seconds, evidence_root=evidence_root)
    else:
        child = {
            "command": [],
            "cwd": str(REPOSITORY_ROOT),
            "exit_code": 0,
            "duration_seconds": 0,
            "timed_out": False,
            "parsed": {
                "parsed": True,
                "status": "NOT_READY",
                "verification": "PARTIAL",
                "classification": "OBSERVE_ONLY",
                "action": "Controller dry-run; no Codex child executed.",
                "next_prompt": prompt,
            },
        }
    commit_result = safe_commit_if_validated(child, iteration) if auto_commit else {"attempted": False, "reason": "auto_commit_disabled"}
    if commit_result.get("commit"):
        state["report_tip_commit"] = commit_result["commit"]
    observations_after = observe(evidence_root)
    parsed = child.get("parsed", {})
    temporary_failure = False
    if child.get("exit_code") != 0:
        temporary_failure = bool(child.get("timed_out") or child.get("exit_code") == 127)
        classification = "BLOCKED_EXTERNAL" if temporary_failure else "FAILED"
        state["last_failure"] = {
            "timestamp": utc_now(),
            "classification": classification,
            "exit_code": child.get("exit_code"),
            "timed_out": child.get("timed_out"),
            "stderr_path": child.get("stderr_path"),
            "error_excerpt": child.get("stderr_tail"),
        }
    elif not parsed.get("parsed"):
        classification = "FAILED"
        state["last_failure"] = {
            "timestamp": utc_now(),
            "classification": classification,
            "reason": "missing structured ASTER_LOOP_RESULT",
        }
    else:
        classification = parsed.get("classification", parsed.get("status", "FAILED"))
        temporary_failure = False
    result = {
        "timestamp": utc_now(),
        "iteration": iteration,
        "attempt": attempt,
        "issue": selected.get("id"),
        "status": parsed.get("status", "FAILED"),
        "action": parsed.get("action", ""),
        "verification": parsed.get("verification", "FAIL"),
        "classification": classification,
        "child": child,
        "commit": commit_result,
        "before_commit": before["git"],
        "after": {"observations": observations_after, "git": git_snapshot()},
        "blocker": state.get("last_failure"),
    }
    write_json(iteration_dir / "result.json", result)
    update_queue_observation(queue, str(selected.get("id")), iteration, result, iteration_dir)
    state["last_result"] = result
    if not temporary_failure:
        state["iterations_completed"] = int(state.get("iterations_completed", 0)) + 1
    # A timeout or unavailable child is a resumable attempt, not permission to
    # advance the durable iteration counter.  The next controller launch will
    # reuse this iteration and its exact persisted prompt/evidence directory.
    state["in_progress"] = temporary_failure
    state["resume_required"] = temporary_failure
    next_prompt = parsed.get("next_prompt", "")
    state["child_suggested_next_prompt"] = next_prompt if parsed.get("parsed") else ""
    if next_prompt and parsed.get("parsed") and str(parsed.get("current_issue", "")) == str(selected.get("id")):
        state["next_prompt"] = next_prompt
        state["next_prompt_source"] = "child_structured_result"
    else:
        next_issue = choose_issue(queue)
        state["next_prompt"] = build_prompt(next_issue, observations_after) if next_issue else ""
        state["next_prompt_source"] = "controller_derived"
    state["history"].append(
        {
            "timestamp": result["timestamp"],
            "iteration": iteration,
            "attempt": attempt,
            "issue": selected.get("id"),
            "status": result["status"],
            "verification": result["verification"],
            "classification": classification,
            "evidence": str(iteration_dir),
            "resumable": temporary_failure,
        }
    )
    persist_state(state, evidence_root)
    render_reports(state, queue, observations_after, evidence_root=evidence_root)
    return result


@contextmanager
def controller_lock(evidence_root: Path) -> Iterator[None]:
    evidence_root.mkdir(parents=True, exist_ok=True)
    path = evidence_root / "controller.lock"
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another ASTER controller is running: {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def print_terminal() -> None:
    if PROGRESS_MD.exists():
        print(PROGRESS_MD.read_text(encoding="utf-8"), end="")


def run_controller(args: argparse.Namespace) -> int:
    evidence_root = Path(args.evidence_root).resolve()
    state = load_or_create_state(evidence_root)
    restore_resumable_attempt(state)
    with controller_lock(evidence_root):
        queue = current_queue()
        observations = observe(evidence_root)
        state["issue_workflow"] = classify_queue(queue)
        state["next_prompt"] = state.get("next_prompt") or (
            build_prompt(choose_issue(queue), observations) if choose_issue(queue) else ""
        )
        persist_state(state, evidence_root)
        selected_issue = choose_issue(queue)
        watch_mode = selected_issue is None and (
            args.watch_external
            or terminal_queue_requires_external_watch(state, queue, observations)
        )
        if args.dry_run:
            render_reports(state, queue, observations, evidence_root=evidence_root)
            print_terminal()
            return 0
        if watch_mode:
            # The watch iteration must observe a clean pre-render tree.  Its
            # own report commit then keeps the dashboard synchronized without
            # manufacturing a DIRTY observation at controller startup.
            return run_external_watch(args, state, evidence_root)
        if selected_issue is None:
            mark_terminal_queue_state(state)
            render_reports(state, queue, observations, evidence_root=evidence_root)
            result = {
                "status": "READY" if state.get("overall_ready") else "NOT_READY",
                "verification": "PARTIAL",
                "action": state.get("current_action"),
                "issue": None,
            }
            print("ASTER_LOOP_RESULT")
            print(f"STATUS={result['status']}")
            print(f"ISSUES_REMAINING={state.get('issues_remaining', '?')}")
            print("CURRENT_ISSUE=NONE")
            print(f"ACTION={result['action']}")
            print(f"VERIFICATION={result['verification']}")
            print("NEXT_PROMPT_BEGIN")
            print(state.get("next_prompt") or "Controller will derive the next prompt from persisted queue and evidence.")
            print("NEXT_PROMPT_END")
            print_terminal()
            return 0
        completed_this_run = 0
        while completed_this_run < args.max_iterations and not state.get("overall_ready"):
            try:
                result = iteration_once(
                    state,
                    evidence_root=evidence_root,
                    execute_codex=not args.no_codex,
                    timeout_seconds=args.timeout_seconds,
                    auto_commit=args.auto_commit,
                )
            except KeyboardInterrupt:
                state["last_failure"] = {
                    "timestamp": utc_now(),
                    "classification": "INTERRUPTED",
                    "reason": "controller interrupted; current iteration remains resumable",
                }
                persist_state(state, evidence_root)
                raise
            completed_this_run += 1
            result = result if isinstance(result, dict) else {}
            parsed = result.get("child", {}).get("parsed", {}) if result.get("child") else result
            print("ASTER_LOOP_RESULT")
            print(f"STATUS={result.get('status', 'FAILED')}")
            print(f"ISSUES_REMAINING={state.get('issues_remaining', '?')}")
            print(f"CURRENT_ISSUE={result.get('issue') or state.get('current_issue') or 'NONE'}")
            print(f"ACTION={result.get('action') or state.get('current_action') or 'none'}")
            print(f"VERIFICATION={result.get('verification', 'FAIL')}")
            print("NEXT_PROMPT_BEGIN")
            print(state.get("next_prompt") or "Controller will derive the next prompt from persisted queue and evidence.")
            print("NEXT_PROMPT_END")
            # A temporary child failure is retryable, but only within the
            # caller's explicit bounded budget. The next attempt gets a new
            # evidence directory while preserving this iteration and prompt.
            if result.get("status") == "READY" and state.get("overall_ready"):
                break
        print_terminal()
    return 0


def self_test() -> int:
    fixture = """noise\nASTER_LOOP_RESULT\nSTATUS=NOT_READY\nISSUES_REMAINING=2\nCURRENT_ISSUE=ASTER-006\nACTION=probe\nVERIFICATION=PARTIAL\nNEXT_PROMPT_BEGIN\nDo the next exact task.\nNEXT_PROMPT_END\n"""
    parsed = parse_result_text(fixture)
    assert parsed["parsed"] and parsed["current_issue"] == "ASTER-006"
    assert parsed["next_prompt"] == "Do the next exact task."
    assert parse_result_text("no structured output")["status"] == "FAILED"
    terminal_state = {
        "in_progress": True,
        "resume_required": True,
        "child_suggested_next_prompt": "retry historical timeout",
        "next_prompt": "retry historical timeout",
    }
    mark_terminal_queue_state(terminal_state)
    assert terminal_state["in_progress"] is False
    assert terminal_state["resume_required"] is False
    assert terminal_state["child_suggested_next_prompt"] == ""
    watch_fixture_state = {
        "overall_ready": False,
        "next_prompt": "wait for the next candidate",
    }
    watch_fixture_queue = {"issues": [{"id": "ASTER-006", "status": "BLOCKED_EXTERNAL"}]}
    watch_fixture_observations = {
        "benchmark": {"fail": 1, "partial": 1},
        "provenance": {"status": "PASS"},
        "runtime": {"status": "PASS"},
        "git": {"status": "CLEAN_SYNCED"},
    }
    assert terminal_queue_requires_external_watch(
        watch_fixture_state, watch_fixture_queue, watch_fixture_observations
    )
    watch_prompt = build_external_watch_prompt(
        DEFAULT_EXTERNAL_CODING_CANDIDATE, watch_fixture_observations, "/evidence/watch"
    )
    assert DEFAULT_EXTERNAL_CODING_CANDIDATE in watch_prompt
    assert "both unchanged coder cases" in watch_prompt
    candidate_prompt = build_candidate_focus_prompt(
        DEFAULT_EXTERNAL_CODING_CANDIDATE, watch_fixture_observations, "/evidence/candidate"
    )
    assert "medium-coding-04" in candidate_prompt and "base case" in candidate_prompt
    child_result = parse_result_text(
        "ASTER_LOOP_RESULT\nSTATUS=NOT_READY\nISSUES_REMAINING=0\nCURRENT_ISSUE=NONE\n"
        "ACTION=run genericity\nVERIFICATION=PARTIAL\nNEXT_PROMPT_BEGIN\n"
        "Run the generic coding subset with the admitted candidate.\nNEXT_PROMPT_END\n"
    )
    apply_child_handoff(watch_fixture_state, child_result, candidate_prompt)
    assert watch_fixture_state["controller_phase"] == "CANDIDATE_FOCUSED_GATE"
    assert watch_fixture_state["next_prompt"] == "Run the generic coding subset with the admitted candidate."
    codex_cli = resolve_codex_cli()
    verified = [
        candidate
        for candidate in codex_cli["candidates"]
        if candidate.get("status") == "VERIFIED"
    ]
    expected = max(
        verified,
        key=lambda candidate: tuple(candidate["version_sort_key"]),
    )
    assert codex_cli["path"] == expected["path"]
    assert codex_cli["version"] == expected["version"]
    print(
        "aster_next_prompt self-test: PASS "
        f"(Codex {codex_cli['version']} at {codex_cli['path']})"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("run", "status", "self-test"), default="run")
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE_ROOT))
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_CHILD_TIMEOUT)
    parser.add_argument("--no-codex", action="store_true", help="observe and persist without starting a child")
    parser.add_argument("--dry-run", action="store_true", help="render current state without executing a child")
    parser.add_argument("--auto-commit", action="store_true", help="commit and push only a child result with objective PASS")
    parser.add_argument("--watch-external", action="store_true", help="watch a terminal queue for a bounded external candidate opportunity")
    parser.add_argument("--watch-interval-seconds", type=float, default=DEFAULT_WATCH_INTERVAL_SECONDS)
    parser.add_argument("--max-watch-cycles", type=int, default=DEFAULT_MAX_WATCH_CYCLES)
    parser.add_argument("--max-download-seconds", type=int, default=DEFAULT_MAX_DOWNLOAD_SECONDS)
    parser.add_argument("--max-estimated-download-hours", type=float, default=DEFAULT_MAX_ESTIMATED_DOWNLOAD_HOURS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "self-test":
        return self_test()
    if args.max_iterations < 1 or args.max_iterations > MAX_ITERATIONS_PER_INVOCATION:
        raise SystemExit(f"--max-iterations must be between 1 and {MAX_ITERATIONS_PER_INVOCATION}")
    if args.timeout_seconds < 1 or args.timeout_seconds > 3600:
        raise SystemExit("--timeout-seconds must be between 1 and 3600")
    if args.watch_interval_seconds < 0 or args.watch_interval_seconds > 86400:
        raise SystemExit("--watch-interval-seconds must be between 0 and 86400")
    if args.max_watch_cycles < 1 or args.max_watch_cycles > 1000:
        raise SystemExit("--max-watch-cycles must be between 1 and 1000")
    if args.max_download_seconds < 1 or args.max_download_seconds > DEFAULT_MAX_DOWNLOAD_SECONDS:
        raise SystemExit(
            f"--max-download-seconds must be between 1 and {DEFAULT_MAX_DOWNLOAD_SECONDS}"
        )
    if args.max_estimated_download_hours <= 0 or args.max_estimated_download_hours > 24:
        raise SystemExit("--max-estimated-download-hours must be greater than 0 and at most 24")
    if args.command == "status":
        print_terminal()
        return 0
    return run_controller(args)


if __name__ == "__main__":
    raise SystemExit(main())
