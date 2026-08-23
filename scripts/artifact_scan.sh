#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
cd "${repository_root}"

if git ls-files | rg -q '(^|/)(__pycache__|\.pytest_cache|node_modules|dist|target)/|\.(pyc|pyo|sqlite3?|dump|tar\.gz)$'; then
  echo "Generated artifacts are tracked by Git." >&2
  exit 1
fi
if git ls-files --others --exclude-standard | rg -q .; then
  echo "Unexpected untracked files remain:" >&2
  git ls-files --others --exclude-standard >&2
  exit 1
fi
echo "artifact scan: no tracked build/runtime artifacts or unexpected files"
