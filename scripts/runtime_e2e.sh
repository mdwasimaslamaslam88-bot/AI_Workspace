#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
backend_python="${repository_root}/backend/.venv/bin/python"

cd "${repository_root}/backend"
exec "${backend_python}" -m scripts.run_runtime_e2e
