#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
backend_python="${repository_root}/backend/.venv/bin/python"
run_integration=true
run_runtime=false
run_browser=false
run_benchmark=false
run_massive_benchmark=false
run_model_candidate_benchmark=false
e2e_backend_pid=""
isolated_ollama_pid=""

usage() {
  cat <<'EOF'
Usage: ./scripts/postgres_integration_check.sh [OPTIONS]

Starts a user-owned, loopback-only PostgreSQL cluster in a random temporary
directory, creates only the approved ai_workspace_test database, runs the real
integration suite, and removes the cluster. Browser and runtime modes migrate
the same disposable database before exercising real clients and runtimes.

  --with-runtime  Add real AI runtime E2E.
  --with-browser  Add real compiled-PWA browser E2E.
  --runtime-only  Skip integration and run real AI runtime E2E.
  --browser-only  Skip integration and run real browser E2E.
  --benchmark     Skip integration and run the full real-HTTP AI benchmark.
  --massive-benchmark  Run only the disposable adaptive massive benchmark.
  --model-candidate-benchmark  Run one isolated real-HTTP model comparison.
EOF
}

for argument in "$@"; do
  case "${argument}" in
    --with-runtime) run_runtime=true ;;
    --with-browser) run_browser=true ;;
    --runtime-only) run_integration=false; run_runtime=true ;;
    --browser-only) run_integration=false; run_browser=true ;;
    --benchmark) run_integration=false; run_benchmark=true; run_massive_benchmark=true ;;
    --massive-benchmark) run_integration=false; run_massive_benchmark=true ;;
    --model-candidate-benchmark) run_integration=false; run_model_candidate_benchmark=true ;;
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

stop_e2e_backend() {
  if [[ -n "${e2e_backend_pid}" ]] && kill -0 "${e2e_backend_pid}" 2>/dev/null; then
    kill -TERM "${e2e_backend_pid}" 2>/dev/null || true
    wait "${e2e_backend_pid}" 2>/dev/null || true
  fi
  e2e_backend_pid=""
  if [[ -n "${isolated_ollama_pid}" ]] && kill -0 "${isolated_ollama_pid}" 2>/dev/null; then
    kill -TERM "${isolated_ollama_pid}" 2>/dev/null || true
    wait "${isolated_ollama_pid}" 2>/dev/null || true
  fi
  isolated_ollama_pid=""
}

print_sanitized_backend_failure() {
  WORK_STATION_FAILURE_LOG="${backend_log}" \
  WORK_STATION_FAILURE_DATABASE_URL="${ephemeral_url}" \
  WORK_STATION_FAILURE_DATABASE_PASSWORD="${cluster_password}" \
  WORK_STATION_FAILURE_PROVISIONING_TOKEN="${e2e_provisioning_token}" \
  WORK_STATION_FAILURE_PROVISIONING_DIGEST="${e2e_provisioning_digest}" \
  WORK_STATION_FAILURE_REPOSITORY_ROOT="${repository_root}" \
  WORK_STATION_FAILURE_TEMPORARY_ROOT="${cluster_root}" \
    "${backend_python}" - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["WORK_STATION_FAILURE_LOG"])
try:
    content = path.read_text(encoding="utf-8", errors="replace")
except OSError:
    raise SystemExit(0)
for key, replacement in (
    ("WORK_STATION_FAILURE_DATABASE_URL", "[disposable-database-url]"),
    ("WORK_STATION_FAILURE_DATABASE_PASSWORD", "[disposable-database-password]"),
    ("WORK_STATION_FAILURE_PROVISIONING_TOKEN", "[disposable-provisioning-token]"),
    ("WORK_STATION_FAILURE_PROVISIONING_DIGEST", "[disposable-provisioning-digest]"),
    ("WORK_STATION_FAILURE_REPOSITORY_ROOT", "[workspace]"),
    ("WORK_STATION_FAILURE_TEMPORARY_ROOT", "[temporary-root]"),
):
    sensitive = os.environ.get(key, "")
    if sensitive:
        content = content.replace(sensitive, replacement)
print(content[-4000:], end="")
PY
}

