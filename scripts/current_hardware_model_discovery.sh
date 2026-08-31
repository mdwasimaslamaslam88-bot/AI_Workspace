#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
report_root="${WORK_STATION_BENCHMARK_REPORT_ROOT:-${HOME}/Desktop/Work_Station_Benchmark}"

if [[ "${report_root}" != /* || "$(basename -- "${report_root}")" != Work_Station_Benchmark ]]; then
  echo "The model discovery report root must be the dedicated absolute directory." >&2
  exit 2
fi
mkdir -m 700 -p "${report_root}"
chmod 700 "${report_root}"

baseline_outputs=(
  "${report_root}/.model-candidate-qwen3-8b.json"
  "${report_root}/.model-candidate-qwen25-coder-7b.json"
  "${report_root}/.model-candidate-qwen25-coder-14b.json"
)
references=(
  qwen3:14b-q4_K_M
  deepcoder:14b-preview-q4_K_M
  gemma4:12b-it-q4_K_M
  qwen3.5:9b-q4_K_M
  ministral-3:14b-instruct-2512-q4_K_M
  phi4:14b-q4_K_M
)
outputs=(
  "${report_root}/.model-discovery-qwen3-14b.json"
  "${report_root}/.model-discovery-deepcoder-14b.json"
  "${report_root}/.model-discovery-gemma4-12b.json"
  "${report_root}/.model-discovery-qwen35-9b.json"
  "${report_root}/.model-discovery-ministral3-14b.json"
  "${report_root}/.model-discovery-phi4-14b.json"
)
vision_references=(
  qwen2.5vl:7b
  gemma4:12b-it-q4_K_M
  qwen3.5:9b-q4_K_M
  ministral-3:14b-instruct-2512-q4_K_M
)
vision_outputs=(
  "${report_root}/.model-vision-qwen25vl-7b.json"
  "${report_root}/.model-vision-gemma4-12b.json"
  "${report_root}/.model-vision-qwen35-9b.json"
  "${report_root}/.model-vision-ministral3-14b.json"
)
loaded_reference=""
task_preference_keys=(
  general_chat reasoning mathematics coding debugging code_generation
  expert_analysis vision rag memory summarization tool_calling
  workflow_planning long_context exact_output
)

isolated_task_preferences() {
  local reference="$1"
  local separator=""
  local payload="{"
  local task
  for task in "${task_preference_keys[@]}"; do
    payload+="${separator}\"${task}\":\"${reference}\""
    separator=,
  done
  printf '%s}' "${payload}"
}

unload_model() {
  if [[ -n "${loaded_reference}" ]]; then
    ollama stop "${loaded_reference}" >/dev/null 2>&1 || true
    loaded_reference=""
  fi
}
trap unload_model EXIT

for output in "${baseline_outputs[@]}"; do
  if [[ ! -f "${output}" ]]; then
    echo "The preserved baseline report is unavailable: $(basename -- "${output}")" >&2
    exit 2
  fi
done

for index in "${!vision_references[@]}"; do
  reference="${vision_references[${index}]}"
  output="${vision_outputs[${index}]}"
  loaded_reference="${reference}"
  task_preferences="$(isolated_task_preferences "${reference}")"
  OLLAMA_LOCAL_MODEL_ALLOWLIST="[\"${reference}\",\"nomic-embed-text:latest\"]" \
  OLLAMA_TASK_MODEL_PREFERENCES="${task_preferences}" \
  WORK_STATION_MODEL_EXPERIMENT_REFERENCE="${reference}" \
  WORK_STATION_MODEL_EXPERIMENT_OUTPUT="${output}" \
  WORK_STATION_MODEL_EXPERIMENT_PROFILE=vision \
  WORK_STATION_BENCHMARK_REPORT_ROOT="${report_root}" \
    "${script_directory}/postgres_integration_check.sh" \
      --model-candidate-benchmark || candidate_status=$?
  candidate_status="${candidate_status:-0}"
  unload_model
  if [[ "${candidate_status}" -ne 0 && "${candidate_status}" -ne 86 ]]; then
    exit "${candidate_status}"
  fi
  unset candidate_status
done

for index in "${!references[@]}"; do
  reference="${references[${index}]}"
  output="${outputs[${index}]}"
  loaded_reference="${reference}"
  task_preferences="$(isolated_task_preferences "${reference}")"
  OLLAMA_LOCAL_MODEL_ALLOWLIST="[\"${reference}\",\"nomic-embed-text:latest\"]" \
  OLLAMA_TASK_MODEL_PREFERENCES="${task_preferences}" \
  WORK_STATION_MODEL_EXPERIMENT_REFERENCE="${reference}" \
  WORK_STATION_MODEL_EXPERIMENT_OUTPUT="${output}" \
  WORK_STATION_MODEL_EXPERIMENT_PROFILE=baseline \
  WORK_STATION_BENCHMARK_REPORT_ROOT="${report_root}" \
    "${script_directory}/postgres_integration_check.sh" \
      --model-candidate-benchmark || candidate_status=$?
  candidate_status="${candidate_status:-0}"
  unload_model
  if [[ "${candidate_status}" -ne 0 && "${candidate_status}" -ne 86 ]]; then
    exit "${candidate_status}"
  fi
  unset candidate_status
done

(
  cd "${repository_root}/backend"
  exec .venv/bin/python -m scripts.current_hardware_model_discovery \
    "${report_root}" \
    "${baseline_outputs[@]}" \
    "${outputs[@]}"
)

(
  cd "${repository_root}/backend"
  exec .venv/bin/python -m scripts.current_hardware_vision_discovery \
    "${report_root}" \
    "${vision_outputs[@]}"
)

echo "isolated current-hardware model discovery: complete"
