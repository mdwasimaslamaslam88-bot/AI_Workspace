#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
backend_python="${repository_root}/backend/.venv/bin/python"

cd "${repository_root}/backend"
for smoke in \
  real_vision_smoke.py \
  real_rag_smoke.py \
  real_memory_smoke.py \
  real_image_smoke.py \
  real_voice_smoke.py \
  real_tools_smoke.py \
  real_workflow_smoke.py; do
  "${backend_python}" "scripts/${smoke}"
done
echo "real runtime E2E: vision, RAG, memory, image, voice, tools, and workflows passed"
