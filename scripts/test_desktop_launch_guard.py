"""Check the native launch guard with real process groups and synthetic X11 data."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile


REPOSITORY = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "owned_visible_window": 0,
    "unrelated_existing_window": 1,
    "missing_window_pid": 1,
    "unmapped_owned_window": 1,
    "window_owner_changes": 1,
}


def executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def check_scenario(name: str, expected: int) -> None:
    source = (REPOSITORY / "scripts" / "desktop_check.sh").read_text(encoding="utf-8")
    # Execute the production functions, without invoking the heavy packaging path.
    functions = source[
        source.index('desktop_pid=""'):source.index('launch_and_verify "production-binary"')
    ]
    with tempfile.TemporaryDirectory(prefix="work-station-desktop-guard-") as temporary:
        root = Path(temporary)
        binaries = root / "bin"
        binaries.mkdir()
        executable(
            binaries / "xwininfo",
            "#!/usr/bin/env bash\n"
            "if [[ $1 == -root ]]; then\n"
            "  echo '0x123 \"WORK STATION\": (\"work-station-desktop\" \"Work-station-desktop\")'\n"
            "elif [[ $GUARD_SCENARIO == unmapped_owned_window ]]; then\n"
            "  echo 'Map State: IsUnMapped'\n"
            "else echo 'Map State: IsViewable'; fi\n",
        )
        executable(
            binaries / "xprop",
            "#!/usr/bin/env bash\n"
            "count=$(cat \"$GUARD_ROOT/queries\" 2>/dev/null || echo 0)\n"
            "count=$((count + 1)); echo \"$count\" > \"$GUARD_ROOT/queries\"\n"
            "if [[ $GUARD_SCENARIO == missing_window_pid ]]; then exit 0; fi\n"
            "if [[ $GUARD_SCENARIO == unrelated_existing_window ]] || "
            "{ [[ $GUARD_SCENARIO == window_owner_changes ]] && ((count > 1)); }; then\n"
            "  echo \"_NET_WM_PID(CARDINAL) = $GUARD_FOREIGN_PID\"\n"
            "else\n"
            "  pid=$(cat \"$GUARD_ROOT/launched_pid\" 2>/dev/null || true)\n"
            "  echo \"_NET_WM_PID(CARDINAL) = $pid\"\n"
            "fi\n",
        )
        executable(
            root / "non_gui_process",
            '#!/usr/bin/env bash\nprintf "%s\\n" "$$" > "$GUARD_ROOT/launched_pid"\n'
            "exec /usr/bin/sleep 30\n",
        )
        harness = root / "harness.sh"
        executable(
            harness,
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"work_root={shlex.quote(str(root))}\n"
            "cleanup_work_root() { :; }\n"
            "sleep() { /usr/bin/sleep 0.01; }\n"
            + functions
            + f"\nlaunch_and_verify synthetic {shlex.quote(str(root / 'non_gui_process'))}\n",
        )
        result = subprocess.run(
            ["bash", str(harness)],
            env={
                **os.environ,
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "GUARD_ROOT": str(root),
                "GUARD_SCENARIO": name,
                "GUARD_FOREIGN_PID": str(os.getpid()),
            },
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == expected, (
            f"{name}: expected exit {expected}, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        if name == "owned_visible_window":
            assert int((root / "queries").read_text()) >= 21
        elif name == "window_owner_changes":
            assert "lost its owned visible native window" in result.stderr
        else:
            assert "did not open its expected native window" in result.stderr
        print(f"desktop launch guard: {name} passed")


if __name__ == "__main__":
    for scenario, expected_result in SCENARIOS.items():
        check_scenario(scenario, expected_result)
