from __future__ import annotations

from pathlib import Path
import stat
import subprocess
from unittest.mock import patch

import pytest

from app.maintenance.self_update import (
    REQUIRED_UPDATE_GATES,
    SelfUpdateError,
    SelfUpdateManager,
    UpdateStatus,
    ValidationGate,
    _run_gate_sandboxed,
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("/usr/bin/git", *arguments),
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir(mode=0o700)
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "Update Test")
    _git(root, "config", "user.email", "update@example.invalid")
    (root / ".gitignore").write_text(".env\n", encoding="utf-8")
    (root / "application.txt").write_text("version one\n", encoding="utf-8")
    (root / ".env").write_text("DATABASE_URL=private-value\n", encoding="utf-8")
    (root / "backend/app/ai").mkdir(parents=True)
    (root / "backend/requirements.txt").write_text("httpx==1\n", encoding="utf-8")
    (root / "backend/app/ai/routing.py").write_text("ROUTE = 'local'\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "application.txt", "backend")
    _git(root, "commit", "-m", "version one")
    return root


def _state_root(tmp_path: Path) -> Path:
    root = tmp_path / "private-update-state"
    root.mkdir(mode=0o700)
    return root


def _passing_runner(gate: ValidationGate, candidate: Path):
    assert (candidate / ".git").exists()
    return subprocess.CompletedProcess(gate.command, 0, f"{gate.name} passed", "")


def _gates() -> tuple[ValidationGate, ...]:
    return tuple(
        ValidationGate(name, ("/usr/bin/true",))
        for name in sorted(REQUIRED_UPDATE_GATES)
    )


def test_checkpoint_is_complete_integrity_checked_and_encrypts_private_config(tmp_path: Path):
    repository = _repository(tmp_path)
    manager = SelfUpdateManager(repository, _state_root(tmp_path))

    checkpoint_id = manager.create_checkpoint()
    checkpoint = manager.checkpoint_root / checkpoint_id
    manifest = manager.verify_checkpoint(checkpoint_id)

    assert manifest["commit"] == _git(repository, "rev-parse", "HEAD")
    assert manifest["private_configuration"] is True
    assert b"private-value" not in (checkpoint / "private-config.enc").read_bytes()
    assert stat.S_IMODE((manager.state_root / "checkpoint.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((checkpoint / "source.bundle").stat().st_mode) == 0o600
    assert all(
        stat.S_IMODE(entry.stat().st_mode) == (0o700 if entry.is_dir() else 0o600)
        for entry in checkpoint.rglob("*")
    )

    bundle = checkpoint / "source.bundle"
    bundle.write_bytes(bundle.read_bytes() + b"tamper")
    with pytest.raises(SelfUpdateError, match="integrity"):
        manager.verify_checkpoint(checkpoint_id)


def test_checkpoint_checksum_index_is_authenticated(tmp_path: Path):
    repository = _repository(tmp_path)
    manager = SelfUpdateManager(repository, _state_root(tmp_path))
    checkpoint_id = manager.create_checkpoint()
    checksum_path = manager.checkpoint_root / checkpoint_id / "SHA256SUMS"
    checksums = checksum_path.read_text(encoding="ascii")
    replacement = ("0" if checksums[0] != "0" else "1") + checksums[1:]
    checksum_path.write_text(replacement, encoding="ascii")
    checksum_path.chmod(0o600)

    with pytest.raises(SelfUpdateError, match="authentication"):
        manager.verify_checkpoint(checkpoint_id)


def test_checkpoint_verification_rejects_directory_symlink(tmp_path: Path):
    repository = _repository(tmp_path)
    manager = SelfUpdateManager(repository, _state_root(tmp_path))
    checkpoint_id = manager.create_checkpoint()
    linked = manager.checkpoint_root / "linked-checkpoint"
    linked.symlink_to(manager.checkpoint_root / checkpoint_id, target_is_directory=True)

    with pytest.raises(SelfUpdateError, match="path is unsafe"):
        manager.verify_checkpoint(linked)


def test_prepare_fails_closed_and_never_marks_a_failed_candidate_ready(tmp_path: Path):
    repository = _repository(tmp_path)

    def failed_runner(gate: ValidationGate, _candidate: Path):
        return subprocess.CompletedProcess(gate.command, 9, "", "private failure detail")

    manager = SelfUpdateManager(repository, _state_root(tmp_path), gate_runner=failed_runner)
    state = manager.prepare(
        candidate_ref="HEAD",
        version="1.1.0",
        gates=_gates(),
    )

    assert state.status is UpdateStatus.FAILED
    assert state.failure_code == "gate_failed"
    assert manager.state().status is UpdateStatus.FAILED
    assert not manager.current_link.exists()
    serialized = manager.state_path.read_text(encoding="utf-8")
    assert "private failure detail" not in serialized

    retry_manager = SelfUpdateManager(
        repository,
        manager.state_root,
        gate_runner=_passing_runner,
    )
    retried = retry_manager.prepare(
        candidate_ref="HEAD",
        version="1.1.0",
        gates=_gates(),
    )
    assert retried.status is UpdateStatus.READY
    assert retried.candidate_directory != state.candidate_directory


def test_ready_activation_requires_owner_and_failed_health_rolls_back(tmp_path: Path):
    repository = _repository(tmp_path)
    manager = SelfUpdateManager(repository, _state_root(tmp_path), gate_runner=_passing_runner)
    gates = _gates()

    first = manager.prepare(candidate_ref="HEAD", version="1.0.0", gates=gates)
    assert first.status is UpdateStatus.READY
    with pytest.raises(SelfUpdateError, match="owner decision"):
        manager.activate_ready(user_confirmed=False)
    activated_first = manager.activate_ready(user_confirmed=True)
    first_release = manager.current_link.resolve(strict=True)
    assert activated_first.status is UpdateStatus.ACTIVATED
    assert (first_release / ".env").read_text(encoding="utf-8") == "DATABASE_URL=private-value\n"

    (repository / "application.txt").write_text("version two\n", encoding="utf-8")
    _git(repository, "add", "application.txt")
    _git(repository, "commit", "-m", "version two")
    second = manager.prepare(candidate_ref="HEAD", version="2.0.0", gates=gates)
    assert second.status is UpdateStatus.READY
    activated_second = manager.activate_ready(user_confirmed=True)
    assert activated_second.previous_release == str(first_release)
    assert manager.current_link.resolve(strict=True) != first_release

    rolled_back = manager.check_health_and_rollback(lambda _release: False)
    assert rolled_back.status is UpdateStatus.ROLLED_BACK
    assert rolled_back.failure_code == "post_activation_health_failed"
    assert manager.current_link.resolve(strict=True) == first_release


def test_update_state_root_refuses_group_or_world_access(tmp_path: Path):
    repository = _repository(tmp_path)
    state_root = tmp_path / "unsafe-state"
    state_root.mkdir(mode=0o755)

    with pytest.raises(SelfUpdateError, match="owner-only"):
        SelfUpdateManager(repository, state_root)


def test_default_gate_runner_uses_scoped_container_mounts(tmp_path: Path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    gate = ValidationGate(
        "source",
        ("/usr/bin/true",),
        read_only_paths=(runtime,),
    )
    completed = subprocess.CompletedProcess(gate.command, 0, "", "")
    with patch(
        "app.maintenance.self_update.subprocess.run",
        return_value=completed,
    ) as run:
        result = _run_gate_sandboxed(gate, candidate)

    assert result is completed
    command = run.call_args.args[0]
    assert command[:3] == ("/usr/bin/docker", "run", "--rm")
    assert "--pull=never" in command
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert (
        f"--mount=type=bind,src={candidate},dst={candidate}"
        in command
    )
    assert (
        f"--mount=type=bind,src={runtime},dst={runtime},readonly"
        in command
    )
    assert not any("docker.sock" in item for item in command)