cleanup_cluster() {
  stop_e2e_backend
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

choose_loopback_port() {
  "${backend_python}" - <<'PY'
import socket

with socket.socket() as candidate:
    candidate.bind(("127.0.0.1", 0))
    print(candidate.getsockname()[1])
PY
}

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

cluster_port="$(choose_loopback_port)"
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

if [[ "${WORK_STATION_ISOLATED_UPDATE_VALIDATION:-}" == "1" ]]; then
  export DATABASE_URL="postgresql+asyncpg://update_boundary@127.0.0.1:1/work_station_production_boundary"
fi

if [[ "${run_integration}" == true ]]; then
  (
    export DATABASE_SSL_MODE=disable
    export RUN_DATABASE_INTEGRATION_TESTS=true
    export WORK_STATION_EPHEMERAL_TEST_DATABASE_URL="${ephemeral_url}"
    cd backend
    exec .venv/bin/python tests/db/run_postgres_integration.py
  )
  (
    export DATABASE_SSL_MODE=disable
    export RUN_DATABASE_INTEGRATION_TESTS=true
    export TEST_DATABASE_URL="${ephemeral_url}"
    cd backend
    .venv/bin/python -m alembic upgrade head
    .venv/bin/python -m alembic check
  )
fi

if [[ "${run_runtime}" == true || "${run_browser}" == true ||
  "${run_benchmark}" == true || "${run_massive_benchmark}" == true ||
  "${run_model_candidate_benchmark}" == true ]]; then
  (
    export DATABASE_SSL_MODE=disable
    export RUN_DATABASE_INTEGRATION_TESTS=true
    export TEST_DATABASE_URL="${ephemeral_url}"
    cd backend
    .venv/bin/python -m alembic upgrade head
  )
fi

if [[ "${run_browser}" == true || "${run_benchmark}" == true ||
  "${run_massive_benchmark}" == true ||
  "${run_model_candidate_benchmark}" == true ]]; then
  playwright_browsers_path="${WORK_STATION_PLAYWRIGHT_BROWSERS_PATH:-${repository_root}/../../AI_Workspace_Runtimes/playwright}"
  if [[ "${run_browser}" == true ]]; then
    if [[ "${playwright_browsers_path}" != /* || ! -d "${playwright_browsers_path}" ]]; then
      echo "The private Playwright browser runtime directory is unavailable." >&2
      echo "Set WORK_STATION_PLAYWRIGHT_BROWSERS_PATH to an existing absolute directory." >&2
      exit 1
    fi
    playwright_browsers_path="$(cd -- "${playwright_browsers_path}" && pwd -P)"
    if ! PLAYWRIGHT_BROWSERS_PATH="${playwright_browsers_path}" node --input-type=module - <<'JS'
import { existsSync } from "node:fs";
import { chromium } from "playwright";

process.exit(existsSync(chromium.executablePath()) ? 0 : 1);
JS
    then
      echo "The pinned Playwright Chromium runtime is unavailable." >&2
      echo "Install it with PLAYWRIGHT_BROWSERS_PATH set to the private runtime directory." >&2
      exit 1
    fi
  fi

  api_port="$(choose_loopback_port)"
  [[ "${api_port}" =~ ^[0-9]+$ ]]
  api_origin="http://127.0.0.1:${api_port}"
  web_root="${cluster_root}/web"
  asset_root="${cluster_root}/assets"
  backend_log="${cluster_root}/backend.log"
  mkdir -m 700 "${web_root}" "${asset_root}"

  isolated_fixture_bin=""
  isolated_ollama_origin=""
  isolated_ollama_reference="work-station-update-smoke:latest"
  if [[ "${run_browser}" == true && "${WORK_STATION_ISOLATED_UPDATE_VALIDATION:-}" == "1" ]]; then
    isolated_fixture_bin="${cluster_root}/isolated-fixture-bin"
    mkdir -m 700 "${isolated_fixture_bin}"
    ln --symbolic \
      "${repository_root}/backend/scripts/isolated_update_nvidia_smi.py" \
      "${isolated_fixture_bin}/nvidia-smi"
    isolated_ollama_port="$(choose_loopback_port)"
    [[ "${isolated_ollama_port}" =~ ^[0-9]+$ ]]
    isolated_ollama_origin="http://127.0.0.1:${isolated_ollama_port}"
    "${backend_python}" \
      "${repository_root}/backend/scripts/isolated_update_ollama.py" \
      --port "${isolated_ollama_port}" \
      >"${cluster_root}/isolated-ollama.log" 2>&1 &
    isolated_ollama_pid=$!
    isolated_ollama_ready=false
    for _attempt in $(seq 1 40); do
      if ! kill -0 "${isolated_ollama_pid}" 2>/dev/null; then
        break
      fi
      if curl --fail --silent --show-error \
        "${isolated_ollama_origin}/api/tags" >/dev/null 2>&1; then
        isolated_ollama_ready=true
        break
      fi
      sleep 0.1
    done
    if [[ "${isolated_ollama_ready}" != true ]]; then
      echo "The loopback-only isolated model fixture did not become ready." >&2
      exit 1
    fi
  fi

  if [[ "${run_browser}" == true || "${run_benchmark}" == true ]]; then
    if ! (
      cd frontend
      VITE_API_BASE_URL="${api_origin}" ../node_modules/.bin/vite build \
        --outDir "${web_root}" \
        --emptyOutDir \
        --logLevel error
    ) >"${cluster_root}/vite.log" 2>&1; then
      echo "The isolated browser PWA build failed." >&2
      exit 1
    fi
  else
    web_root="${repository_root}/frontend/dist"
    if [[ ! -f "${web_root}/index.html" ]]; then
      echo "The compiled local PWA required by the isolated backend is unavailable." >&2
      exit 1
    fi
  fi

  e2e_provisioning_token="$("${backend_python}" - <<'PY'
import secrets

print(secrets.token_urlsafe(32))
PY
)"
  e2e_provisioning_digest="$(
    printf '%s' "${e2e_provisioning_token}" | "${backend_python}" -c \
      'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  )"

  (
    export APP_TITLE="WORK STATION"
    export ASSET_STORAGE_ROOT="${asset_root}"
    export BACKEND_CORS_ORIGINS="[\"${api_origin}\"]"
    export DATABASE_SSL_MODE=disable
    export DATABASE_URL="${ephemeral_url}"
    export EXTERNAL_AI_STATE_ROOT="${cluster_root}/external-ai"
    export CONNECTOR_STATE_ROOT="${cluster_root}/connectors"
    export CONNECTOR_ALLOWED_ORIGINS="[]"
    export HARDWARE_STATE_PATH="${cluster_root}/hardware-capability.json"
    export REMOTE_GATEWAY_MODE=local
    export SELF_UPDATE_STATE_ROOT="${cluster_root}/self-update"
    export USER_PROVISIONING_TOKEN_DIGEST="${e2e_provisioning_digest}"
    export WORK_STATION_WEB_ROOT="${web_root}"
    if [[ -n "${isolated_ollama_origin}" ]]; then
      export PATH="${isolated_fixture_bin}:${PATH}"
      export OLLAMA_BASE_URL="${isolated_ollama_origin}"
      export OLLAMA_EMBEDDING_MODEL="${isolated_ollama_reference}"
      export OLLAMA_LOCAL_MODEL_ALLOWLIST="[\"${isolated_ollama_reference}\"]"
      export OLLAMA_TASK_MODEL_PREFERENCES="$(
        ISOLATED_OLLAMA_REFERENCE="${isolated_ollama_reference}" \
          "${backend_python}" - <<'PY'
import json
import os

reference = os.environ["ISOLATED_OLLAMA_REFERENCE"]
tasks = (
    "general_chat",
    "reasoning",
    "mathematics",
    "coding",
    "debugging",
    "code_generation",
    "expert_analysis",
    "vision",
    "rag",
    "memory",
    "summarization",
    "tool_calling",
    "workflow_planning",
    "long_context",
    "exact_output",
)
print(json.dumps({task: reference for task in tasks}, separators=(",", ":")))
PY
      )"
    fi
    cd backend
    uvicorn_arguments=(
      app.main:app
      --host 127.0.0.1
      --port "${api_port}"
      --no-access-log
      --log-level warning
    )
    if [[ "${run_massive_benchmark}" == true && "${run_benchmark}" != true ]]; then
      massive_backend_workers="${WORK_STATION_MASSIVE_BACKEND_WORKERS:-4}"
      if [[ ! "${massive_backend_workers}" =~ ^[1-4]$ ]]; then
        echo "Massive benchmark backend workers must be between 1 and 4." >&2
        exit 2
      fi
      uvicorn_arguments+=(--workers "${massive_backend_workers}")
    fi
    exec .venv/bin/python -m uvicorn "${uvicorn_arguments[@]}"
  ) >"${backend_log}" 2>&1 &
  e2e_backend_pid=$!

  backend_ready=false
  for _attempt in $(seq 1 120); do
    if ! kill -0 "${e2e_backend_pid}" 2>/dev/null; then
      break
    fi
    if curl --fail --silent --show-error \
      "${api_origin}/api/v1/health/live" >/dev/null 2>&1; then
      backend_ready=true
      break
    fi
    sleep 0.25
  done
  if [[ "${backend_ready}" != true ]]; then
    print_sanitized_backend_failure >&2
    echo "The isolated browser backend did not become ready." >&2
    exit 1
  fi

  if [[ "${run_browser}" == true ]]; then
    printf '%s' "${e2e_provisioning_token}" | (
      export PLAYWRIGHT_BROWSERS_PATH="${playwright_browsers_path}"
      export WORK_STATION_E2E_API_ORIGIN="${api_origin}"
      export WORK_STATION_E2E_WEB_ORIGIN="${api_origin}"
      exec node scripts/browser_e2e.mjs
    )
  fi
  if [[ "${run_benchmark}" == true ]]; then
    benchmark_report_root="${WORK_STATION_BENCHMARK_REPORT_ROOT:-${HOME}/Desktop/Work_Station_Benchmark}"
    if [[ "${benchmark_report_root}" != /* ]]; then
      echo "The benchmark report root must be an absolute path." >&2
      exit 1
    fi
    printf '%s' "${e2e_provisioning_token}" | (
      export WORK_STATION_BENCHMARK_API_ORIGIN="${api_origin}"
      export WORK_STATION_BENCHMARK_ASSET_ROOT="${asset_root}"
      export WORK_STATION_BENCHMARK_DATABASE_URL="${ephemeral_url}"
      export WORK_STATION_BENCHMARK_PROVISIONING_DIGEST="${e2e_provisioning_digest}"
      export WORK_STATION_BENCHMARK_REPORT_ROOT="${benchmark_report_root}"
      export WORK_STATION_BENCHMARK_WEB_ROOT="${web_root}"
      cd backend
      exec .venv/bin/python -m scripts.ai_quality_benchmark
    )
  fi
  if [[ "${run_massive_benchmark}" == true ]]; then
    benchmark_report_root="${WORK_STATION_BENCHMARK_REPORT_ROOT:-${HOME}/Desktop/Work_Station_Benchmark}"
    if [[ "${benchmark_report_root}" != /* ]]; then
      echo "The benchmark report root must be an absolute path." >&2
      exit 1
    fi
    printf '%s' "${e2e_provisioning_token}" | (
      export WORK_STATION_BENCHMARK_API_ORIGIN="${api_origin}"
      export WORK_STATION_BENCHMARK_REPORT_ROOT="${benchmark_report_root}"
      cd backend
      exec .venv/bin/python -m scripts.massive_ai_benchmark
    )
  fi
  if [[ "${run_model_candidate_benchmark}" == true ]]; then
    candidate_output="${WORK_STATION_MODEL_EXPERIMENT_OUTPUT:-}"
    candidate_reference="${WORK_STATION_MODEL_EXPERIMENT_REFERENCE:-}"
    if [[ "${candidate_output}" != /* || -z "${candidate_reference}" ]]; then
      echo "The isolated model candidate benchmark configuration is invalid." >&2
      exit 2
    fi
    printf '%s' "${e2e_provisioning_token}" | (
      export WORK_STATION_BENCHMARK_API_ORIGIN="${api_origin}"
      export WORK_STATION_MODEL_EXPERIMENT_OUTPUT="${candidate_output}"
      export WORK_STATION_MODEL_EXPERIMENT_REFERENCE="${candidate_reference}"
      cd backend
      exec .venv/bin/python -m scripts.model_candidate_benchmark
    )
  fi
  e2e_provisioning_token=""
  stop_e2e_backend
fi

if [[ "${run_runtime}" == true ]]; then
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

if [[ "${run_benchmark}" == true ]]; then
  echo "ephemeral PostgreSQL validation: real-HTTP AI and massive benchmarks passed"
elif [[ "${run_model_candidate_benchmark}" == true ]]; then
  echo "ephemeral PostgreSQL validation: isolated model candidate benchmark passed"
elif [[ "${run_massive_benchmark}" == true ]]; then
  echo "ephemeral PostgreSQL validation: massive benchmark passed"
elif [[ "${run_integration}" == true && "${run_browser}" == true && "${run_runtime}" == true ]]; then
  echo "ephemeral PostgreSQL validation: integration, browser/PWA E2E, and runtime E2E passed"
elif [[ "${run_integration}" == true && "${run_browser}" == true ]]; then
  echo "ephemeral PostgreSQL validation: integration and browser/PWA E2E passed"
elif [[ "${run_integration}" == true && "${run_runtime}" == true ]]; then
  echo "ephemeral PostgreSQL validation: integration and runtime E2E passed"
elif [[ "${run_browser}" == true && "${run_runtime}" == true ]]; then
  echo "ephemeral PostgreSQL validation: browser/PWA and runtime E2E passed"
elif [[ "${run_browser}" == true ]]; then
  echo "ephemeral PostgreSQL validation: browser/PWA E2E passed"
elif [[ "${run_runtime}" == true ]]; then
  echo "ephemeral PostgreSQL validation: runtime E2E passed"
else
  echo "ephemeral PostgreSQL validation: 39 integration tests passed"
fi
