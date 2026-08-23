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
.venv/bin/alembic check
.venv/bin/python -m compileall -q app scripts tests
cd ..
./scripts/postgres_integration_check.sh
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

The repository-level PostgreSQL gate uses the installed PostgreSQL server
binaries to initialize a randomly named, mode-700 temporary cluster. It binds
only a random `127.0.0.1` port, generates a transient SCRAM credential without
printing or writing its plaintext to disk, creates only `ai_workspace_test`,
runs the migration chain and integration suite, then stops and removes the
entire cluster. The
protected application URL is loaded before the child test URL is overridden,
so interpolated local environments cannot redirect the gate to application
data. Git state is compared before and after.

The lower-level `backend/tests/db/run_postgres_integration.py` remains
fail-closed for operators who intentionally use an already configured test
database: it accepts only `127.0.0.1/ai_workspace_test` and requires an identity
separate from `DATABASE_URL`. Never bypass that guard or point tests at the
application database.

## Environment modes

Safe templates live under `config/environments`:

- `development.env.example`: Vite plus loopback API
- `local.env.example`: compiled PWA served from the backend
- `remote-self-hosted.env.example`: private HTTPS gateway mode

The templates contain no secrets and are not runtime credentials.
