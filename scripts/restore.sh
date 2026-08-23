#!/usr/bin/env bash
set -euo pipefail
script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "${script_directory}/../backend/.venv/bin/python" \
  "${script_directory}/backup_tool.py" restore "$@"
