#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
template="${repository_root}/deploy/systemd/work-station-backend.service.in"
user_unit_directory="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"

if [[ ! -x "${repository_root}/backend/.venv/bin/uvicorn" ]]; then
  echo "Backend virtual environment is not ready." >&2
  exit 1
fi
if [[ ! -f "${repository_root}/frontend/dist/index.html" ]]; then
  echo "Build the production web app before installing services." >&2
  exit 1
fi
if [[ ! -d "${HOME}/AI_Workspace_Data" ]]; then
  echo "The private data root is missing." >&2
  exit 1
fi

mkdir -p -- "${user_unit_directory}"
escaped_repository_root="${repository_root//&/\\&}"
escaped_repository_root="${escaped_repository_root//|/\\|}"
sed "s|@REPOSITORY_ROOT@|${escaped_repository_root}|g" "${template}" \
  > "${user_unit_directory}/work-station-backend.service"
install -m 0644 \
  "${repository_root}/deploy/systemd/work-station-health.service" \
  "${repository_root}/deploy/systemd/work-station-health.timer" \
  "${user_unit_directory}/"

systemctl --user daemon-reload
systemd-analyze --user verify \
  "${user_unit_directory}/work-station-backend.service" \
  "${user_unit_directory}/work-station-health.service" \
  "${user_unit_directory}/work-station-health.timer"

echo "User services installed but not started."
echo "Start with: systemctl --user enable --now work-station-backend.service work-station-health.timer"
