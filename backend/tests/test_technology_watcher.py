import importlib.util
import os
from pathlib import Path

import pytest


_TOOL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "technology_watcher.py"
_SPEC = importlib.util.spec_from_file_location("work_station_technology_watcher", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
watcher = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(watcher)


def test_watcher_does_not_validate_or_notify_when_remote_commit_is_unchanged(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    calls = []

    def run(*command, timeout=300):
        calls.append((command, timeout))
        if "fetch" in command:
            return ""
        return "a" * 40

    monkeypatch.setattr(watcher, "_run", run)

    assert watcher.watch(state_root, "origin", "main") == "no_new_candidate"
    assert len(calls) == 3
    assert all("self_update_tool.py" not in " ".join(call[0]) for call in calls)


def test_watcher_rejects_untrusted_remote_or_traversing_branch_before_fetch(tmp_path):
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="remote"):
        watcher.watch(state_root, "../origin", "main")
    with pytest.raises(ValueError, match="branch"):
        watcher.watch(state_root, "origin", "../main")


def test_watcher_rejects_permissive_state_root(tmp_path):
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o755)

    with pytest.raises(ValueError, match="state root is invalid"):
        watcher.watch(state_root, "origin", "main")


def test_watcher_rejects_permissive_existing_lock(tmp_path):
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    lock_path = state_root / "technology-watcher.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.close(descriptor)

    with pytest.raises(watcher.WatchError, match="lock is unsafe"):
        watcher.watch(state_root, "origin", "main")
