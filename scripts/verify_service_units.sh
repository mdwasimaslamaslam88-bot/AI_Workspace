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
cp "${repository_root}/deploy/systemd/work-station-health.service" \
  "${repository_root}/deploy/systemd/work-station-health.timer" \
  "${repository_root}/deploy/systemd/work-station-remote-health.timer" \
  "${repository_root}/deploy/systemd/work-station.target" \
  "${temporary_units}/"
systemd-analyze --user verify "${temporary_units}"/*
echo "systemd user units: valid"
