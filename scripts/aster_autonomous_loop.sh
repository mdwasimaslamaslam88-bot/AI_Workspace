#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"

cd "${repository_root}"
exec python3 "${script_directory}/aster_next_prompt.py" run "$@"
