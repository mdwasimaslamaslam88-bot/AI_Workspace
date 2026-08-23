#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
mobile_root="${repository_root}/apps/mobile"
native_mode="if-available"

usage() {
  cat <<'EOF'
Usage: ./scripts/mobile_check.sh [--require-native-android|--skip-native-android]

Runs the Expo client tests, typecheck/config validation, lint, Android/iOS
static exports, and Expo Doctor. When an Android SDK is configured through
WORK_STATION_ANDROID_SDK_ROOT, ANDROID_HOME, or ANDROID_SDK_ROOT, the default
also performs a fresh x86_64 native debug build in a disposable directory.

  --require-native-android  Fail unless the native Android build can run.
  --skip-native-android     Run only platform-independent mobile checks.
EOF
}

for argument in "$@"; do
  case "${argument}" in
    --require-native-android) native_mode="required" ;;
    --skip-native-android) native_mode="skip" ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown mobile check option: ${argument}" >&2; usage >&2; exit 2 ;;
  esac
done

cd "${repository_root}"
before_status="$(git status --porcelain=v1 --untracked-files=all)"

assert_repository_unchanged() {
  local after_status
  after_status="$(git status --porcelain=v1 --untracked-files=all)"
  if [[ "${after_status}" != "${before_status}" ]]; then
    echo "Mobile validation changed the repository worktree." >&2
    exit 1
  fi
}

npm run test --workspace @work-station/mobile
npm run typecheck --workspace @work-station/mobile
npm run lint --workspace @work-station/mobile
npm run build:static --workspace @work-station/mobile
(
  cd "${mobile_root}"
  "${repository_root}/node_modules/.bin/expo-doctor"
)

if [[ "${native_mode}" == "skip" ]]; then
  assert_repository_unchanged
  echo "native Android validation: explicitly skipped"
  exit 0
fi

android_sdk_root="${WORK_STATION_ANDROID_SDK_ROOT:-${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}}"
if [[ -z "${android_sdk_root}" ]]; then
  if [[ "${native_mode}" == "required" ]]; then
    echo "Native Android validation requires WORK_STATION_ANDROID_SDK_ROOT, ANDROID_HOME, or ANDROID_SDK_ROOT." >&2
    exit 1
  fi
  assert_repository_unchanged
  echo "native Android validation: skipped because no SDK root is configured"
  exit 0
fi
if [[ "${android_sdk_root}" != /* || ! -d "${android_sdk_root}" ]]; then
  echo "The configured Android SDK root must be an existing absolute directory." >&2
  exit 1
fi
for executable in java rsync unzip rg; do
  command -v "${executable}" >/dev/null
done

stage_parent="${TMPDIR:-/tmp}"
[[ "${stage_parent}" == /* && -d "${stage_parent}" ]]
native_stage_root="$(mktemp -d "${stage_parent%/}/work-station-mobile-native.XXXXXX")"

cleanup_native_stage() {
  if [[ -z "${native_stage_root:-}" || ! -e "${native_stage_root}" ]]; then
    return
  fi
  if [[ "$(dirname -- "${native_stage_root}")" != "${stage_parent%/}" || "$(basename -- "${native_stage_root}")" != work-station-mobile-native.* ]]; then
    echo "Refusing unexpected native mobile cleanup target." >&2
    return 1
  fi
  find "${native_stage_root}" -depth ! -type d -delete
  find "${native_stage_root}" -depth -type d -empty -delete
}
trap cleanup_native_stage EXIT

mkdir -p "${native_stage_root}/apps/mobile"
rsync -a \
  --exclude='.env*' \
  --exclude='.expo' \
  --exclude='android' \
  --exclude='dist' \
  --exclude='ios' \
  --exclude='node_modules' \
  --exclude='web-build' \
  "${mobile_root}/" "${native_stage_root}/apps/mobile/"
ln -s "${repository_root}/node_modules" "${native_stage_root}/node_modules"
ln -s "${repository_root}/shared" "${native_stage_root}/shared"

native_mobile_root="${native_stage_root}/apps/mobile"
(
  cd "${native_mobile_root}"
  CI=1 NODE_ENV=development \
    "${repository_root}/node_modules/.bin/expo" prebuild \
      --platform android --no-install
  ANDROID_HOME="${android_sdk_root}" \
  ANDROID_SDK_ROOT="${android_sdk_root}" \
  NODE_ENV=development \
    ./android/gradlew --no-daemon -p android \
      -PreactNativeArchitectures="${WORK_STATION_ANDROID_ARCHITECTURES:-x86_64}" \
      app:assembleDebug
)

apk_path="${native_mobile_root}/android/app/build/outputs/apk/debug/app-debug.apk"
[[ -s "${apk_path}" ]]
build_tools_version="$({
  find "${android_sdk_root}/build-tools" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null || true
} | sort -V | tail -n 1)"
aapt_path="${android_sdk_root}/build-tools/${build_tools_version}/aapt"
[[ -x "${aapt_path}" ]]
apk_badging="$("${aapt_path}" dump badging "${apk_path}")"
rg -q "^package: name='com\.workstation\.personalai'" <<<"${apk_badging}"

apk_scan_root="${native_stage_root}/apk-scan"
mkdir -p "${apk_scan_root}"
unzip -qq "${apk_path}" -d "${apk_scan_root}"
if rg -a -n --hidden --no-ignore \
  'USER_PROVISIONING_TOKEN_DIGEST|X-User-Provisioning-Token|\.ai_workspace_provisioning_token|-----BEGIN .*PRIVATE KEY-----' \
  "${apk_scan_root}" >/dev/null; then
  echo "The native Android artifact contains operator configuration or key material." >&2
  exit 1
fi

assert_repository_unchanged

echo "mobile validation: tests, static Android/iOS bundles, Expo Doctor, native Android package identity, and artifact safety passed"
