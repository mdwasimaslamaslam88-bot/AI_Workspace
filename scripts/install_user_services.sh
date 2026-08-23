#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
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
for template_name in \
  work-station-backend.service \
  work-station-remote-health.service; do
  sed "s|@REPOSITORY_ROOT@|${escaped_repository_root}|g" \
    "${repository_root}/deploy/systemd/${template_name}.in" \
    > "${user_unit_directory}/${template_name}"
done
install -m 0644 \
  "${repository_root}/deploy/systemd/work-station-health.service" \
  "${repository_root}/deploy/systemd/work-station-health.timer" \
  "${repository_root}/deploy/systemd/work-station-remote-health.timer" \
  "${repository_root}/deploy/systemd/work-station.target" \
  "${user_unit_directory}/"

systemctl --user daemon-reload
systemd-analyze --user verify \
  "${user_unit_directory}/work-station-backend.service" \
  "${user_unit_directory}/work-station-health.service" \
  "${user_unit_directory}/work-station-health.timer" \
  "${user_unit_directory}/work-station-remote-health.service" \
  "${user_unit_directory}/work-station-remote-health.timer" \
  "${user_unit_directory}/work-station.target"

echo "User services installed but not started."
echo "Start with: systemctl --user enable --now work-station.target"
