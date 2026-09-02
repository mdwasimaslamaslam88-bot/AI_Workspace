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
  path-free HTML5 file drag/drop through the authenticated uploader, and
  `work-station://` deep-link registration; native path interception stays off,
  so no filesystem permission or path-bearing IPC is required
- generic notifications only; sensitive response content is not used as a
  preview; the Settings panel requests OS permission explicitly and image or
  workflow completion/failure alerts contain only fixed generic text
- owner-configurable sign-in startup through Settings; the native plugin owns
  the platform startup entry and no credential is placed in startup arguments
- allowlisted `work-station://chat`, `work-station://settings`,
  `work-station://studio`, `work-station://memory`, `work-station://tools`, and
  `work-station://workflows` navigation (Studio opens the bounded tools panel);
  links containing parameters, credentials, fragments, or object identifiers
  are ignored
- explicit owner session naming, issuance, revocation, and token rotation
  through Settings; the replacement remains in the OS credential vault and
  invalidates only the prior token for this desktop session
- one-time device-token views enable native window content protection, clear on
  background/page hide, fail closed if protection cannot be enabled, and always
  disable the capture guard during cleanup; ordinary browsers receive the same
  background clearing without claiming OS-level capture prevention

## Linux

Install the official Tauri Linux prerequisites, Rust stable, and Node.js. On
this workstation, a user-owned native sysroot under `AI_Workspace_Runtimes` is
supported when system development packages cannot be installed.

```bash
./scripts/desktop_check.sh
npm run check:desktop:launch
```

The validated AppImage and Debian package are written below
`apps/desktop/src-tauri/target/release/bundle`. Installation of the package is
a separate owner action. The build uses a pinned AppImage runtime, remaps Rust
source roots, neutralizes equal-length third-party compiler roots, validates
AppStream metadata, and rejects local home paths in the executable and package
contents. When an X11 session and `xwininfo` are available, the default check
starts both the production executable and AppImage, confirms the expected
native WORK STATION window without taking a screenshot, and terminates only the
process groups it started. `check:desktop:launch` makes both smokes mandatory;
use `--skip-launch` on a deliberately headless packaging host.

Install the pinned AppImage type-2 runtime once on a Linux packaging host:

```bash
install -d -m 700 ~/.cache/tauri
runtime_tmp="$(mktemp ~/.cache/tauri/runtime-x86_64.XXXXXX)"
curl --fail --location --output "${runtime_tmp}" \
  https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64
printf '%s  %s\n' \
  '1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf' \
  "${runtime_tmp}" | sha256sum --check
install -m 0644 "${runtime_tmp}" ~/.cache/tauri/runtime-x86_64
find "${runtime_tmp}" -maxdepth 0 -type f -delete
```

## Private remote build

```bash
VITE_API_BASE_URL=https://workstation.example.ts.net \
  ./scripts/desktop_check.sh
```

Only use the workstation's actual Tailscale MagicDNS HTTPS origin. The bearer
token is entered at runtime and saved in the OS vault; it must not be set in the
build environment. The backend CORS allowlist must contain only the packaged
Tauri origins used by the target platforms: `tauri://localhost` for Linux and
macOS and `http://tauri.localhost` for Windows. The shipped local and remote
environment examples include these exact origins; the remote profile does not
retain the Vite development origin.

## Windows and macOS

The Tauri identifier, Windows `.ico`, macOS `.icns`, deep-link scheme, session
vault abstraction, and window behavior are shared. Platform-specific Tauri
configuration selects Linux packages, an NSIS installer on Windows,
and an application bundle plus DMG on macOS; the common configuration does not
hard-code a foreign platform's package type. The manual `Windows release
artifact` GitHub Actions workflow builds on `windows-latest`, runs shared/web
and Rust tests, creates a real NSIS installer and standalone PE executable,
inspects their metadata and contents, and performs a Windows process-launch
smoke before uploading checksummed artifacts. Download it with:

```bash
gh workflow run windows-release.yml --ref main
gh run watch --exit-status
gh run download RUN_ID --name Work_Station_Windows --dir /absolute/output
```

The manual `macOS release artifact` workflow uses a GitHub-hosted macOS target
to build and validate the application bundle and DMG, run a process-launch
smoke, and upload checksummed owner-sideload artifacts. Run and download it in
the same way with `macos-release.yml` and artifact name `Work_Station_macOS`.

The Windows artifact is unsigned. The macOS target-host workflow applies and
verifies an ad-hoc owner-sideload signature so its resource envelope is
internally consistent; it is not an Apple-trusted publisher signature. Trusted
publisher signing, Apple notarization, and auto-update signing require
owner-controlled platform certificates and remain external activation
boundaries.
Current Tauri target prerequisites remain documented at
<https://v2.tauri.app/start/prerequisites/>.
