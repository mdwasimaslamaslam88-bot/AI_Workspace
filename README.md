# WORK STATION

WORK STATION is an owner-only Personal AI product built around one authoritative
FastAPI/PostgreSQL backend. The canonical React application runs as an
installable PWA, a Tauri desktop application, and an Expo Android/iOS client.
Every client uses the same authenticated API and shared TypeScript contracts;
AI, authorization, storage, and workflow logic stay on the workstation.

The existing local platform provides text chat, vision, document intelligence
and RAG, explicit long-term memory, image generation/editing, speech-to-text,
text-to-speech, bounded tools/workflows, private assets, and hardware-aware
model admission. Conversation pin/archive state and immutable duplicate,
edit/resend, and regenerate branches are owner-scoped and shared across
clients. Availability shown by a client always comes from the backend.
The web and mobile model catalogs expose the backend-reported installed/ready
state, runtime, modality, context, scale class, hardware class, and capabilities;
clients never infer availability or download models automatically.

## Product surfaces

| Surface | Implementation | Session storage | Current distribution |
| --- | --- | --- | --- |
| Web/PWA | `frontend` | browser session storage | production Vite build |
| Desktop | `apps/desktop` (Tauri 2) | OS credential vault | Linux `.deb`; Windows/macOS projects prepared |
| Mobile | `apps/mobile` (Expo SDK 57) | Keychain/Keystore via SecureStore | Android/iOS project and release profiles |
| Shared client | `shared` | none | workspace TypeScript package |

The backend and all AI runtimes remain loopback-only. Worldwide access uses one
private Tailscale Serve HTTPS gateway to the compiled web application and API;
it does not expose development ports, PostgreSQL, Ollama, ComfyUI, or audio
runtimes.

## Quick start

```bash
npm install
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/alembic upgrade head
cd ..
npm run dev:web
```

In another terminal:

```bash
cd backend
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Provisioning remains an operator-only action. The PWA, desktop application,
and mobile app accept only a user bearer token; they never receive the
provisioning credential.

## Release verification

```bash
npm test
npm run build
npm run package
npm run check:mobile
npm run check:desktop
npm run check:postgres
npm run check:browser
npm run security-audit
npm run check
npm run e2e
npm run release
```

`check` is the complete static/local release gate; `e2e` starts a user-owned,
SCRAM-authenticated disposable PostgreSQL cluster on loopback and runs the real
compiled-PWA browser flow plus AI runtime smokes without touching the application
database. The browser flow proves service-worker control, bearer connection,
current-user resolution, conversation creation, real chat, private-cache
isolation, and logout without retaining credentials or test data. `release`
combines both and additionally requires a clean,
synchronized `HEAD == main == origin/main`. Its equivalent direct form is
`./scripts/release_check.sh --with-runtime --require-clean`.
When an Android SDK root is configured, `check` also runs a disposable native
Android compile. Use `npm run check:mobile:native` to require that gate instead
of allowing a platform-independent skip.
On an inspectable X11 session, `check` also launches the packaged Linux desktop
window without capturing screen content. Use `npm run check:desktop:launch` to
require that gate.

Install the pinned browser runtime once into the private runtime root before
using `check:browser`, `e2e`, or the full runtime release gate:

```bash
install -d -m 700 ~/AI_Workspace_Runtimes/playwright
PLAYWRIGHT_BROWSERS_PATH=~/AI_Workspace_Runtimes/playwright \
  npx playwright install chromium
WORK_STATION_PLAYWRIGHT_BROWSERS_PATH=~/AI_Workspace_Runtimes/playwright \
  npm run check:browser
```

## Documentation

- [Local development](docs/LOCAL_DEVELOPMENT.md)
- [Remote access](docs/REMOTE_ACCESS.md)
- [Desktop](docs/DESKTOP.md)
- [Mobile](docs/MOBILE.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Security](docs/SECURITY.md)
- [Backup](docs/BACKUP.md) and [recovery](docs/RECOVERY.md)
- [Operations](docs/OPERATIONS.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Backend AI architecture](backend/docs/local_ai.md)
