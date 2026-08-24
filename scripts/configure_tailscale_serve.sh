#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
tailscale_cli="${script_directory}/tailscale_cli.sh"

if ! "${tailscale_cli}" version >/dev/null 2>&1; then
  echo "Tailscale is not installed. Install and enroll this workstation first." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for the local health check." >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required for redacted Tailscale state validation." >&2
  exit 1
fi
if ! tailscale_status="$("${tailscale_cli}" status --json 2>/dev/null)" ||
  ! jq --exit-status '.BackendState == "Running"' \
    >/dev/null 2>&1 <<< "${tailscale_status}"; then
  echo "This workstation is not enrolled in a Tailscale tailnet." >&2
  exit 1
fi
if ! curl --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:8000/api/v1/health/ready >/dev/null; then
  echo "The loopback WORK STATION backend or a required dependency is not ready." >&2
  exit 1
fi

existing_status="$("${tailscale_cli}" serve status --json 2>/dev/null || true)"
if [[ -n "${existing_status//[[:space:]]/}" && "${existing_status//[[:space:]]/}" != "{}" ]]; then
  echo "An existing Tailscale Serve configuration was detected; refusing to overwrite it." >&2
  exit 1
fi
if ! existing_funnel="$("${tailscale_cli}" funnel status --json 2>/dev/null)"; then
  echo "The current Funnel state could not be verified; refusing to configure Serve." >&2
  exit 1
fi
normalized_funnel="${existing_funnel//[[:space:]]/}"
if [[ -n "${normalized_funnel}" &&
  "${normalized_funnel}" != "{}" &&
  "${normalized_funnel}" != "null" ]]; then
  echo "A public Funnel configuration was detected; refusing to configure Serve." >&2
  exit 1
fi

# Serve is private to the tailnet. Funnel is intentionally never enabled.
"${tailscale_cli}" serve --yes --bg --https=443 http://127.0.0.1:8000
"${script_directory}/check_remote_gateway.sh" >/dev/null
echo "Private HTTPS Serve is active for the enrolled tailnet."
