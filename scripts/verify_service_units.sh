#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
temporary_units="$(mktemp -d)"
trap 'rm -rf -- "${temporary_units}"' EXIT

escaped_repository_root="${repository_root//&/\\&}"
escaped_repository_root="${escaped_repository_root//|/\\|}"
sed "s|@REPOSITORY_ROOT@|${escaped_repository_root}|g" \
  "${repository_root}/deploy/systemd/work-station-backend.service.in" \
  > "${temporary_units}/work-station-backend.service"
sed "s|@REPOSITORY_ROOT@|${escaped_repository_root}|g" \
  "${repository_root}/deploy/systemd/work-station-remote-health.service.in" \
  > "${temporary_units}/work-station-remote-health.service"
sed "s|@REPOSITORY_ROOT@|${escaped_repository_root}|g" \
  "${repository_root}/deploy/systemd/work-station-backup.service.in" \
  > "${temporary_units}/work-station-backup.service"
sed "s|@REPOSITORY_ROOT@|${escaped_repository_root}|g" \
  "${repository_root}/deploy/systemd/work-station-technology-watch.service.in" \
  > "${temporary_units}/work-station-technology-watch.service"
sed "s|@REPOSITORY_ROOT@|${escaped_repository_root}|g" \
  "${repository_root}/deploy/systemd/work-station-tailscaled.service.in" \
  > "${temporary_units}/work-station-tailscaled.service"
cp "${repository_root}/deploy/systemd/work-station-health.service" \
  "${repository_root}/deploy/systemd/work-station-health.timer" \
  "${repository_root}/deploy/systemd/work-station-backup.timer" \
  "${repository_root}/deploy/systemd/work-station-technology-watch.timer" \
  "${repository_root}/deploy/systemd/work-station-remote-health.timer" \
  "${repository_root}/deploy/systemd/work-station.target" \
  "${temporary_units}/"
mkdir -m 700 "${temporary_units}/.runtime"
XDG_RUNTIME_DIR="${temporary_units}/.runtime" \
  systemd-analyze --user verify "${temporary_units}"/*
if grep -E -q 'work-station-(backup|technology-watch)' \
  "${temporary_units}/work-station.target"; then
  echo "Opt-in backup and technology timers must not be part of work-station.target." >&2
  exit 1
else
  scan_status=$?
  if [[ "${scan_status}" -gt 1 ]]; then
    echo "Service-unit target scan failed with grep status ${scan_status}." >&2
    exit "${scan_status}"
  fi
fi
mapfile -d '' unit_files < <(find "${temporary_units}" -type f -print0)
if [[ "${#unit_files[@]}" -gt 0 ]] && grep -E -q '^ProtectKernelModules=' "${unit_files[@]}"; then
  echo "User services must not request the unavailable kernel-module capability boundary." >&2
  exit 1
else
  scan_status=$?
  if [[ "${scan_status}" -gt 1 ]]; then
    echo "Service-unit capability scan failed with grep status ${scan_status}." >&2
    exit "${scan_status}"
  fi
fi
echo "systemd user units: valid"
