#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"

cd "${repository_root}"
if [[ $# -gt 0 && ( "$1" == "run" || "$1" == "status" || "$1" == "self-test" ) ]]; then
  exec python3 "${script_directory}/aster_next_prompt.py" "$@"
fi
exec python3 "${script_directory}/aster_next_prompt.py" run "$@"
