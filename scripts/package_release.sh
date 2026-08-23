#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
output_directory="${HOME}/Desktop/Work_Station_Releases"
windows_artifact_directory=""

usage() {
  cat <<'EOF'
Usage: ./scripts/package_release.sh --windows-artifact-dir ABSOLUTE_PATH
                                    [--output-dir ABSOLUTE_PATH]

Runs the complete runtime-enabled release gate, builds Linux and Android
packages, validates real Windows artifacts produced by the repository's
Windows workflow, and atomically publishes a checksummed release directory.
The output directory must not already exist.
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --windows-artifact-dir)
      [[ "$#" -ge 2 ]] || { usage >&2; exit 2; }
      windows_artifact_directory="$2"
      shift
      ;;
    --output-dir)
      [[ "$#" -ge 2 ]] || { usage >&2; exit 2; }
      output_directory="$2"
      shift
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown package option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "${output_directory}" != /* || -e "${output_directory}" ]]; then
  echo "The absolute release output path must not already exist." >&2
  exit 1
fi
output_parent="$(dirname -- "${output_directory}")"
[[ -d "${output_parent}" ]]
if [[ -z "${windows_artifact_directory}" ||
      "${windows_artifact_directory}" != /* ||
      ! -d "${windows_artifact_directory}" ]]; then
  echo "A real Windows workflow artifact directory is required." >&2
  exit 1
fi

cd "${repository_root}"
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]]
[[ "$(git rev-parse HEAD)" == "$(git rev-parse main)" ]]
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]]
[[ "$(git rev-list --left-right --count main...origin/main)" == $'0\t0' ]]

for executable in file jq sha256sum unzip 7z rg; do
  command -v "${executable}" >/dev/null
done

stage_root="$(mktemp -d "${output_parent%/}/.work-station-release.XXXXXX")"
chmod 700 "${stage_root}"
cleanup_stage() {
  if [[ -z "${stage_root:-}" || ! -e "${stage_root}" ]]; then
    return
  fi
  if [[ "$(dirname -- "${stage_root}")" != "${output_parent}" ||
        "$(basename -- "${stage_root}")" != .work-station-release.* ]]; then
    echo "Refusing unexpected release staging cleanup target." >&2
    return 1
  fi
  find "${stage_root}" -depth ! -type d -delete
  find "${stage_root}" -depth -type d -empty -delete
}
trap cleanup_stage EXIT

android_artifact="${stage_root}/Work_Station_Android.apk"
"${script_directory}/release_check.sh" \
  --with-runtime \
  --require-clean \
  --require-native-android \
  --require-desktop-launch \
  --android-output-apk "${android_artifact}"

product_version="$(node -e 'process.stdout.write(require("./package.json").version)')"
git_commit="$(git rev-parse HEAD)"
build_timestamp="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
desktop_bundle_root="${repository_root}/apps/desktop/src-tauri/target/release/bundle"
linux_appimage="${desktop_bundle_root}/appimage/WORK STATION_${product_version}_amd64.AppImage"
linux_deb="${desktop_bundle_root}/deb/WORK STATION_${product_version}_amd64.deb"
[[ -s "${linux_appimage}" && -x "${linux_appimage}" && -s "${linux_deb}" ]]
install -m 0755 "${linux_appimage}" "${stage_root}/Work_Station_Ubuntu.AppImage"
install -m 0644 "${linux_deb}" "${stage_root}/Work_Station_Ubuntu.deb"

windows_setup_source="${windows_artifact_directory}/Work_Station_Windows_Setup.exe"
windows_binary_source="${windows_artifact_directory}/Work_Station_Windows.exe"
windows_checksums_source="${windows_artifact_directory}/Work_Station_Windows.sha256"
[[ -s "${windows_setup_source}" && -s "${windows_binary_source}" && -s "${windows_checksums_source}" ]]
(
  cd "${windows_artifact_directory}"
  sha256sum --check --strict "$(basename -- "${windows_checksums_source}")"
)
for windows_artifact in "${windows_setup_source}" "${windows_binary_source}"; do
  file "${windows_artifact}" | rg -q 'PE32\+.*x86-64'
done
7z t "${windows_setup_source}" >/dev/null
windows_scan_root="${stage_root}/.windows-scan"
install -d -m 0700 "${windows_scan_root}"
7z x "-o${windows_scan_root}" "${windows_setup_source}" >/dev/null
if rg -a -q --hidden --no-ignore \
  'USER_PROVISIONING_TOKEN_DIGEST|X-User-Provisioning-Token|\.ai_workspace_provisioning_token|-----BEGIN .*PRIVATE KEY-----|/home/|/tmp/|[A-Za-z]:\\Users\\' \
  "${windows_setup_source}" "${windows_binary_source}" "${windows_scan_root}"; then
  echo "Windows artifacts failed the local secret or path scan." >&2
  exit 1
fi
find "${windows_scan_root}" -depth ! -type d -delete
find "${windows_scan_root}" -depth -type d -empty -delete
install -m 0644 "${windows_setup_source}" "${stage_root}/Work_Station_Windows_Setup.exe"
install -m 0644 "${windows_binary_source}" "${stage_root}/Work_Station_Windows.exe"

binary_artifacts=(
  Work_Station_Ubuntu.AppImage
  Work_Station_Ubuntu.deb
  Work_Station_Windows_Setup.exe
  Work_Station_Windows.exe
  Work_Station_Android.apk
)
(
  cd "${stage_root}"
  sha256sum "${binary_artifacts[@]}" >checksums.sha256
  sha256sum Work_Station_Ubuntu.AppImage Work_Station_Ubuntu.deb >Work_Station_Ubuntu.sha256
  sha256sum Work_Station_Windows_Setup.exe Work_Station_Windows.exe >Work_Station_Windows.sha256
  sha256sum Work_Station_Android.apk >Work_Station_Android.sha256
)

artifact_json='[]'
for artifact in "${binary_artifacts[@]}"; do
  platform="unknown"
  case "${artifact}" in
    Work_Station_Ubuntu.*) platform="ubuntu-x86_64" ;;
    Work_Station_Windows*) platform="windows-x86_64" ;;
    Work_Station_Android.apk) platform="android-arm64-v8a" ;;
  esac
  artifact_hash="$(sha256sum "${stage_root}/${artifact}" | cut -d ' ' -f 1)"
  artifact_json="$(jq \
    --arg platform "${platform}" \
    --arg artifact "${artifact}" \
    --arg sha256 "${artifact_hash}" \
    '. + [{platform:$platform, artifact:$artifact, sha256:$sha256, build_status:"passed", tests_status:"passed"}]' \
    <<<"${artifact_json}")"
done
jq -n \
  --arg product_name "WORK STATION" \
  --arg version "${product_version}" \
  --arg git_commit "${git_commit}" \
  --arg build_timestamp "${build_timestamp}" \
  --arg tests_status "passed" \
  --argjson artifacts "${artifact_json}" \
  '{product_name:$product_name, version:$version, git_commit:$git_commit, build_timestamp:$build_timestamp, tests_status:$tests_status, artifacts:$artifacts}' \
  >"${stage_root}/release-manifest.json"

printf '%s\n' \
  'WORK STATION private owner release' \
  '' \
  "Version: ${product_version}" \
  "Git commit: ${git_commit}" \
  '' \
  'Ubuntu: AppImage and Debian package, both launch-validated.' \
  'Windows: NSIS installer and standalone executable, unsigned; process-launch validated on Windows CI.' \
  'Android: optimized ARM64 owner-sideload APK signed with the standard Android debug certificate.' \
  'Web/PWA: served by the authenticated WORK STATION backend and is not duplicated in this folder.' \
  '' \
  'Verify all files with: sha256sum --check checksums.sha256' \
  'No client artifact contains a backend credential.' \
  >"${stage_root}/README.txt"

if rg -a -q --hidden --no-ignore \
  'USER_PROVISIONING_TOKEN_DIGEST|X-User-Provisioning-Token|\.ai_workspace_provisioning_token|-----BEGIN .*PRIVATE KEY-----|/home/|/tmp/work-station' \
  "${stage_root}"; then
  echo "Release folder failed its final secret or path scan." >&2
  exit 1
fi
(
  cd "${stage_root}"
  sha256sum --check --strict checksums.sha256
)
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]]
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]]

mv -- "${stage_root}" "${output_directory}"
stage_root=""
trap - EXIT
echo "WORK STATION distribution release published: ${output_directory}"
