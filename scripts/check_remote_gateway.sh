#!/usr/bin/env bash
set -euo pipefail

if ! command -v tailscale >/dev/null 2>&1; then
  echo "remote gateway: unavailable (Tailscale is not installed)"
  exit 2
fi
if ! tailscale status --json >/dev/null 2>&1; then
  echo "remote gateway: unavailable (workstation is not enrolled)"
  exit 2
fi
if ! tailscale serve status --json >/dev/null 2>&1; then
  echo "remote gateway: unavailable (Serve is not configured)"
  exit 2
fi
echo "remote gateway: configured for the private tailnet"
