#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}\Z")


class WatchError(RuntimeError):
    pass


def _run(*command: str, timeout: int = 300) -> str:
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise WatchError("technology watcher command failed")
    return completed.stdout.strip()


def _version_at(ref: str) -> str:
    payload = json.loads(_run("/usr/bin/git", "show", f"{ref}:package.json"))
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise WatchError("candidate version metadata is invalid")
    return version


def watch(state_root: Path, remote: str, branch: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", remote):
        raise ValueError("remote name is invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", branch) or ".." in branch:
        raise ValueError("branch name is invalid")
    if not state_root.is_absolute():
        raise ValueError("technology watcher state root must be absolute")
    root_metadata = state_root.lstat()
    if (
        stat.S_ISLNK(root_metadata.st_mode)
        or not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_IMODE(root_metadata.st_mode) & 0o077
    ):
        raise ValueError("technology watcher state root is invalid")
    lock_path = state_root / "technology-watcher.lock"
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_descriptor, "a+b") as lock:
        lock_metadata = os.fstat(lock.fileno())
        if not stat.S_ISREG(lock_metadata.st_mode) or stat.S_IMODE(lock_metadata.st_mode) & 0o077:
            raise WatchError("technology watcher lock is unsafe")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _run("/usr/bin/git", "fetch", "--prune", remote, branch, timeout=600)
        current = _run("/usr/bin/git", "rev-parse", "HEAD")
        candidate_ref = f"{remote}/{branch}"
        candidate = _run("/usr/bin/git", "rev-parse", "--verify", f"{candidate_ref}^{{commit}}")
        if current == candidate:
            return "no_new_candidate"
        ancestor = subprocess.run(
            ("/usr/bin/git", "merge-base", "--is-ancestor", current, candidate),
            cwd=REPOSITORY_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        if ancestor.returncode != 0:
            raise WatchError("candidate is not a fast-forward descendant")
        completed = subprocess.run(
            (
                str(REPOSITORY_ROOT / "backend/.venv/bin/python"),
                str(REPOSITORY_ROOT / "scripts/self_update_tool.py"),
                "--state-root",
                str(state_root),
                "prepare",
                "--candidate",
                candidate_ref,
                "--version",
                _version_at(candidate_ref),
            ),
            cwd=REPOSITORY_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=24 * 60 * 60,
            check=False,
        )
        if completed.returncode != 0:
            raise WatchError("candidate validation failed")
        return "validated_candidate_ready"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a fast-forward candidate and validate it without activating production.",
    )
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    arguments = parser.parse_args()
    try:
        result = watch(arguments.state_root, arguments.remote, arguments.branch)
    except (OSError, ValueError, WatchError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
