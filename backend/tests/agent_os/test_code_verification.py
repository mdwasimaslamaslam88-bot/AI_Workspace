from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from app.agent_os.code_verification import (
    IndependentCodeVerifier,
    TrustedCodeVerificationProfile,
    _run_sandboxed,
)


def _profile() -> TrustedCodeVerificationProfile:
    return TrustedCodeVerificationProfile(
        profile_id="python-clamp-v1",
        language="python",
        artifact_filename="artifact.py",
        verifier_files=(("verify.py", "assert True\n"),),
        commands=(
            ("/usr/bin/python3", "-I", "-m", "py_compile", "artifact.py"),
            ("/usr/bin/python3", "verify.py"),
        ),
        expected_stdout="PASS",
    )


def test_verifier_hashes_and_executes_the_untouched_original_artifact():
    artifact = "def clamp(value, low, high):\n    return max(low, min(high, value))\n"
    seen: list[str] = []

    def runner(command: tuple[str, ...], root: Path, stdin: str | None, timeout: int):
        assert stdin is None
        assert timeout == 20
        seen.append((root / "artifact.py").read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(command, 0, "PASS\n", "")

    result = IndependentCodeVerifier(runner).verify(
        _profile(),
        f"```python\n{artifact}```",
    )

    assert result.passed is True
    assert result.original_artifact_sha256 == hashlib.sha256(artifact.encode()).hexdigest()
    assert seen == [artifact, artifact]
    assert len(result.evidence) == 2


def test_verifier_never_repairs_and_detects_verifier_side_mutation():
    artifact = "def value():\n    return 1\n"

    def mutating_runner(command: tuple[str, ...], root: Path, _stdin: str | None, _timeout: int):
        (root / "artifact.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "PASS", "")

    result = IndependentCodeVerifier(mutating_runner).verify(_profile(), artifact)

    assert result.passed is False
    assert result.failure_code == "original_artifact_mutated"
    assert result.original_artifact_sha256 == hashlib.sha256(artifact.encode()).hexdigest()


def test_verifier_rejects_unsafe_code_before_execution():
    runner = pytest.fail
    result = IndependentCodeVerifier(runner).verify(
        _profile(),
        "import subprocess\nsubprocess.run(['id'])",
    )

    assert result.passed is False
    assert result.static_safety_passed is False
    assert result.failure_code == "static_safety_rejection"


def test_trusted_profile_rejects_relative_or_shell_commands():
    with pytest.raises(ValueError, match="fixed absolute executables"):
        TrustedCodeVerificationProfile(
            profile_id="unsafe",
            language="python",
            artifact_filename="artifact.py",
            verifier_files=(),
            commands=(("sh", "-c", "arbitrary"),),
        )


def test_default_runner_uses_a_networkless_read_only_container(tmp_path):
    completed = subprocess.CompletedProcess(("python3",), 0, "", "")
    with patch(
        "app.agent_os.code_verification.subprocess.run",
        return_value=completed,
    ) as run:
        result = _run_sandboxed(
            ("/usr/bin/python3", "artifact.py"),
            tmp_path,
            None,
            20,
        )

    assert result is completed
    command = run.call_args.args[0]
    assert command[:3] == ("/usr/bin/docker", "run", "--rm")
    assert "--pull=never" in command
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert f"--mount=type=bind,src={tmp_path},dst=/input,readonly" in command
    assert any(item.startswith("--tmpfs=/workspace:") for item in command)
