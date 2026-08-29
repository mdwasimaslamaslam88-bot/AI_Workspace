#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
report_root="${WORK_STATION_BENCHMARK_REPORT_ROOT:-${HOME}/Desktop/Work_Station_Benchmark}"

if [[ "${report_root}" != /* || "$(basename -- "${report_root}")" != Work_Station_Benchmark ]]; then
  echo "The Quality Push v2 report root must be the dedicated absolute directory." >&2
  exit 2
fi
mkdir -m 700 -p "${report_root}"
chmod 700 "${report_root}"

references=(
  qwen3:8b
  qwen2.5-coder:7b
  gemma4:12b-it-q4_K_M
  phi4:14b-q4_K_M
)
outputs=(
  "${report_root}/.v2-model-qwen3-8b.json"
  "${report_root}/.v2-model-qwen25-coder-7b.json"
  "${report_root}/.v2-model-gemma4-12b.json"
  "${report_root}/.model-discovery-phi4-14b.json"
)
loaded_reference=""

unload_model() {
  if [[ -n "${loaded_reference}" ]]; then
    ollama stop "${loaded_reference}" >/dev/null 2>&1 || true
    loaded_reference=""
  fi
}
trap unload_model EXIT

for index in "${!references[@]}"; do
  reference="${references[${index}]}"
  output="${outputs[${index}]}"
  loaded_reference="${reference}"
  OLLAMA_LOCAL_MODEL_ALLOWLIST="[\"${reference}\",\"nomic-embed-text:latest\"]" \
  OLLAMA_TASK_MODEL_PREFERENCES="{\"code_generation\":\"${reference}\"}" \
  WORK_STATION_MODEL_EXPERIMENT_REFERENCE="${reference}" \
  WORK_STATION_MODEL_EXPERIMENT_OUTPUT="${output}" \
  WORK_STATION_MODEL_EXPERIMENT_PROFILE=baseline \
  WORK_STATION_BENCHMARK_REPORT_ROOT="${report_root}" \
    "${script_directory}/postgres_integration_check.sh" \
      --model-candidate-benchmark
  unload_model
done

exec "${script_directory}/quality_push_v2_model_comparison.sh"
