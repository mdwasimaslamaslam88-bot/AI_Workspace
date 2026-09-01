#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
backend_python="${repository_root}/backend/.venv/bin/python"
with_runtime=false
require_clean=false
require_native_android=false
require_desktop_launch=false
android_output_apk=""

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --with-runtime) with_runtime=true ;;
    --require-clean) require_clean=true ;;
    --require-native-android) require_native_android=true ;;
    --require-desktop-launch) require_desktop_launch=true ;;
    --android-output-apk)
      if [[ "$#" -lt 2 ]]; then
        echo "--android-output-apk requires an absolute path." >&2
        exit 2
      fi
      android_output_apk="$2"
      require_native_android=true
      shift
      ;;
    *) echo "Unknown release option: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "${repository_root}"
git diff --check
npm run report:features
if [[ "${require_clean}" == true && -n "$(git status --short)" ]]; then
  echo "Release verification requires a clean Git worktree." >&2
  exit 1
fi

for executable in npm pg_dump pg_restore curl jq systemd-analyze; do
  command -v "${executable}" >/dev/null
done
[[ -x /usr/bin/ffmpeg && -x /usr/bin/ffprobe ]]
[[ -x "${backend_python}" ]]

(
  cd backend
  "${backend_python}" -m pytest -q
  "${backend_python}" -m alembic heads
  "${backend_python}" -m compileall -q app scripts tests
)

npm run test:web
npm run typecheck --workspace @work-station/shared
npm run typecheck --workspace @work-station/web
npm run lint --workspace @work-station/web
npm run build:web
mobile_arguments=()
if [[ "${require_native_android}" == true ]]; then
  mobile_arguments+=(--require-native-android)
fi
if [[ -n "${android_output_apk}" ]]; then
  mobile_arguments+=(--output-apk "${android_output_apk}")
fi
"${script_directory}/mobile_check.sh" "${mobile_arguments[@]}"
desktop_arguments=()
if [[ "${require_desktop_launch}" == true ]]; then
  desktop_arguments+=(--require-launch)
fi
"${script_directory}/desktop_check.sh" "${desktop_arguments[@]}"

"${script_directory}/verify_service_units.sh"
"${script_directory}/test_remote_gateway_check.sh"
bash -n scripts/*.sh
"${backend_python}" scripts/backup_tool.py --help >/dev/null

"${script_directory}/security_audit.sh"
if [[ "${with_runtime}" == true ]]; then
  "${script_directory}/postgres_integration_check.sh" --with-runtime --with-browser
else
  "${script_directory}/postgres_integration_check.sh"
fi

git diff --check
if [[ "${require_clean}" == true ]]; then
  "${script_directory}/artifact_scan.sh"
  [[ "$(git rev-parse HEAD)" == "$(git rev-parse main)" ]]
  [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]]
  [[ "$(git rev-list --left-right --count main...origin/main)" == $'0\t0' ]]
fi
echo "WORK STATION release validation passed"
