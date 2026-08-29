#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
report_root="${HOME}/Desktop/Work_Station_Benchmark"

reports=(
  "${report_root}/.v2-model-qwen3-8b.json"
  "${report_root}/.v2-model-qwen25-coder-7b.json"
  "${report_root}/.v2-model-gemma4-12b.json"
  "${report_root}/.model-discovery-phi4-14b.json"
)

for report in "${reports[@]}"; do
  if [[ ! -f "${report}" ]]; then
    echo "Missing isolated Quality Push v2 report: ${report}" >&2
    exit 2
  fi
done

cd "${repository_root}/backend"
exec .venv/bin/python -m scripts.current_hardware_model_discovery \
  --quality-push-v2 \
  "${report_root}" \
  "${reports[@]}"
