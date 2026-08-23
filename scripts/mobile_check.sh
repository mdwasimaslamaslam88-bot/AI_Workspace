#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
mobile_root="${repository_root}/apps/mobile"
native_mode="if-available"
output_apk=""

usage() {
  cat <<'EOF'
Usage: ./scripts/mobile_check.sh [--require-native-android|--skip-native-android]
                                 [--output-apk ABSOLUTE_PATH]

Runs the Expo client tests, typecheck/config validation, lint, Android/iOS
static exports, and Expo Doctor. When an Android SDK is configured through
WORK_STATION_ANDROID_SDK_ROOT, ANDROID_HOME, or ANDROID_SDK_ROOT, the default
also performs a fresh x86_64 native debug build in a disposable directory.
When --output-apk is provided, it instead produces a fresh ARM64 optimized
release variant signed with Android's standard debug certificate for private
owner sideloading, validates it, and copies it to the requested new path.

  --require-native-android  Fail unless the native Android build can run.
  --skip-native-android     Run only platform-independent mobile checks.
  --output-apk PATH         Retain a validated owner-sideload release APK.
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --require-native-android) native_mode="required" ;;
    --skip-native-android) native_mode="skip" ;;
    --output-apk)
      if [[ "$#" -lt 2 ]]; then
        echo "--output-apk requires an absolute path." >&2
        exit 2
      fi
      output_apk="$2"
      native_mode="required"
      shift
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown mobile check option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ -n "${output_apk}" ]]; then
  native_mode="required"
  if [[ "${output_apk}" != /* || ! -d "$(dirname -- "${output_apk}")" ]]; then
    echo "The APK output must be an absolute path in an existing directory." >&2
    exit 1
  fi
  if [[ -e "${output_apk}" ]]; then
    echo "Refusing to overwrite an existing APK output path." >&2
    exit 1
  fi
fi

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
for executable in java rsync unzip zip rg; do
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
build_variant="debug"
gradle_task="app:assembleDebug"
node_environment="development"
android_architectures="${WORK_STATION_ANDROID_ARCHITECTURES:-x86_64}"
apk_path="${native_mobile_root}/android/app/build/outputs/apk/debug/app-debug.apk"
if [[ -n "${output_apk}" ]]; then
  build_variant="release"
  gradle_task="app:assembleRelease"
  node_environment="production"
  android_architectures="${WORK_STATION_ANDROID_ARCHITECTURES:-arm64-v8a}"
  apk_path="${native_mobile_root}/android/app/build/outputs/apk/release/app-release.apk"
fi
(
  cd "${native_mobile_root}"
  CI=1 NODE_ENV="${node_environment}" \
    "${repository_root}/node_modules/.bin/expo" prebuild \
      --platform android --no-install
  ANDROID_HOME="${android_sdk_root}" \
  ANDROID_SDK_ROOT="${android_sdk_root}" \
  NODE_ENV="${node_environment}" \
    ./android/gradlew --no-daemon -p android \
      -PreactNativeArchitectures="${android_architectures}" \
      "${gradle_task}"
)

[[ -s "${apk_path}" ]]
build_tools_version="$({
  find "${android_sdk_root}/build-tools" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null || true
} | sort -V | tail -n 1)"
aapt_path="${android_sdk_root}/build-tools/${build_tools_version}/aapt"
zipalign_path="${android_sdk_root}/build-tools/${build_tools_version}/zipalign"
apksigner_path="${android_sdk_root}/build-tools/${build_tools_version}/apksigner"
[[ -x "${aapt_path}" && -x "${zipalign_path}" && -x "${apksigner_path}" ]]
apk_badging="$("${aapt_path}" dump badging "${apk_path}")"
rg -q "^package: name='com\.workstation\.personalai'.*versionName='0\.1\.0'" <<<"${apk_badging}"
rg -q "^application-label:'WORK STATION'" <<<"${apk_badging}"
unzip -tqq "${apk_path}"
"${zipalign_path}" -c -p 4 "${apk_path}"
signer_output="$("${apksigner_path}" verify --verbose --print-certs "${apk_path}")"
rg -q '^Verifies$' <<<"${signer_output}"
if [[ "${build_variant}" == "release" ]]; then
  rg -q 'certificate DN: .*Android Debug' <<<"${signer_output}"
fi

apk_scan_root="${native_stage_root}/apk-scan"
mkdir -p "${apk_scan_root}"
unzip -qq "${apk_path}" -d "${apk_scan_root}"
if rg -a -n --hidden --no-ignore \
  'USER_PROVISIONING_TOKEN_DIGEST|X-User-Provisioning-Token|\.ai_workspace_provisioning_token|-----BEGIN .*PRIVATE KEY-----' \
  "${apk_scan_root}" >/dev/null; then
  echo "The native Android artifact contains operator configuration or key material." >&2
  exit 1
fi
mapfile -d '' apk_path_matches < <(
  rg -a -l -0 --hidden --no-ignore \
    '/home/|/tmp/work-station-mobile-native\.' \
    "${apk_scan_root}" || true
)
if [[ "${#apk_path_matches[@]}" -gt 0 && -n "${output_apk}" ]]; then
  sanitized_members=()
  for matched_file in "${apk_path_matches[@]}"; do
    relative_member="${matched_file#"${apk_scan_root}/"}"
    if [[ "${relative_member}" != lib/arm64-v8a/*.so ]]; then
      echo "An unexpected APK member contains a build-machine path: ${relative_member}" >&2
      exit 1
    fi
    # Equal-length replacements preserve ELF offsets while removing local
    # compiler roots from release libraries.
    perl -0pi -e 's{/home/}{build:}g; s{/tmp/}{temp:}g' "${matched_file}"
    sanitized_members+=("${relative_member}")
  done

  unsigned_apk="${native_stage_root}/sanitized-unsigned.apk"
  aligned_apk="${native_stage_root}/sanitized-aligned.apk"
  sanitized_apk="${native_stage_root}/sanitized-signed.apk"
  install -m 0644 "${apk_path}" "${unsigned_apk}"
  zip -q -d "${unsigned_apk}" \
    'META-INF/*.SF' 'META-INF/*.RSA' 'META-INF/*.DSA' 'META-INF/MANIFEST.MF' \
    >/dev/null || true
  (
    cd "${apk_scan_root}"
    zip -q -0 -u "${unsigned_apk}" "${sanitized_members[@]}"
  )
  "${zipalign_path}" -f -p 4 "${unsigned_apk}" "${aligned_apk}"
  debug_keystore="${native_mobile_root}/android/app/debug.keystore"
  [[ -f "${debug_keystore}" ]]
  WORK_STATION_DEBUG_STORE_PASSWORD=android \
  WORK_STATION_DEBUG_KEY_PASSWORD=android \
    "${apksigner_path}" sign \
      --ks "${debug_keystore}" \
      --ks-key-alias androiddebugkey \
      --ks-pass env:WORK_STATION_DEBUG_STORE_PASSWORD \
      --key-pass env:WORK_STATION_DEBUG_KEY_PASSWORD \
      --out "${sanitized_apk}" \
      "${aligned_apk}"
  install -m 0644 "${sanitized_apk}" "${apk_path}"

  unzip -tqq "${apk_path}"
  "${zipalign_path}" -c -p 4 "${apk_path}"
  signer_output="$("${apksigner_path}" verify --verbose --print-certs "${apk_path}")"
  rg -q '^Verifies$' <<<"${signer_output}"
  rg -q 'certificate DN: .*Android Debug' <<<"${signer_output}"
  apk_badging="$("${aapt_path}" dump badging "${apk_path}")"
  rg -q "^package: name='com\.workstation\.personalai'.*versionName='0\.1\.0'" <<<"${apk_badging}"
  rg -q "^application-label:'WORK STATION'" <<<"${apk_badging}"

  find "${apk_scan_root}" -depth ! -type d -delete
  find "${apk_scan_root}" -depth -type d -empty -delete
  mkdir -p "${apk_scan_root}"
  unzip -qq "${apk_path}" -d "${apk_scan_root}"
  if rg -a -q --hidden --no-ignore \
    '/home/|/tmp/work-station-mobile-native\.' \
    "${apk_scan_root}"; then
    echo "The sanitized Android artifact still contains a build-machine path." >&2
    exit 1
  fi
fi

if [[ -n "${output_apk}" ]]; then
  install -m 0644 "${apk_path}" "${output_apk}"
fi

assert_repository_unchanged

echo "mobile validation: tests, static Android/iOS bundles, Expo Doctor, native Android ${build_variant} package identity, signing, alignment, and artifact safety passed"
