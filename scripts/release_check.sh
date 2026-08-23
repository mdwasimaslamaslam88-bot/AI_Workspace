#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
backend_python="${repository_root}/backend/.venv/bin/python"
with_runtime=false
require_clean=false

for argument in "$@"; do
  case "${argument}" in
    --with-runtime) with_runtime=true ;;
    --require-clean) require_clean=true ;;
    *) echo "Unknown release option: ${argument}" >&2; exit 2 ;;
  esac
done

cd "${repository_root}"
git diff --check
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
  .venv/bin/alembic check
  "${backend_python}" tests/db/run_postgres_integration.py
  "${backend_python}" -m compileall -q app scripts tests
)

npm run test:web
npm run test --workspace @work-station/mobile
npm run typecheck
npm run lint
npm run build:web
npm run build:static --workspace @work-station/mobile
"${script_directory}/desktop_check.sh"

"${script_directory}/verify_service_units.sh"
"${script_directory}/test_remote_gateway_check.sh"
bash -n scripts/*.sh
"${backend_python}" scripts/backup_tool.py --help >/dev/null

"${script_directory}/security_audit.sh"
if [[ "${with_runtime}" == true ]]; then
  "${script_directory}/runtime_e2e.sh"
fi

git diff --check
if [[ "${require_clean}" == true ]]; then
  "${script_directory}/artifact_scan.sh"
  [[ "$(git rev-parse HEAD)" == "$(git rev-parse main)" ]]
  [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]]
  [[ "$(git rev-list --left-right --count main...origin/main)" == $'0\t0' ]]
fi
echo "WORK STATION release validation passed"
