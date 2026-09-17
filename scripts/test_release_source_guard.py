"""Exercise release control flow with real Git state and stubbed heavy checks."""

import os
from pathlib import Path
import re
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


def test_desktop_check_does_not_require_unavailable_ripgrep():
    source = (REPOSITORY / "scripts" / "desktop_check.sh").read_text(encoding="utf-8")
    assert not any(re.search(r"\brg\b", line) for line in source.splitlines())
    assert "grep -Eq" in source
    assert "grep -rlZ" in source


def _artifact_home_scan_function() -> str:
    source = (REPOSITORY / "scripts" / "desktop_check.sh").read_text(encoding="utf-8")
    start = source.index("artifact_home_path_scan() {")
    end = source.index("\nartifact_home_scan_report=", start)
    return source[start:end]


def _run_artifact_home_scan(root: Path, targets: list[Path]) -> int:
    harness = root / "scan-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -u\n"
        + _artifact_home_scan_function()
        + "\nset +e\nartifact_home_path_scan \"$1\" \"${@:2}\"\nstatus=$?\n"
        + "printf 'status=%s\\n' \"$status\"\nexit 0\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    report = root / "scan-report.txt"
    result = subprocess.run(
        ["bash", str(harness), str(report), *(str(path) for path in targets)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip().split("=", 1)[1])


def test_desktop_artifact_scan_recurses_and_fails_closed(tmp_path):
    appdir = tmp_path / "WORK STATION.AppDir"
    deb_root = tmp_path / "deb-root"
    (appdir / "usr" / "share").mkdir(parents=True)
    (deb_root / "usr" / "share").mkdir(parents=True)
    appimage = tmp_path / "WORK STATION.AppImage"
    binary = tmp_path / "work-station-desktop"
    appimage.write_bytes(b"clean artifact\n")
    binary.write_bytes(b"clean binary\n")
    (appdir / "usr" / "share" / "safe.txt").write_text("safe\n", encoding="utf-8")
    (deb_root / "usr" / "share" / "safe.txt").write_text("safe\n", encoding="utf-8")
    targets = [appimage, binary, appdir, deb_root]

    assert _run_artifact_home_scan(tmp_path, targets) == 1

    (appdir / "usr" / "share" / "nested.txt").write_text("/home/developer\n", encoding="utf-8")
    assert _run_artifact_home_scan(tmp_path, targets) == 0

    (appdir / "usr" / "share" / "nested.txt").write_text("safe\n", encoding="utf-8")
    assert _run_artifact_home_scan(tmp_path, [*targets, tmp_path / "missing-artifact"]) == 2


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
