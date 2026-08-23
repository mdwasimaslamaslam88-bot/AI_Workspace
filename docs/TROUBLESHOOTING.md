# Troubleshooting

## Browser cannot connect

```bash
ss -ltnp | grep -E ':3000|:8000'
curl -i http://127.0.0.1:8000/api/v1/health/live
curl -i http://localhost:3000/
```

Local Vite uses `http://127.0.0.1:8000`. A production page served through
Tailscale uses its own HTTPS origin. Do not add a path, credential, remote HTTP
origin, or wildcard CORS entry to work around a connection problem.

## Authentication required

Confirm that the value entered is a user bearer token, not the provisioning
credential. Token rotation immediately invalidates the old token. Network
failure does not clear a valid saved session; a real HTTP 401 does.

Never paste tokens into commands, issue reports, URLs, or browser developer
console logs.

## Remote gateway unavailable

```bash
tailscale status
tailscale serve status
./scripts/check_remote_gateway.sh
```

Both devices must be enrolled in the owner's tailnet and allowed by its policy.
The repository configurator refuses to overwrite an existing Serve
configuration; inspect and reconcile it manually rather than using `serve
reset`. Funnel is not supported for this owner-only deployment.

## Backend not ready

`/api/v1/health/ready` returns only generic dependency states. Check local
PostgreSQL, Redis, and Ollama services without exposing their connection
strings. Run `alembic current` and `alembic check` from `backend`.

## AI runtime unavailable

The model selector distinguishes installed, runnable, insufficient-hardware,
and runtime-unavailable states. Verify the exact allowlist and loopback runtime;
do not fake availability or bypass hardware admission. ComfyUI and speech
runtimes may intentionally be off until requested.

## Desktop build

Run `./scripts/desktop_check.sh`. On Linux it uses system WebKit/GTK packages or
the documented user-owned sysroot. Windows installers must be built on Windows;
macOS bundles/signing must be built on macOS.

## Mobile build

Run the mobile test, typecheck, lint, and static export commands from
[MOBILE.md](MOBILE.md). A physical-device result requires an actual enrolled
device; static export is not a substitute. iOS signing requires macOS/Xcode and
an Apple Developer account.

## Backup failure

Confirm `pg_dump`/`pg_restore` are installed, the protected backend database
configuration is valid, and the encrypted destination is existing/writable and
outside the source tree. The tool redacts PostgreSQL command errors so database
credentials cannot appear in reports.
