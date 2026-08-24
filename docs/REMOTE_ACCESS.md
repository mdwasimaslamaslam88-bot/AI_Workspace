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

The configurator checks enrollment and backend health, refuses an existing
Serve configuration, binds HTTPS 443 to `http://127.0.0.1:8000`, and never
enables Funnel. Inspect status without exposing application credentials:

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
