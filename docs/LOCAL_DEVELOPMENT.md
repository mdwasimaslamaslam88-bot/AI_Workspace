# Local development

## Prerequisites

- Python 3.12 and the packages in `backend/requirements.txt`
- Node.js 24 and npm
- PostgreSQL with the application and disposable test databases
- Ollama on loopback when local generation is required
- FFmpeg and FFprobe at `/usr/bin/ffmpeg` and `/usr/bin/ffprobe`

Copy `backend/.env.example` to `backend/.env`, keep it mode `0600`, and fill
only local values. Never copy a bearer or provisioning token into a frontend,
mobile, or desktop environment file.

## Development processes

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
npm run dev:web
```

Open `http://localhost:3000`. The development frontend defaults to
`http://127.0.0.1:8000`; an explicit `VITE_API_BASE_URL` may contain only a
credential-free origin. Non-loopback origins must use HTTPS.

## Test and build

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/python tests/db/run_postgres_integration.py
.venv/bin/alembic check
.venv/bin/python -m compileall -q app scripts tests
cd ..
npm run test:web
npm run test:a11y --workspace @work-station/web
npm run test --workspace @work-station/mobile
npm run typecheck
npm run lint
npm run build:web
npm run build:static --workspace @work-station/mobile
./scripts/mobile_check.sh
./scripts/desktop_check.sh
```

Set `WORK_STATION_ANDROID_SDK_ROOT` and add
`--require-native-android` when the local Android SDK is installed. The native
gate uses a disposable prebuild directory and leaves the managed Expo project
unchanged.

The accessibility command runs axe WCAG A/AA structural checks over the owner
connection, conversation/chat/model workspace, and Settings surfaces. The same
test is included in the full web suite. Color contrast is verified against the
fixed theme CSS variables with WCAG relative-luminance calculations; axe's
pixel-layout contrast rule is disabled because jsdom has no rendering engine.

The PostgreSQL runner refuses any host except `127.0.0.1` and any database name
except `ai_workspace_test`. It also refuses to run unless `DATABASE_URL` points
to a different application database. Real runtime scripts apply the disposable
test database restriction before cleanup: both URLs must exist, their
host/port/database identities must differ, and only the approved loopback test
URL is selected inside the smoke process.

If both URLs currently identify `ai_workspace_test`, a PostgreSQL administrator
must create a separate application database once. For example, substitute the
existing application role (never a password) in this administrator command:

```bash
sudo -u postgres createdb --owner=<application-role> ai_workspace
```

Then update only the protected backend `DATABASE_URL` to identify
`127.0.0.1/ai_workspace`, keep `TEST_DATABASE_URL` on
`127.0.0.1/ai_workspace_test`, and apply the application migrations:

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

Do not work around the guard by pointing integration tests at the application
database. Creating a database requires PostgreSQL administrator authority and
is intentionally not attempted by the release script.

## Environment modes

Safe templates live under `config/environments`:

- `development.env.example`: Vite plus loopback API
- `local.env.example`: compiled PWA served from the backend
- `remote-self-hosted.env.example`: private HTTPS gateway mode

The templates contain no secrets and are not runtime credentials.
