#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
native_root="${HOME}/AI_Workspace_Runtimes/tauri-sysroot/root"
cargo_binary="${HOME}/.cargo/bin/cargo"
launch_mode="if-available"

usage() {
  cat <<'EOF'
Usage: ./scripts/desktop_check.sh [--require-launch|--skip-launch]

Builds and validates the Linux Tauri package. On an available X11 session, the
default also launches the production executable and verifies its native window
without capturing screen content.

  --require-launch  Fail unless the native-window smoke can run.
  --skip-launch     Validate tests, build, linkage, and package only.
EOF
}

for argument in "$@"; do
  case "${argument}" in
    --require-launch) launch_mode="required" ;;
    --skip-launch) launch_mode="skip" ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown desktop check option: ${argument}" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -x "${cargo_binary}" ]]; then
  echo "Rust is required for desktop validation." >&2
  exit 1
fi
export PATH="${HOME}/.cargo/bin:${PATH}"

if ! pkg-config --exists webkit2gtk-4.1 2>/dev/null; then
  if [[ ! -d "${native_root}/usr/lib/x86_64-linux-gnu/pkgconfig" ]]; then
    echo "Tauri system libraries or the documented user-owned sysroot are required." >&2
    exit 1
  fi
  export PATH="${native_root}/usr/bin:${PATH}"
  export PKG_CONFIG_SYSROOT_DIR="${native_root}"
  export PKG_CONFIG_PATH="${native_root}/usr/lib/x86_64-linux-gnu/pkgconfig:${native_root}/usr/share/pkgconfig"
  export LIBRARY_PATH="${native_root}/usr/lib/x86_64-linux-gnu"
  export LD_LIBRARY_PATH="${native_root}/usr/lib/x86_64-linux-gnu"
fi

npm run typecheck --workspace @work-station/desktop
(
  cd "${repository_root}/apps/desktop/src-tauri"
  "${cargo_binary}" test --locked
)
npm run build --workspace @work-station/desktop

package="${repository_root}/apps/desktop/src-tauri/target/release/bundle/deb/WORK STATION_0.1.0_amd64.deb"
binary="${repository_root}/apps/desktop/src-tauri/target/release/work-station-desktop"
[[ -s "${package}" && -x "${binary}" ]]
if ldd "${binary}" | rg -q "not found"; then
  echo "The desktop executable has unresolved shared libraries." >&2
  exit 1
fi

if [[ "${launch_mode}" == "skip" ]]; then
  echo "desktop launch smoke: explicitly skipped"
  echo "desktop validation: Rust tests, production binary, and Debian package passed"
  exit 0
fi

if [[ -z "${DISPLAY:-}" ]] || ! command -v xwininfo >/dev/null; then
  if [[ "${launch_mode}" == "required" ]]; then
    echo "Desktop launch validation requires an X11 DISPLAY and xwininfo." >&2
    exit 1
  fi
  echo "desktop launch smoke: skipped because no inspectable X11 session is available"
  echo "desktop validation: Rust tests, production binary, and Debian package passed"
  exit 0
fi

for process_executable in /proc/[0-9]*/exe; do
  if [[ "$(readlink -f -- "${process_executable}" 2>/dev/null || true)" == "${binary}" ]]; then
    echo "Desktop launch validation refuses to interfere with an existing WORK STATION process." >&2
    exit 1
  fi
done

launch_parent="${TMPDIR:-/tmp}"
[[ "${launch_parent}" == /* && -d "${launch_parent}" ]]
launch_root="$(mktemp -d "${launch_parent%/}/work-station-desktop-launch.XXXXXX")"
desktop_pid=""

desktop_pid_is_owned() {
  [[ -n "${desktop_pid:-}" && -e "/proc/${desktop_pid}/exe" ]] &&
    [[ "$(readlink -f -- "/proc/${desktop_pid}/exe")" == "${binary}" ]]
}

cleanup_desktop_launch() {
  if desktop_pid_is_owned; then
    kill -TERM "${desktop_pid}" 2>/dev/null || true
    for _attempt in {1..20}; do
      if ! desktop_pid_is_owned; then
        break
      fi
      sleep 0.1
    done
    if desktop_pid_is_owned; then
      kill -KILL "${desktop_pid}" 2>/dev/null || true
    fi
  fi
  if [[ -n "${desktop_pid:-}" ]]; then
    wait "${desktop_pid}" 2>/dev/null || true
    desktop_pid=""
  fi
  if [[ -n "${launch_root:-}" && -e "${launch_root}" ]]; then
    if [[ "$(dirname -- "${launch_root}")" != "${launch_parent%/}" || "$(basename -- "${launch_root}")" != work-station-desktop-launch.* ]]; then
      echo "Refusing unexpected desktop launch cleanup target." >&2
      return 1
    fi
    find "${launch_root}" -depth ! -type d -delete
    find "${launch_root}" -depth -type d -empty -delete
  fi
}
trap cleanup_desktop_launch EXIT

"${binary}" >"${launch_root}/desktop.log" 2>&1 &
desktop_pid="$!"
window_found=false
for _attempt in {1..60}; do
  if ! desktop_pid_is_owned; then
    break
  fi
  window_tree="$(xwininfo -root -tree 2>/dev/null || true)"
  if rg -q '"WORK STATION".*"work-station-desktop"' <<<"${window_tree}"; then
    window_found=true
    break
  fi
  sleep 0.25
done
if [[ "${window_found}" != true ]]; then
  echo "The packaged desktop executable did not open its expected native window." >&2
  exit 1
fi

cleanup_desktop_launch
trap - EXIT
echo "desktop launch smoke: packaged native WORK STATION window opened and closed cleanly"
echo "desktop validation: Rust tests, production binary, Debian package, and launch passed"
