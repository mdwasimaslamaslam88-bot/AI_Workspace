#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
tailscale_cli="${script_directory}/tailscale_cli.sh"

if ! "${tailscale_cli}" version >/dev/null 2>&1; then
  echo "remote gateway: unavailable (Tailscale is not installed)"
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "remote gateway: unavailable (JSON validation is not installed)"
  exit 2
fi
if ! tailscale_status="$("${tailscale_cli}" status --json 2>/dev/null)" ||
  ! jq --exit-status '.BackendState == "Running"' \
    >/dev/null 2>&1 <<< "${tailscale_status}"; then
  echo "remote gateway: unavailable (workstation is not enrolled)"
  exit 2
fi
expected_host_port="$(
  jq --raw-output '.Self.DNSName // empty | rtrimstr(".") + ":443"' \
    <<< "${tailscale_status}"
)"
if [[ "${expected_host_port}" == ":443" ]]; then
  echo "remote gateway: unavailable (workstation MagicDNS name is unavailable)"
  exit 2
fi
if ! serve_status="$("${tailscale_cli}" serve status --json 2>/dev/null)" ||
  ! jq --exit-status 'type == "object"' \
    >/dev/null 2>&1 <<< "${serve_status}"; then
  echo "remote gateway: unavailable (Serve state could not be verified)"
  exit 2
fi
if jq --exit-status 'length == 0' \
  >/dev/null 2>&1 <<< "${serve_status}"; then
  echo "remote gateway: unavailable (Serve is not configured)"
  exit 2
fi
funnel_is_clear='[
  .. | objects | .AllowFunnel? | select(. != null)
] | all(type == "object" and length == 0)'
if ! jq --exit-status "${funnel_is_clear}" \
  >/dev/null 2>&1 <<< "${serve_status}"; then
  echo "remote gateway: unsafe (public Funnel exposure detected)"
  exit 2
fi
route_matches='type == "object" and
  (.TCP["443"].HTTPS == true) and
  (.Web[$host].Handlers["/"].Proxy == "http://127.0.0.1:8000")'
if ! jq --exit-status --arg host "${expected_host_port}" "${route_matches}" \
  >/dev/null 2>&1 <<< "${serve_status}"; then
  echo "remote gateway: unavailable (Serve does not match WORK STATION)"
  exit 2
fi
exact_private_route='type == "object" and
  ((keys | sort) == ["TCP", "Web"]) and
  (.TCP | type == "object" and length == 1 and
    .["443"] == {"HTTPS": true}) and
  (.Web | type == "object" and length == 1 and has($host) and
    .[$host] == {
      "Handlers": {
        "/": {"Proxy": "http://127.0.0.1:8000"}
      }
    })'
if ! jq --exit-status --arg host "${expected_host_port}" "${exact_private_route}" \
  >/dev/null 2>&1 <<< "${serve_status}"; then
  echo "remote gateway: unavailable (Serve contains an unexpected handler)"
  exit 2
fi
if ! funnel_status="$("${tailscale_cli}" funnel status --json 2>/dev/null)"; then
  echo "remote gateway: unavailable (Funnel state could not be verified)"
  exit 2
fi
if ! jq --exit-status 'type == "object"' \
  >/dev/null 2>&1 <<< "${funnel_status}"; then
  echo "remote gateway: unavailable (Funnel state could not be verified)"
  exit 2
fi
if ! jq --exit-status "${funnel_is_clear}" \
  >/dev/null 2>&1 <<< "${funnel_status}"; then
  echo "remote gateway: unsafe (public Funnel configuration detected)"
  exit 2
fi
echo "remote gateway: configured for the private tailnet"
