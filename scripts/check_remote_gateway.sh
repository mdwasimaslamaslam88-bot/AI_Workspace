#!/usr/bin/env bash
set -euo pipefail

if ! command -v tailscale >/dev/null 2>&1; then
  echo "remote gateway: unavailable (Tailscale is not installed)"
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "remote gateway: unavailable (JSON validation is not installed)"
  exit 2
fi
if ! tailscale_status="$(tailscale status --json 2>/dev/null)" ||
  ! jq --exit-status '.BackendState == "Running"' \
    >/dev/null 2>&1 <<< "${tailscale_status}"; then
  echo "remote gateway: unavailable (workstation is not enrolled)"
  exit 2
fi
serve_status="$(tailscale serve status 2>/dev/null || true)"
if [[ -z "${serve_status//[[:space:]]/}" ]]; then
  echo "remote gateway: unavailable (Serve is not configured)"
  exit 2
fi
if [[ "${serve_status}" != *"Available within your tailnet:"* ]] ||
  [[ "${serve_status}" != *"proxy http://127.0.0.1:8000"* ]]; then
  echo "remote gateway: unavailable (Serve does not match WORK STATION)"
  exit 2
fi
proxy_count="$(grep -c ' proxy ' <<< "${serve_status}" || true)"
if [[ "${proxy_count}" -ne 1 ]] ||
  grep --extended-regexp --quiet '\|--.*( path | text:|tcp://)' \
    <<< "${serve_status}"; then
  echo "remote gateway: unavailable (Serve contains an unexpected handler)"
  exit 2
fi
if [[ "${serve_status}" == *"Available on the internet:"* ]]; then
  echo "remote gateway: unsafe (public Funnel exposure detected)"
  exit 2
fi
if ! funnel_status="$(tailscale funnel status --json 2>/dev/null)"; then
  echo "remote gateway: unavailable (Funnel state could not be verified)"
  exit 2
fi
normalized_funnel_status="${funnel_status//[[:space:]]/}"
if [[ -n "${normalized_funnel_status}" &&
  "${normalized_funnel_status}" != "{}" &&
  "${normalized_funnel_status}" != "null" ]]; then
  echo "remote gateway: unsafe (public Funnel configuration detected)"
  exit 2
fi
echo "remote gateway: configured for the private tailnet"
