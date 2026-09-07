"""Exercise release control flow with real Git state and stubbed heavy checks."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


REPOSITORY = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "clean": (":", True, 0),
    "tracked_change": ("printf changed > tracked_source.txt", True, 1),
    "staged_change": (
        "printf changed > tracked_source.txt\ngit add tracked_source.txt",
        True,
        1,
    ),
    "untracked_change": ("printf unexpected > unexpected.txt", True, 1),
    "head_change": (
        "printf changed > tracked_source.txt\ngit add tracked_source.txt\n"
        "git commit -qm synthetic-head-change\n"
        "git update-ref refs/remotes/origin/main HEAD",
        True,
        1,
    ),
    "permissive_tracked_change": ("printf changed > tracked_source.txt", False, 0),
}


def executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def check_scenario(name: str, mutation: str, require_clean: bool, expected: int) -> None:
    with tempfile.TemporaryDirectory(prefix="work-station-release-guard-") as temporary:
        root = Path(temporary)
        fixture = root / "repository"
        scripts = fixture / "scripts"
        scripts.mkdir(parents=True)
        binaries = root / "bin"
        binaries.mkdir()
        python = fixture / "backend" / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        executable(python, "#!/usr/bin/env bash\nexit 0\n")
        (fixture / ".gitignore").write_text("backend/.venv/\n", encoding="utf-8")
        (fixture / "tracked_source.txt").write_text("original\n", encoding="utf-8")
        for script in ("release_check.sh", "artifact_scan.sh"):
            shutil.copyfile(REPOSITORY / "scripts" / script, scripts / script)
            (scripts / script).chmod(0o755)
        for script in (
            "mobile_check.sh",
            "desktop_check.sh",
            "verify_service_units.sh",
            "test_remote_gateway_check.sh",
            "postgres_integration_check.sh",
        ):
            executable(scripts / script, "#!/usr/bin/env bash\nexit 0\n")
        executable(
            scripts / "security_audit.sh",
            "#!/usr/bin/env bash\nset -euo pipefail\n" + mutation + "\n",
        )
        for binary in ("npm", "pg_dump", "pg_restore", "curl", "jq", "systemd-analyze"):
            executable(binaries / binary, "#!/usr/bin/env bash\nexit 0\n")
        environment = {
            **os.environ,
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }

        def git(*arguments: str) -> None:
            subprocess.run(
                ["git", *arguments], cwd=fixture, env=environment, check=True,
                capture_output=True, text=True, timeout=10,
            )

        git("init", "--initial-branch=main", "--quiet")
        git("config", "user.name", "Release Guard Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("add", ".")
        git("commit", "--quiet", "-m", "synthetic initial fixture")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        arguments = ["bash", str(scripts / "release_check.sh")]
        if require_clean:
            arguments.append("--require-clean")
        result = subprocess.run(
            arguments, cwd=fixture, env=environment, capture_output=True,
            text=True, timeout=30,
        )
        assert result.returncode == expected, (
            f"{name}: expected exit {expected}, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert ("WORK STATION release validation passed" in result.stdout) == (expected == 0)
        if name == "head_change":
            assert "Git HEAD change during validation" in result.stderr
        elif require_clean and expected != 0:
            assert "clean Git worktree after validation" in result.stderr
        print(f"release source guard: {name} passed")


if __name__ == "__main__":
    for scenario, parameters in SCENARIOS.items():
        check_scenario(scenario, *parameters)
