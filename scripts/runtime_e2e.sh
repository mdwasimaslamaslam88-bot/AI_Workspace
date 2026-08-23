#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
backend_python="${repository_root}/backend/.venv/bin/python"

cd "${repository_root}/backend"
for smoke in \
  scripts.real_vision_smoke \
  scripts.real_rag_smoke \
  scripts.real_memory_smoke \
  scripts.real_image_smoke \
  scripts.real_voice_smoke \
  scripts.real_tools_smoke \
  scripts.real_workflow_smoke; do
  "${backend_python}" -m "${smoke}"
done
echo "real runtime E2E: vision, RAG, memory, image, voice, tools, and workflows passed"
