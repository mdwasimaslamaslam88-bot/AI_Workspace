#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
cd "${repository_root}"

if git ls-files | rg -q '(^|/)\.env$|\.(pem|key|p12|pfx)$|id_(rsa|ed25519)$'; then
  echo "Tracked credential material was detected." >&2
  exit 1
fi
if git grep -nE -- \
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}' \
  ':!scripts/security_audit.sh' >/dev/null; then
  echo "A tracked source file matches a private credential signature." >&2
  exit 1
fi
if git grep -nE -- '/home/[^ /]+' -- \
  backend/app frontend/src shared/src apps/mobile/src apps/desktop/src-tauri \
  >/dev/null; then
  echo "A tracked runtime/source file contains an absolute home path." >&2
  exit 1
fi
if rg -n --hidden --no-ignore \
  'USER_PROVISIONING_TOKEN_DIGEST|X-User-Provisioning-Token|\.ai_workspace_provisioning_token|-----BEGIN .*PRIVATE KEY-----|/home/[^ /]+' \
  frontend/dist apps/mobile/dist 2>/dev/null; then
  echo "A client build contains operator configuration, key material, or a host path." >&2
  exit 1
fi
if rg --pcre2 -n 'connect-src[^\"]* https:(?:[ ;])' \
  apps/desktop/src-tauri/tauri.conf.json >/dev/null; then
  echo "Desktop CSP contains an unrestricted HTTPS connect source." >&2
  exit 1
fi

backend/.venv/bin/python -m pip check
npm audit --audit-level=high
echo "security audit: tracked secrets, client artifacts, CSP, and dependency gates passed"
