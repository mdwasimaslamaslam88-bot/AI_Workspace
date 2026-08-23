#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
native_root="${HOME}/AI_Workspace_Runtimes/tauri-sysroot/root"
cargo_binary="${HOME}/.cargo/bin/cargo"

if [[ ! -x "${cargo_binary}" ]]; then
  echo "Rust is required for desktop validation." >&2
  exit 1
fi
export PATH="${HOME}/.cargo/bin:${PATH}"

if ! pkg-config --exists webkit2gtk-4.1 2>/dev/null; then
  if [[ ! -d "${native_root}/usr/lib/x86_64-linux-gnu/pkgconfig" ]]; then
    echo "Tauri system libraries or the documented user-owned sysroot are required." >&2
    exit 1
  fi
  export PATH="${native_root}/usr/bin:${PATH}"
  export PKG_CONFIG_SYSROOT_DIR="${native_root}"
  export PKG_CONFIG_PATH="${native_root}/usr/lib/x86_64-linux-gnu/pkgconfig:${native_root}/usr/share/pkgconfig"
  export LIBRARY_PATH="${native_root}/usr/lib/x86_64-linux-gnu"
  export LD_LIBRARY_PATH="${native_root}/usr/lib/x86_64-linux-gnu"
fi

npm run typecheck --workspace @work-station/desktop
(
  cd "${repository_root}/apps/desktop/src-tauri"
  "${cargo_binary}" test --locked
)
npm run build --workspace @work-station/desktop

package="${repository_root}/apps/desktop/src-tauri/target/release/bundle/deb/WORK STATION_0.1.0_amd64.deb"
binary="${repository_root}/apps/desktop/src-tauri/target/release/work-station-desktop"
[[ -s "${package}" && -x "${binary}" ]]
if ldd "${binary}" | rg -q "not found"; then
  echo "The desktop executable has unresolved shared libraries." >&2
  exit 1
fi
echo "desktop validation: Rust tests, production binary, and Debian package passed"
