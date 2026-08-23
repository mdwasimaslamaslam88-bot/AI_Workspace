# Desktop application

`apps/desktop` is a Tauri 2 shell around the canonical production frontend. It
does not contain AI or authorization logic.

## Security and native behavior

- bearer session stored through the OS credential vault
- fixed credential service/account identifiers; token values are never logged
- least-privilege Tauri capability for notifications, autostart, deep links,
  window state, and core window behavior
- no shell, process, filesystem, or unrestricted HTTP Tauri permission
- loopback or private `.ts.net` network destinations enforced by CSP
- system tray, single-instance behavior, close-to-tray, persisted window state,
  native drag/drop, and `work-station://` deep-link registration
- generic notifications only; sensitive response content is not used as a
  preview; the Settings panel requests OS permission explicitly and image or
  workflow completion/failure alerts contain only fixed generic text
- owner-configurable sign-in startup through Settings; the native plugin owns
  the platform startup entry and no credential is placed in startup arguments
- explicit owner session naming, issuance, revocation, and token rotation
  through Settings; the replacement remains in the OS credential vault and
  invalidates only the prior token for this desktop session

## Linux

Install the official Tauri Linux prerequisites, Rust stable, and Node.js. On
this workstation, a user-owned native sysroot under `AI_Workspace_Runtimes` is
supported when system development packages cannot be installed.

```bash
./scripts/desktop_check.sh
```

The validated package is written below
`apps/desktop/src-tauri/target/release/bundle/deb`. Installation of the package
is a separate owner action.

## Private remote build

```bash
VITE_API_BASE_URL=https://workstation.example.ts.net \
  ./scripts/desktop_check.sh
```

Only use the workstation's actual Tailscale MagicDNS HTTPS origin. The bearer
token is entered at runtime and saved in the OS vault; it must not be set in the
build environment.

## Windows and macOS

The Tauri identifier, Windows `.ico`, macOS `.icns`, deep-link scheme, session
vault abstraction, and window behavior are shared. Build installers on the
target operating system with the current Tauri prerequisites:
<https://v2.tauri.app/start/prerequisites/>. Code signing, notarization, and
auto-update signing require owner-controlled platform certificates and are not
generated or embedded by this repository.
