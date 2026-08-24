#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
native_root="${HOME}/AI_Workspace_Runtimes/tauri-sysroot/root"
cargo_binary="${HOME}/.cargo/bin/cargo"
appimage_plugin="${HOME}/.cache/tauri/linuxdeploy-plugin-appimage.AppImage"
appimage_runtime="${HOME}/.cache/tauri/runtime-x86_64"
appimage_runtime_sha256="1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf"
launch_mode="if-available"

usage() {
  cat <<'EOF'
Usage: ./scripts/desktop_check.sh [--require-launch|--skip-launch]

Builds and validates the Linux Tauri executable, AppImage, and Debian package.
On an available X11 session, the default also launches both distributable forms
and verifies their native windows without capturing screen content.

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

if [[ ! -f "${appimage_runtime}" ]] ||
  [[ "$(sha256sum "${appimage_runtime}" | cut -d ' ' -f 1)" != "${appimage_runtime_sha256}" ]]; then
  echo "The pinned Tauri AppImage runtime is missing or invalid." >&2
  exit 1
fi
export LDAI_RUNTIME_FILE="${appimage_runtime}"

rust_remap_flags="--remap-path-prefix=${repository_root}=workspace --remap-path-prefix=${HOME}/.cargo=cargo-registry --remap-path-prefix=${native_root}=tauri-sysroot"
export RUSTFLAGS="${RUSTFLAGS:+${RUSTFLAGS} }${rust_remap_flags}"

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
npm run build:binary --workspace @work-station/desktop

product_version="$(node -e 'process.stdout.write(require("./apps/desktop/package.json").version)' 2>/dev/null)"
target_root="${repository_root}/apps/desktop/src-tauri/target/release"
binary="${repository_root}/apps/desktop/src-tauri/target/release/work-station-desktop"
package="${target_root}/bundle/deb/WORK STATION_${product_version}_amd64.deb"
appimage="${target_root}/bundle/appimage/WORK STATION_${product_version}_amd64.AppImage"
appdir="${target_root}/bundle/appimage/WORK STATION.AppDir"

work_parent="${TMPDIR:-/tmp}"
[[ "${work_parent}" == /* && -d "${work_parent}" ]]
work_root="$(mktemp -d "${work_parent%/}/work-station-desktop-check.XXXXXX")"

cleanup_work_root() {
  if [[ -n "${work_root:-}" && -e "${work_root}" ]]; then
    if [[ "$(dirname -- "${work_root}")" != "${work_parent%/}" || "$(basename -- "${work_root}")" != work-station-desktop-check.* ]]; then
      echo "Refusing unexpected desktop check cleanup target." >&2
      return 1
    fi
    find "${work_root}" -depth ! -type d -delete
    find "${work_root}" -depth -type d -empty -delete
  fi
}
trap cleanup_work_root EXIT

[[ -x "${binary}" ]]
install -m 0755 "${binary}" "${work_root}/unpatched-binary"

# Tauri reuses its generated AppDir between incremental builds. Remove only
# the obsolete, tool-generated filename from pre-validation experiments.
stale_appstream_metadata="${appdir}/usr/share/metainfo/WORK STATION.appdata.xml"
if [[ -f "${stale_appstream_metadata}" ]]; then
  find "${stale_appstream_metadata}" -maxdepth 0 -type f -delete
fi

# AppImage deployment must inspect host library paths rather than the compile
# sysroot. The pkg-config metadata remains pinned to the private sysroot.
(
  unset PKG_CONFIG_SYSROOT_DIR LIBRARY_PATH LD_LIBRARY_PATH
  cd "${repository_root}/apps/desktop"
  "${repository_root}/node_modules/.bin/tauri" bundle --bundles appimage
)
[[ -s "${appimage}" && -d "${appdir}" ]]
if [[ ! -x "${appimage_plugin}" ]]; then
  echo "Tauri did not install its required AppImage packaging plugin." >&2
  exit 1
fi

install -d -m 0755 "${appdir}/usr/share/metainfo"
appstreamcli validate \
  "${repository_root}/apps/desktop/src-tauri/linux/com.workstation.personalai.metainfo.xml" \
  >/dev/null
install -m 0644 \
  "${repository_root}/apps/desktop/src-tauri/linux/com.workstation.personalai.metainfo.xml" \
  "${appdir}/usr/share/metainfo/com.workstation.personalai.metainfo.xml"

# Keep compiled packages free of developer-machine paths. The replacement is
# deliberately equal-length so embedded library offsets remain unchanged.
while IFS= read -r -d '' bundled_file; do
  perl -0pi -e 's{/home/}{build:}g' "${bundled_file}"
done < <(rg -l -0 -a -F '/home/' "${appdir}" || true)

(
  cd "${work_root}"
  ARCH=x86_64 "${appimage_plugin}" --appimage-extract-and-run --appdir="${appdir}"
)
mapfile -d '' repacked_appimages < <(
  find "${work_root}" -maxdepth 1 -type f -name '*.AppImage' -print0
)
if [[ "${#repacked_appimages[@]}" -ne 1 ]]; then
  echo "Expected exactly one repacked AppImage." >&2
  exit 1
fi
install -m 0755 "${repacked_appimages[0]}" "${appimage}"

# Restore the pristine release executable before Tauri applies its Debian
# package marker, then build the second distributable.
install -m 0755 "${work_root}/unpatched-binary" "${binary}"
perl -0pi -e 's{/home/}{build:}g' "${binary}"
(
  cd "${repository_root}/apps/desktop"
  "${repository_root}/node_modules/.bin/tauri" bundle --bundles deb
)

[[ -s "${package}" && -x "${binary}" && -s "${appimage}" && -x "${appimage}" ]]
file "${appimage}" | rg -q 'ELF .* executable'
dpkg-deb --info "${package}" >/dev/null
dpkg-deb --extract "${package}" "${work_root}/deb-root"
if rg -a -q -F '/home/' \
  "${appimage}" "${binary}" "${appdir}" "${work_root}/deb-root"; then
  echo "Desktop artifacts contain a developer-machine home path." >&2
  exit 1
fi
if ldd "${binary}" | rg -q "not found"; then
  echo "The desktop executable has unresolved shared libraries." >&2
  exit 1
fi

if [[ "${launch_mode}" == "skip" ]]; then
  echo "desktop launch smoke: explicitly skipped"
  cleanup_work_root
  trap - EXIT
  echo "desktop validation: Rust tests, production binary, AppImage, and Debian package passed"
  exit 0
fi

if [[ -z "${DISPLAY:-}" ]] || ! command -v xwininfo >/dev/null; then
  if [[ "${launch_mode}" == "required" ]]; then
    echo "Desktop launch validation requires an X11 DISPLAY and xwininfo." >&2
    exit 1
  fi
  echo "desktop launch smoke: skipped because no inspectable X11 session is available"
  cleanup_work_root
  trap - EXIT
  echo "desktop validation: Rust tests, production binary, AppImage, and Debian package passed"
  exit 0
fi

for process_executable in /proc/[0-9]*/exe; do
  if [[ "$(readlink -f -- "${process_executable}" 2>/dev/null || true)" == "${binary}" ]]; then
    echo "Desktop launch validation refuses to interfere with an existing WORK STATION process." >&2
    exit 1
  fi
done

desktop_pid=""
desktop_pgid=""

desktop_pid_is_owned() {
  [[ -n "${desktop_pgid:-}" ]] && kill -0 -- "-${desktop_pgid}" 2>/dev/null
}

cleanup_desktop_launch() {
  if desktop_pid_is_owned; then
    kill -TERM -- "-${desktop_pgid}" 2>/dev/null || true
    for _attempt in {1..20}; do
      if ! desktop_pid_is_owned; then
        break
      fi
      sleep 0.1
    done
    if desktop_pid_is_owned; then
      kill -KILL -- "-${desktop_pgid}" 2>/dev/null || true
    fi
  fi
  if [[ -n "${desktop_pid:-}" ]]; then
    wait "${desktop_pid}" 2>/dev/null || true
    desktop_pid=""
    desktop_pgid=""
  fi
}
trap 'cleanup_desktop_launch; cleanup_work_root' EXIT

launch_and_verify() {
  local label="$1"
  shift
  # xwininfo can inspect X11 windows only. On mixed Wayland/X11 sessions GTK
  # otherwise prefers Wayland, leaving this smoke blind to a healthy window.
  setsid env GDK_BACKEND=x11 "$@" >"${work_root}/${label}.log" 2>&1 &
  desktop_pid="$!"
  desktop_pgid="$(ps -o pgid= -p "${desktop_pid}" | tr -d ' ')"
  if [[ -z "${desktop_pgid}" || "${desktop_pgid}" != "${desktop_pid}" ]]; then
    echo "Could not establish an isolated process group for ${label}." >&2
    return 1
  fi
  local window_found=false
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
    echo "${label} did not open its expected native window." >&2
    return 1
  fi
  cleanup_desktop_launch
}

launch_and_verify "production-binary" "${binary}"
launch_and_verify "appimage" env APPIMAGE_EXTRACT_AND_RUN=1 "${appimage}"

cleanup_work_root
trap - EXIT
echo "desktop launch smoke: production and AppImage WORK STATION windows opened and closed cleanly"
echo "desktop validation: Rust tests, production binary, AppImage, Debian package, and launch passed"
