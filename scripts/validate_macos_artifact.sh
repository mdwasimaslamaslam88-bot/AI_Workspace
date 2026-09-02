#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
output_directory="${1:-}"

if [[ -z "${output_directory}" ]]; then
  echo "Usage: bash ./scripts/validate_macos_artifact.sh OUTPUT_DIRECTORY" >&2
  exit 2
fi
if [[ "${output_directory}" != /* ]]; then
  output_directory="${repository_root}/${output_directory}"
fi
if [[ -e "${output_directory}" ]]; then
  echo "Refusing to overwrite an existing macOS artifact directory." >&2
  exit 1
fi

release_root="${repository_root}/apps/desktop/src-tauri/target/release"
shopt -s nullglob
applications=("${release_root}/bundle/macos/"*.app)
images=("${release_root}/bundle/dmg/"*.dmg)
shopt -u nullglob
if [[ "${#applications[@]}" -ne 1 || "${#images[@]}" -ne 1 ]]; then
  echo "Expected exactly one macOS application and one DMG." >&2
  exit 1
fi

application="${applications[0]}"
disk_image="${images[0]}"
info_plist="${application}/Contents/Info.plist"
[[ -s "${info_plist}" ]]
bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "${info_plist}")"
bundle_name="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleName' "${info_plist}")"
bundle_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${info_plist}")"
executable_name="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "${info_plist}")"
[[ "${bundle_id}" == "com.workstation.personalai" ]]
[[ "${bundle_name}" == "WORK STATION" ]]
[[ "${bundle_version}" == "0.1.0" ]]

executable="${application}/Contents/MacOS/${executable_name}"
[[ -x "${executable}" ]]
file "${executable}" | grep -Eq 'Mach-O .* executable'
hdiutil verify "${disk_image}" >/dev/null

if grep -aEqr \
  'USER_PROVISIONING_TOKEN_DIGEST|X-User-Provisioning-Token|\.ai_workspace_provisioning_token|-----BEGIN .*PRIVATE KEY-----' \
  "${application}" "${disk_image}"; then
  echo "macOS artifacts contain operator configuration or private key material." >&2
  exit 1
fi
if grep -aEqr '/Users/runner/|/home/' "${application}" "${disk_image}"; then
  echo "macOS artifacts contain a build-machine filesystem path." >&2
  exit 1
fi

if ! codesign --display --verbose=1 "${application}" >/dev/null 2>&1; then
  echo "macOS artifact is not code-signed." >&2
  exit 1
fi
codesign --verify --deep --strict "${application}"
codesign --display --verbose=4 "${application}" 2>&1 | grep -E '^(Identifier|Format|Signature)='

launch_log="$(mktemp "${RUNNER_TEMP:-/tmp}/work-station-macos-launch.XXXXXX")"
"${executable}" >"${launch_log}" 2>&1 &
application_pid="$!"
cleanup_launch() {
  if kill -0 "${application_pid}" 2>/dev/null; then
    kill -TERM "${application_pid}" 2>/dev/null || true
  fi
  wait "${application_pid}" 2>/dev/null || true
  find "${launch_log}" -maxdepth 0 -type f -delete
}
trap cleanup_launch EXIT
sleep 12
if ! kill -0 "${application_pid}" 2>/dev/null; then
  echo "The macOS application exited during launch smoke." >&2
  exit 1
fi
cleanup_launch
trap - EXIT

install -d -m 0755 "${output_directory}"
ditto -c -k --sequesterRsrc --keepParent \
  "${application}" "${output_directory}/Work_Station_macOS.app.zip"
install -m 0644 "${disk_image}" "${output_directory}/Work_Station_macOS.dmg"
unzip -tq "${output_directory}/Work_Station_macOS.app.zip" >/dev/null
(
  cd "${output_directory}"
  shasum -a 256 Work_Station_macOS.app.zip Work_Station_macOS.dmg \
    >Work_Station_macOS.sha256
)

echo "macOS validation: bundle metadata, DMG integrity, secret/path scan, launch smoke, and checksums passed"
