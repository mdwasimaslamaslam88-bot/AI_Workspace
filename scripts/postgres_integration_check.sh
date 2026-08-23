#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
backend_python="${repository_root}/backend/.venv/bin/python"
run_integration=true
run_runtime=false

usage() {
  cat <<'EOF'
Usage: ./scripts/postgres_integration_check.sh [--with-runtime|--runtime-only]

Starts a user-owned, loopback-only PostgreSQL cluster in a random temporary
directory, creates only the approved ai_workspace_test database, runs the real
integration suite, and removes the cluster. Runtime-only mode migrates the same
disposable database and runs the bounded real AI smokes.

  --with-runtime  Run PostgreSQL integration and real runtime E2E.
  --runtime-only  Run only migrated real runtime E2E.
EOF
}

for argument in "$@"; do
  case "${argument}" in
    --with-runtime) run_runtime=true ;;
    --runtime-only) run_integration=false; run_runtime=true ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown PostgreSQL check option: ${argument}" >&2; usage >&2; exit 2 ;;
  esac
done

cd "${repository_root}"
before_status="$(git status --porcelain=v1 --untracked-files=all)"

for executable in pg_config find mktemp; do
  command -v "${executable}" >/dev/null
done
[[ -x "${backend_python}" ]]
postgres_bindir="$(pg_config --bindir)"
[[ "${postgres_bindir}" == /* && -d "${postgres_bindir}" ]]
for executable in initdb pg_ctl createdb; do
  [[ -x "${postgres_bindir}/${executable}" ]]
done

cluster_parent="${TMPDIR:-/tmp}"
[[ "${cluster_parent}" == /* && -d "${cluster_parent}" ]]
cluster_parent="$(cd -- "${cluster_parent}" && pwd -P)"
cluster_root="$(mktemp -d "${cluster_parent%/}/work-station-postgres-integration.XXXXXX")"
cluster_data="${cluster_root}/data"
cluster_socket="${cluster_root}/socket"
cluster_log="${cluster_root}/postgres.log"
cluster_started=false

cleanup_cluster() {
  if [[ "${cluster_started}" == true && -x "${postgres_bindir}/pg_ctl" && -d "${cluster_data}" ]]; then
    "${postgres_bindir}/pg_ctl" -D "${cluster_data}" -m fast -w stop \
      >/dev/null 2>&1 || true
    cluster_started=false
  fi
  if [[ -n "${cluster_root:-}" && -e "${cluster_root}" ]]; then
    if [[ "$(dirname -- "${cluster_root}")" != "${cluster_parent%/}" || "$(basename -- "${cluster_root}")" != work-station-postgres-integration.* ]]; then
      echo "Refusing unexpected PostgreSQL integration cleanup target." >&2
      return 1
    fi
    find "${cluster_root}" -depth ! -type d -delete
    find "${cluster_root}" -depth -type d -empty -delete
  fi
}
trap cleanup_cluster EXIT

mkdir -m 700 "${cluster_socket}"
cluster_password="$("${backend_python}" - <<'PY'
import secrets

print(secrets.token_urlsafe(32))
PY
)"
"${postgres_bindir}/initdb" \
  -D "${cluster_data}" \
  --username=work_station_test_admin \
  --auth-local=trust \
  --auth-host=scram-sha-256 \
  --pwfile=<(printf '%s\n' "${cluster_password}") \
  --encoding=UTF8 \
  --no-locale \
  >/dev/null

cluster_port="$("${backend_python}" - <<'PY'
import socket

with socket.socket() as candidate:
    candidate.bind(("127.0.0.1", 0))
    print(candidate.getsockname()[1])
PY
)"
[[ "${cluster_port}" =~ ^[0-9]+$ ]]

if ! "${postgres_bindir}/pg_ctl" \
  -D "${cluster_data}" \
  -l "${cluster_log}" \
  -o "-h 127.0.0.1 -p ${cluster_port} -k ${cluster_socket} -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=64 -c shared_buffers=32MB" \
  -w start \
  >/dev/null; then
  echo "The disposable PostgreSQL integration cluster did not start." >&2
  exit 1
fi
cluster_started=true

PGPASSWORD="${cluster_password}" "${postgres_bindir}/createdb" \
  -h 127.0.0.1 \
  -p "${cluster_port}" \
  -U work_station_test_admin \
  ai_workspace_test

ephemeral_url="postgresql+asyncpg://work_station_test_admin:${cluster_password}@127.0.0.1:${cluster_port}/ai_workspace_test"

if [[ "${run_integration}" == true ]]; then
  (
    export RUN_DATABASE_INTEGRATION_TESTS=true
    export WORK_STATION_EPHEMERAL_TEST_DATABASE_URL="${ephemeral_url}"
    cd backend
    exec .venv/bin/python tests/db/run_postgres_integration.py
  )
fi

if [[ "${run_runtime}" == true ]]; then
  (
    export RUN_DATABASE_INTEGRATION_TESTS=true
    export TEST_DATABASE_URL="${ephemeral_url}"
    cd backend
    .venv/bin/alembic upgrade head
  )
  (
    export WORK_STATION_EPHEMERAL_TEST_DATABASE_URL="${ephemeral_url}"
    exec "${script_directory}/runtime_e2e.sh"
  )
fi

after_status="$(git status --porcelain=v1 --untracked-files=all)"
if [[ "${after_status}" != "${before_status}" ]]; then
  echo "PostgreSQL integration validation changed the repository worktree." >&2
  exit 1
fi

if [[ "${run_integration}" == true && "${run_runtime}" == true ]]; then
  echo "ephemeral PostgreSQL validation: integration and runtime E2E passed"
elif [[ "${run_runtime}" == true ]]; then
  echo "ephemeral PostgreSQL validation: runtime E2E passed"
else
  echo "ephemeral PostgreSQL validation: 36 integration tests passed"
fi
