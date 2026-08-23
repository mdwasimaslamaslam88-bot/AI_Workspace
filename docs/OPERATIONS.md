# Operations

## Service layout

- PostgreSQL: existing local service; never publicly bind port 5432
- Ollama: existing loopback service; never expose port 11434
- WORK STATION backend/PWA: user service on `127.0.0.1:8000`
- Tailscale Serve: private tailnet HTTPS proxy to the backend
- ComfyUI, faster-whisper, Piper: started only when their bounded runtime is
  required; no public listener

## Daily checks

```bash
systemctl --user status work-station-backend.service
systemctl --user status work-station-health.timer
systemctl --user status work-station-remote-health.timer
curl --fail --silent http://127.0.0.1:8000/api/v1/health/ready
./scripts/check_remote_gateway.sh
```

`work-station-health.timer` checks full backend readiness, including the local
database, Redis, and Ollama. `work-station-remote-health.timer` validates the
tailnet-only HTTPS proxy and rejects a wrong backend target or any detectable
Funnel configuration; it is conditionally skipped while Tailscale is absent.

The authenticated Settings view provides fixed `ready`, `unavailable`, or
`unconfigured` states for backend, PostgreSQL, Redis, Ollama, vision, image,
speech, storage, remote gateway, and GPU. It shows only a bounded GPU model
label, VRAM, and class—never URLs, credentials, filesystem paths, or private
content.

## Logs

```bash
journalctl --user -u work-station-backend.service --since today
```

Logs contain event names, method/status, request IDs, and fixed dependency
labels. Authorization and provisioning headers, request bodies, model content,
database URLs, and filesystem paths must not be logged.

## Updates

```bash
git fetch origin
git status --short --branch
git pull --ff-only origin main
npm ci
backend/.venv/bin/pip install -r backend/requirements.txt
npm run build:web
cd backend && .venv/bin/alembic upgrade head && cd ..
./scripts/release_check.sh
systemctl --user restart work-station-backend.service
```

Take and verify a backup before migrations. Never force-push, reset a dirty
worktree, or delete live data as an update shortcut.

## Capacity and future GPUs

The backend detects RAM/VRAM at startup and recomputes current model admission.
Clients render the model registry's installed/runnable/capability/resource
facts. Replacing or adding GPUs changes runtime eligibility without changing
the client API or data model. Do not install every model automatically.

## Notifications

Desktop and mobile notifications remain generic by default (completion or
failure, no prompt/response content). Remote push delivery is disabled until an
owner-controlled provider and device-revocation path are explicitly configured.
The packaged desktop app exposes notification permission and sign-in startup in
Settings. Desktop image/workflow alerts and mobile task alerts contain only
fixed WORK STATION copy; private result details remain inside the authenticated
application.
