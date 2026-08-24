#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${WORK_STATION_TAILSCALE_CLI:-}" ]]; then
  if [[ "${WORK_STATION_TAILSCALE_CLI}" != /* ||
        ! -x "${WORK_STATION_TAILSCALE_CLI}" ]]; then
    echo "The Tailscale CLI override must be an executable absolute path." >&2
    exit 2
  fi
  exec "${WORK_STATION_TAILSCALE_CLI}" "$@"
fi
if [[ -x /usr/bin/tailscale ]]; then
  exec /usr/bin/tailscale "$@"
fi

runtime_binary="${HOME}/AI_Workspace_Runtimes/tailscale/current/tailscale"
runtime_socket="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/work-station-tailscale/tailscaled.sock"
if [[ ! -x "${runtime_binary}" ]]; then
  echo "Tailscale is not installed." >&2
  exit 127
fi
exec "${runtime_binary}" --socket="${runtime_socket}" "$@"
