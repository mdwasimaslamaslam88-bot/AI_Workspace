#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
report_root="${WORK_STATION_BENCHMARK_REPORT_ROOT:-${HOME}/Desktop/Work_Station_Benchmark}"

if [[ "${report_root}" != /* || "$(basename -- "${report_root}")" != Work_Station_Benchmark ]]; then
  echo "The model candidate report root must be the dedicated absolute directory." >&2
  exit 2
fi
mkdir -m 700 -p "${report_root}"
chmod 700 "${report_root}"

references=(
  qwen3:8b
  qwen2.5-coder:7b
  qwen2.5-coder:14b-instruct-q3_K_L
)
outputs=(
  "${report_root}/.model-candidate-qwen3-8b.json"
  "${report_root}/.model-candidate-qwen25-coder-7b.json"
  "${report_root}/.model-candidate-qwen25-coder-14b.json"
  "${report_root}/.model-profile-qwen3-thinking-auto.json"
)
profiles=(
  baseline
  baseline
  baseline
  qwen3_thinking_auto
)
loaded_reference=""

unload_model() {
  if [[ -n "${loaded_reference}" ]]; then
    ollama stop "${loaded_reference}" >/dev/null 2>&1 || true
    loaded_reference=""
  fi
}
trap unload_model EXIT

references+=(qwen3:8b)

for index in "${!references[@]}"; do
  reference="${references[${index}]}"
  output="${outputs[${index}]}"
  profile="${profiles[${index}]}"
  loaded_reference="${reference}"
  OLLAMA_LOCAL_MODEL_ALLOWLIST="[\"${reference}\",\"nomic-embed-text:latest\"]" \
  WORK_STATION_MODEL_EXPERIMENT_REFERENCE="${reference}" \
  WORK_STATION_MODEL_EXPERIMENT_OUTPUT="${output}" \
  WORK_STATION_MODEL_EXPERIMENT_PROFILE="${profile}" \
  WORK_STATION_BENCHMARK_REPORT_ROOT="${report_root}" \
    "${script_directory}/postgres_integration_check.sh" \
      --model-candidate-benchmark
  unload_model
done

(
  cd "${repository_root}/backend"
  exec .venv/bin/python -m scripts.model_candidate_benchmark \
    --aggregate \
    "${report_root}" \
    "${outputs[@]}"
)

echo "isolated three-model candidate benchmark: complete"
