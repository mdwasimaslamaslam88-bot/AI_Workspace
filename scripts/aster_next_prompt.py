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
from typing import Any, Iterator


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


def command_record(
    command: list[str],
    cwd: Path,
    *,
    timeout: float = 30,
    input_text: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        return {
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
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return {
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
    except OSError as error:
        return {
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
    files_result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", current],
        cwd=str(REPOSITORY_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    changed = [line.strip() for line in files_result.stdout.splitlines() if line.strip()]
    if changed and all(
        any(path.startswith(prefix) for prefix in REPORT_ONLY_PATH_PREFIXES)
        for path in changed
    ):
        return parent
    return current


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


def persist_state(state: dict[str, Any], evidence_root: Path) -> None:
    state["updated_at"] = utc_now()
    state["history"] = state.get("history", [])[-MAX_HISTORY:]
    write_json(evidence_root / "current_state.json", state)


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
    alternate_commits = (
        (report_tip_commit,)
        if isinstance(report_tip_commit, str) and report_tip_commit != current_commit
        else ()
    )
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

    attestation_path = None
    configured_release = status.get("release", {})
    if isinstance(configured_release, dict) and isinstance(configured_release.get("final_report_tip_attestation"), str):
        attestation_path = configured_release["final_report_tip_attestation"]
    attestation = read_json(Path(attestation_path), {}) if isinstance(attestation_path, str) else {}
    artifact_hashes = attestation.get("artifact_hashes_verified") if isinstance(attestation, dict) else None
    artifact_structural = isinstance(artifact_hashes, dict) and bool(artifact_hashes) and all(artifact_hashes.values())
    artifact_commit = attestation.get("final_report_commit") if isinstance(attestation, dict) else None
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
        },
        "historical_evidence_rejected": any(
            value == "HISTORICAL_PASS_REQUIRES_CURRENT_PROVENANCE"
            for value in component_statuses.values()
        ),
    }


def discover_runtime(
    evidence_root: Path, status: dict[str, Any], current_commit: str | None
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
    source_matches = bool(recorded_source and current_commit and recorded_source == current_commit)
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
    runtime = discover_runtime(evidence_root, status, source_commit)
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
    commit = benchmark.get("commit") or observations.get("git", {}).get("commit")
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
    last_result = state.get("last_result") or {}
    last_child = last_result.get("child") if isinstance(last_result, dict) else None
    if isinstance(last_child, dict) and isinstance(last_child.get("codex_resolution"), dict):
        state["last_codex_cli"] = last_child["codex_resolution"]
    # The report is generated from this observed source commit. A later
    # report-only commit is intentionally represented separately by the Git
    # history; this prevents self-referential commit claims.
    state["report_tip_commit"] = git.get("commit")
    current_issue = selected.get("id") if selected else None
    state.update(
        {
            "overall_ready": ready,
            "status": "READY" if ready else "NOT_READY",
            "readiness_reason": readiness_reason,
            "current_issue": current_issue,
            "current_action": (
                required_action(selected) if selected else "Run final comprehensive validation and release gate."
            ),
            "observations": observations,
            "issue_workflow": classify_queue(queue),
            "issues_remaining": len(local_unresolved),
            "issues_fixed": len(fixed),
            "issues_blocked_externally": len(external_blocked),
            "next_prompt": state.get("next_prompt")
            or (build_prompt(selected, observations) if selected else ""),
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
            "evidence_root": str(evidence_root),
            "issue_queue": str(QUEUE_JSON.relative_to(REPOSITORY_ROOT)),
            "controller_state": str(evidence_root / "current_state.json"),
            "report_generated_from_commit": git.get("commit"),
            "report_tip_commit": state.get("report_tip_commit") or git.get("commit"),
            "evidence_provenance": provenance,
            "controller": {
                "iteration": state.get("iteration", 0),
                "iterations_completed": state.get("iterations_completed", 0),
                "readiness_reason": readiness_reason,
                "current_action": state.get("current_action"),
                "last_successful_commit": state.get("last_successful_commit"),
                "last_codex_cli": state.get("last_codex_cli"),
                "last_result": last_result,
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
        f"Current source commit: `{git.get('commit', 'unknown')}`. Evidence root: `{evidence_root}`.",
        "",
        "## Current measured state",
        "",
        f"- Canonical benchmark: **{benchmark.get('score', 'unknown')}/100**, {benchmark.get('pass', '?')} PASS, {benchmark.get('partial', '?')} PARTIAL, {benchmark.get('fail', '?')} FAIL; mean {benchmark.get('mean', '?')}s, P95 {benchmark.get('p95', '?')}s.",
        f"- Issues: {len(local_unresolved)} locally unresolved, {len(fixed)} fixed/verified, {len(external_blocked)} externally blocked.",
        f"- Current issue/action: `{current_issue or 'NONE'}` — {state.get('current_action')}",
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
        "The issue queue and machine-readable controller state are authoritative. A child model response cannot mark an issue complete without objective evidence.",
        "",
        f"Next prompt is persisted at `{evidence_root / 'current_state.json'}` and is generated from the selected issue and observed evidence.",
        "",
    ]
    atomic_write(STATUS_MD, "\n".join(status_lines), mode=0o644)

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
        "voice": "PARTIAL: ASTER-028 preserves reproducible exact-WAV lexical variability." if any(
            isinstance(issue, dict) and issue.get("id") == "ASTER-028" and issue.get("status") not in {"VERIFIED", "CLOSED"}
            for issue in queue.get("issues", [])
        ) else str(status.get("acceptance", {}).get("voice", "UNKNOWN")),
        "performance_mean": benchmark.get("mean"),
        "performance_p95": benchmark.get("p95"),
        "git_status": git.get("status"),
        "current_commit": git.get("commit"),
        "application_source_commit": git.get("application_source_commit"),
        "last_successful_commit": state.get("last_successful_commit"),
        "codex_cli": state.get("last_codex_cli"),
        "report_tip_commit": state.get("report_tip_commit"),
        "last_artifact_verification": status.get("release", {}).get("final_report_tip_attestation"),
        "last_failure": state.get("last_failure"),
        "next_action": state.get("current_action"),
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
    if status not in {"READY", "NOT_READY", "BLOCKED", "FAILED"}:
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
        "classification": "BLOCKED_EXTERNAL" if status == "BLOCKED" else status,
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


def iteration_once(
    state: dict[str, Any], *, evidence_root: Path, execute_codex: bool, timeout_seconds: int, auto_commit: bool
) -> dict[str, Any]:
    queue = current_queue()
    observations_before = observe(evidence_root)
    selected = choose_issue(queue)
    if selected is None:
        state["iteration"] = max(int(state.get("iteration", 0)), int(state.get("iterations_completed", 0)))
        state["in_progress"] = False
        state["next_prompt"] = "Run final comprehensive validation and release gate; stop only after all evidence passes."
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
        render_reports(state, queue, observations, evidence_root=evidence_root)
        if args.dry_run:
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "self-test":
        return self_test()
    if args.max_iterations < 1 or args.max_iterations > MAX_ITERATIONS_PER_INVOCATION:
        raise SystemExit(f"--max-iterations must be between 1 and {MAX_ITERATIONS_PER_INVOCATION}")
    if args.timeout_seconds < 1 or args.timeout_seconds > 3600:
        raise SystemExit("--timeout-seconds must be between 1 and 3600")
    if args.command == "status":
        print_terminal()
        return 0
    return run_controller(args)


if __name__ == "__main__":
    raise SystemExit(main())
