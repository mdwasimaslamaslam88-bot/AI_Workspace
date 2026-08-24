#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
temporary_root="$(mktemp -d)"
trap 'rm -rf -- "${temporary_root}"' EXIT

mkdir -p -- "${temporary_root}/bin"
fake_tailscale="${temporary_root}/bin/tailscale"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'serve_json() {' \
  '  case "${WORK_STATION_GATEWAY_TEST_CASE:-}" in' \
  '    missing) printf "%s\n" "{}" ;;' \
  '    wrong-target)' \
  '      printf "%s\n" "{\"TCP\":{\"443\":{\"HTTPS\":true}},\"Web\":{\"work-station.example.ts.net:443\":{\"Handlers\":{\"/\":{\"Proxy\":\"http://127.0.0.1:3000\"}}}}}" ;;' \
  '    public)' \
  '      printf "%s\n" "{\"TCP\":{\"443\":{\"HTTPS\":true}},\"Web\":{\"work-station.example.ts.net:443\":{\"Handlers\":{\"/\":{\"Proxy\":\"http://127.0.0.1:8000\"}}}},\"AllowFunnel\":{\"work-station.example.ts.net:443\":true}}" ;;' \
  '    extra-proxy)' \
  '      printf "%s\n" "{\"TCP\":{\"443\":{\"HTTPS\":true}},\"Web\":{\"work-station.example.ts.net:443\":{\"Handlers\":{\"/\":{\"Proxy\":\"http://127.0.0.1:8000\"},\"/models\":{\"Proxy\":\"http://127.0.0.1:11434\"}}}}}" ;;' \
  '    file-handler)' \
  '      printf "%s\n" "{\"TCP\":{\"443\":{\"HTTPS\":true}},\"Web\":{\"work-station.example.ts.net:443\":{\"Handlers\":{\"/\":{\"Proxy\":\"http://127.0.0.1:8000\"},\"/files\":{\"Path\":\"/private\"}}}}}" ;;' \
  '    existing-serve) printf "%s\n" "{\"configured\":true}" ;;' \
  '    *)' \
  '      printf "%s\n" "{\"TCP\":{\"443\":{\"HTTPS\":true}},\"Web\":{\"work-station.example.ts.net:443\":{\"Handlers\":{\"/\":{\"Proxy\":\"http://127.0.0.1:8000\"}}}}}" ;;' \
  '  esac' \
  '}' \
  'case "${1:-} ${2:-} ${3:-}" in' \
  '  "version  ") exit 0 ;;' \
  '  "status --json ")' \
  '    if [[ "${WORK_STATION_GATEWAY_TEST_CASE:-}" == "not-enrolled" ]]; then' \
  '      printf "%s\n" "{\"BackendState\":\"NeedsLogin\"}"' \
  '    else' \
  '      printf "%s\n" "{\"BackendState\":\"Running\",\"Self\":{\"DNSName\":\"work-station.example.ts.net.\"}}"' \
  '    fi ;;' \
  '  "serve status --json")' \
  '    if [[ "${WORK_STATION_GATEWAY_CONFIGURE_TEST:-}" == "true" &&' \
  '      "${WORK_STATION_GATEWAY_TEST_CASE:-}" == "valid" &&' \
  '      ! -e "${WORK_STATION_GATEWAY_TEST_STATE}" ]]; then' \
  '      printf "%s\n" "{}"' \
  '    else' \
  '      serve_json' \
  '    fi ;;' \
  '  "funnel status --json")' \
  '    case "${WORK_STATION_GATEWAY_TEST_CASE:-}" in' \
  '      funnel)' \
  '        printf "%s\n" "{\"AllowFunnel\":{\"work-station.example.ts.net:443\":true}}" ;;' \
  '      funnel-error) exit 1 ;;' \
  '      *) serve_json ;;' \
  '    esac ;;' \
  '  "serve --yes --bg")' \
  '    [[ "${4:-}" == "--https=443" && "${5:-}" == "http://127.0.0.1:8000" ]]' \
  '    : > "${WORK_STATION_GATEWAY_TEST_STATE}" ;;' \
  '  *) exit 1 ;;' \
  'esac' > "${fake_tailscale}"
chmod 0700 -- "${fake_tailscale}"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "${temporary_root}/bin/curl"
chmod 0700 -- "${temporary_root}/bin/curl"

run_case() {
  local case_name="$1"
  local expected_status="$2"
  local expected_message="$3"
  local output
  local actual_status=0
  output="$(
    PATH="${temporary_root}/bin:/usr/bin:/bin" \
      WORK_STATION_TAILSCALE_CLI="${fake_tailscale}" \
      WORK_STATION_GATEWAY_TEST_CASE="${case_name}" \
      "${script_directory}/check_remote_gateway.sh" 2>&1
  )" || actual_status=$?
  if [[ "${actual_status}" -ne "${expected_status}" ]]; then
    echo "remote gateway check test failed for ${case_name}" >&2
    exit 1
  fi
  if [[ "${output}" != "${expected_message}" ]]; then
    echo "remote gateway check returned unexpected redacted output" >&2
    exit 1
  fi
}

run_case valid 0 "remote gateway: configured for the private tailnet"
run_case missing 2 "remote gateway: unavailable (Serve is not configured)"
run_case wrong-target 2 "remote gateway: unavailable (Serve does not match WORK STATION)"
run_case public 2 "remote gateway: unsafe (public Funnel exposure detected)"
run_case extra-proxy 2 "remote gateway: unavailable (Serve contains an unexpected handler)"
run_case file-handler 2 "remote gateway: unavailable (Serve contains an unexpected handler)"
run_case funnel 2 "remote gateway: unsafe (public Funnel configuration detected)"
run_case funnel-error 2 "remote gateway: unavailable (Funnel state could not be verified)"

run_configure_case() {
  local case_name="$1"
  local expected_status="$2"
  local expected_message="$3"
  local output
  local actual_status=0
  output="$(
    PATH="${temporary_root}/bin:/usr/bin:/bin" \
      WORK_STATION_TAILSCALE_CLI="${fake_tailscale}" \
      WORK_STATION_GATEWAY_CONFIGURE_TEST=true \
      WORK_STATION_GATEWAY_TEST_CASE="${case_name}" \
      WORK_STATION_GATEWAY_TEST_STATE="${temporary_root}/${case_name}.state" \
      "${script_directory}/configure_tailscale_serve.sh" 2>&1
  )" || actual_status=$?
  if [[ "${actual_status}" -ne "${expected_status}" ]]; then
    echo "remote gateway configure test failed for ${case_name}" >&2
    exit 1
  fi
  if [[ "${output}" != "${expected_message}" ]]; then
    echo "remote gateway configure returned unexpected redacted output" >&2
    exit 1
  fi
}

run_configure_case valid 0 "Private HTTPS Serve is active for the enrolled tailnet."
run_configure_case not-enrolled 1 "This workstation is not enrolled in a Tailscale tailnet."
run_configure_case existing-exact 0 "Private HTTPS Serve is active for the enrolled tailnet."
run_configure_case existing-serve 1 "An existing Tailscale Serve configuration was detected; refusing to overwrite it."
run_configure_case funnel 1 "A public Funnel configuration was detected; refusing to configure Serve."

echo "remote gateway check: private target and Funnel guards passed"
