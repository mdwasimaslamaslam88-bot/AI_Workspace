#!/usr/bin/env bash
set -euo pipefail

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale is not installed. Install and enroll this workstation first." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for the local health check." >&2
  exit 1
fi
if ! tailscale status --json >/dev/null 2>&1; then
  echo "This workstation is not enrolled in a Tailscale tailnet." >&2
  exit 1
fi
if ! curl --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:8000/api/v1/health/live >/dev/null; then
  echo "The loopback WORK STATION backend is not healthy." >&2
  exit 1
fi

existing_status="$(tailscale serve status --json 2>/dev/null || true)"
if [[ -n "${existing_status//[[:space:]]/}" && "${existing_status//[[:space:]]/}" != "{}" ]]; then
  echo "An existing Tailscale Serve configuration was detected; refusing to overwrite it." >&2
  exit 1
fi

# Serve is private to the tailnet. Funnel is intentionally never enabled.
tailscale serve --yes --bg --https=443 http://127.0.0.1:8000
tailscale serve status --json >/dev/null
echo "Private HTTPS Serve is active for the enrolled tailnet."
