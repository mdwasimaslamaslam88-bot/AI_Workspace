# Private remote access

## Architecture

```text
owner device on the private tailnet
              |
        HTTPS / MagicDNS
              |
       Tailscale Serve :443
              |
      http://127.0.0.1:8000
          /           \
 compiled PWA      /api/v1 (bearer required)
                         |
          PostgreSQL and local AI runtimes
             remain on loopback/private storage
```

Tailscale Serve is used instead of Funnel. Serve makes the application
available only inside the enrolled tailnet and terminates HTTPS with an
automatically provisioned certificate. No router port-forward is required.
The backend continues to authorize every API request with its own bearer token
and deliberately does not treat forwarded Tailscale identity headers as API
authorization.

Official references:

- <https://tailscale.com/docs/features/tailscale-serve>
- <https://tailscale.com/docs/reference/tailscale-cli/serve>

## External prerequisite

Install Tailscale, enroll the workstation and each owner device in the same
tailnet, enable MagicDNS/HTTPS, and restrict the tailnet policy to the owner's
identity and devices. The service supports either the normal system client or
official static binaries under
`~/AI_Workspace_Runtimes/tailscale/current/`. The account actions cannot be
created by the repository without the owner's Tailscale authorization.

## Enable the private gateway

1. Build the web application and run the backend with the compiled web root.

   ```bash
   npm run build:web
   mkdir -p ~/.config/work-station
   cp config/environments/remote-self-hosted.env.example \
     ~/.config/work-station/backend.env
   chmod 600 ~/.config/work-station/backend.env
   ```

2. Replace only the documented absolute web path and the normal backend
   configuration in that private file. Do not add a token to it.

3. Install/start the user service, then configure Serve.

   ```bash
   ./scripts/install_user_services.sh
   systemctl --user enable --now work-station.target
   ./scripts/tailscale_cli.sh up
   ./scripts/configure_tailscale_serve.sh
   ```

   `tailscale_cli.sh up` prints Tailscale's enrollment URL when the workstation
   is not yet authorized. Complete that owner-account step, then run the Serve
   configurator. Do not use Funnel.

The configurator checks enrollment and backend health, accepts an existing
Serve configuration only when it exactly matches the private WORK STATION
route, otherwise refuses to overwrite it, binds HTTPS 443 to
`http://127.0.0.1:8000`, and never enables Funnel. Inspect status without
exposing application credentials:

```bash
./scripts/check_remote_gateway.sh
./scripts/tailscale_cli.sh serve status
```

Open the HTTPS MagicDNS URL printed by Tailscale on an enrolled owner device.
The production PWA automatically uses that same HTTPS origin for `/api/v1`.

## Client endpoints

- Web/PWA: same origin; no public API hostname is compiled into the bundle.
- Desktop: build with an HTTPS `VITE_API_BASE_URL` under the tailnet's `.ts.net`
  hostname. The desktop CSP permits loopback and `.ts.net` only, and the remote
  backend profile allowlists only the exact Linux/macOS and Windows Tauri
  application origins.
- Mobile: set `EXPO_PUBLIC_API_BASE_URL` to the HTTPS MagicDNS origin for the
  release build. It is an endpoint, never a token.

PostgreSQL 5432, Ollama 11434, ComfyUI 8188, Vite 3000, and audio worker ports
must remain unavailable outside the workstation.

## Production validation

The private gateway can be checked without changing production data:

```bash
./scripts/check_remote_gateway.sh
```

The authenticated desktop-to-mobile and mobile-to-desktop mission-continuation
smoke creates a temporary owner and two temporary workflows, then verifies
session revocation and removes that exact data. It is deliberately guarded
because it touches the production API and database:

```bash
WORK_STATION_ALLOW_PRODUCTION_REMOTE_SMOKE=YES \
  backend/.venv/bin/python scripts/remote_device_e2e.py
```

The smoke refuses an unsafe provisioning-token file, validates the uniform
credential-free authentication denial, and confirms that production database
row counts return to their starting values. It never prints tokens, owner IDs,
or the private tailnet hostname.

This test proves the private HTTPS/API/session/mission contract through the
active tailnet. It does not claim a physical Android/iOS device run or push
notification delivery; those remain separate device/provider gates.

## Step 7 verified production snapshot

On 2026-09-04, the guarded production smoke passed through the active private
Serve endpoint. It verified TLS and bearer authentication, desktop-to-mobile
and mobile-to-desktop workflow continuation, targeted session revocation and
logout, authenticated diagnostics, connector-monitor owner isolation, and
exact cleanup back to the starting database counts.

The same release gate passed the canonical backend, web, mobile, PostgreSQL,
browser/PWA, desktop package/launch, Android native build, security, and local
runtime suites. Native Windows and macOS hosted checks also succeeded on exact
commit `7fafcf800be66171b64b136b7a12ee8613205201`. See
`reports/STEP7_REMOTE_DEVICES.md` for hashes and the remaining physical-device,
push-provider, and distribution-signing boundaries.
