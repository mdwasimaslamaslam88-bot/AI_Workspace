#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
_NODE_VERSION = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)\Z")
_NODE_GATES = frozenset(
    {"backend", "web", "mobile", "desktop", "browser_e2e", "security", "release"}
)
_RUST_GATES = frozenset({"backend", "desktop", "release"})
_RG_GATES = frozenset(
    {"mobile", "desktop", "browser_e2e", "security", "release"}
)

from app.maintenance import (  # noqa: E402
    REQUIRED_UPDATE_GATES,
    SelfUpdateError,
    SelfUpdateManager,
    ValidationGate,
)


def _manager(state_root: Path) -> SelfUpdateManager:
    return SelfUpdateManager(REPOSITORY_ROOT, state_root)


def _safe_existing_paths(*candidates: Path) -> tuple[Path, ...]:
    return tuple(
        path.resolve()
        for path in candidates
        if path.exists() and not path.is_symlink()
    )


def _trusted_node_runtime() -> Path:
    roots = []
    selected = shutil.which("node")
    if selected is not None:
        roots.append(Path(selected).resolve().parent.parent)
    nvm_root = Path.home() / ".nvm/versions/node"
    if nvm_root.is_dir() and not nvm_root.is_symlink():
        roots.extend(
            candidate
            for candidate in nvm_root.iterdir()
            if candidate.is_dir() and not candidate.is_symlink()
        )
    admitted: list[tuple[tuple[int, int, int], Path]] = []
    for root in roots:
        node = root / "bin/node"
        npm = root / "bin/npm"
        if not node.is_file() or not npm.is_file():
            continue
        completed = subprocess.run(
            (str(node), "--version"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
        match = _NODE_VERSION.fullmatch(completed.stdout.strip())
        if completed.returncode == 0 and match is not None and int(match[1]) >= 24:
            admitted.append((tuple(int(part) for part in match.groups()), root.resolve()))
    if not admitted:
        raise ValueError("a verified Node.js 24+ runtime is unavailable")
    return max(admitted)[1]


def _runtime_paths(gate_name: str) -> tuple[Path, ...]:
    runtime_root = Path.home() / "AI_Workspace_Runtimes"
    paths: tuple[Path, ...] = (
        (_trusted_node_runtime(),)
        if gate_name in _NODE_GATES
        else ()
    )
    rg_binary = shutil.which("rg")
    if gate_name in _RG_GATES and rg_binary is not None:
        paths += _safe_existing_paths(Path(rg_binary).resolve().parent)
    if gate_name == "browser_e2e":
        paths += _safe_existing_paths(runtime_root / "playwright")
    if gate_name in _RUST_GATES:
        paths += _safe_existing_paths(
            Path.home() / ".cargo/bin",
            Path.home() / ".rustup",
        )
    if gate_name in {"desktop", "release"}:
        paths += _safe_existing_paths(
            Path.home() / ".cargo/registry",
            Path.home() / ".cache/tauri",
            runtime_root / "tauri-sysroot",
        )
    return paths


def _gate_command(gate_name: str, gate_script: Path) -> tuple[str, ...]:
    path_parts = []
    if gate_name in _NODE_GATES:
        path_parts.append(str(_trusted_node_runtime() / "bin"))
    if gate_name in _RUST_GATES:
        path_parts.append(str(Path.home() / ".cargo/bin"))
    rg_binary = shutil.which("rg")
    if gate_name in _RG_GATES and rg_binary is not None:
        path_parts.append(str(Path(rg_binary).resolve().parent))
    path_parts.extend(
        ("/usr/local/bin", "/usr/local/sbin", "/usr/bin", "/usr/sbin", "/bin", "/sbin")
    )
    environment = (f"PATH={':'.join(path_parts)}",)
    if gate_name == "browser_e2e":
        playwright = Path.home() / "AI_Workspace_Runtimes/playwright"
        if not playwright.is_dir() or playwright.is_symlink():
            raise ValueError("the private Playwright runtime is unavailable")
        return (
            "/usr/bin/env",
            *environment,
            f"WORK_STATION_PLAYWRIGHT_BROWSERS_PATH={playwright.resolve()}",
            "/usr/bin/bash",
            str(gate_script),
            gate_name,
        )
    return ("/usr/bin/env", *environment, "/usr/bin/bash", str(gate_script), gate_name)


def _mandatory_gates() -> tuple[ValidationGate, ...]:
    gate_script = (REPOSITORY_ROOT / "scripts/self_update_gate.sh").resolve(strict=True)
    long_running = {"database", "desktop", "browser_e2e", "release"}
    networked = {"security", "release"}
    return tuple(
        ValidationGate(
            name=name,
            command=_gate_command(name, gate_script),
            timeout_seconds=7_200 if name in long_running else 3_600,
            network_access=name in networked,
            read_only_paths=_runtime_paths(name),
        )
        for name in sorted(REQUIRED_UPDATE_GATES)
    )


def _public_state(manager: SelfUpdateManager) -> dict[str, object]:
    state = manager.state()
    return {
        "configured": True,
        "status": state.status.value,
        "version": state.version,
        "candidate_commit": state.candidate_commit,
        "checkpoint_ready": state.checkpoint_id is not None,
        "rollback_ready": state.checkpoint_id is not None,
        "gates": [
            {"name": gate.name, "passed": gate.passed}
            for gate in state.gate_results
        ],
        "failure_code": state.failure_code,
    }


def _health_probe(url: str):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/api/v1/health/ready"
    ):
        raise ValueError("health URL must be the exact loopback readiness endpoint")

    def probe(_release: Path) -> bool:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status == 200
        except OSError:
            return False

    return probe


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate, activate, or roll back a private WORK STATION release.",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        required=True,
        help="Absolute owner-only self-update state directory outside source.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--candidate", required=True)
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--database-backup", type=Path)
    verify = commands.add_parser("verify-checkpoint")
    verify.add_argument("checkpoint")
    activate = commands.add_parser("activate")
    activate.add_argument("--confirm", choices=("UPDATE",), required=True)
    commands.add_parser("cancel")
    health = commands.add_parser("health")
    health.add_argument(
        "--url",
        default="http://127.0.0.1:8000/api/v1/health/ready",
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        manager = _manager(arguments.state_root)
        if arguments.command == "prepare":
            manager.prepare(
                candidate_ref=arguments.candidate,
                version=arguments.version,
                gates=_mandatory_gates(),
                database_backup=arguments.database_backup,
            )
        elif arguments.command == "verify-checkpoint":
            manager.verify_checkpoint(arguments.checkpoint)
        elif arguments.command == "activate":
            manager.activate_ready(user_confirmed=arguments.confirm == "UPDATE")
        elif arguments.command == "cancel":
            manager.cancel_ready()
        elif arguments.command == "health":
            manager.check_health_and_rollback(_health_probe(arguments.url))
        print(json.dumps(_public_state(manager), sort_keys=True))
    except (OSError, ValueError, SelfUpdateError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
