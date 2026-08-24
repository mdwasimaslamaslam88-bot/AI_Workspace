#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
"${script_directory}/desktop_check.sh" --require-launch
exec "${script_directory}/postgres_integration_check.sh" \
  --benchmark \
  --with-browser
