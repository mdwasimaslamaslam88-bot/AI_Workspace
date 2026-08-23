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
npm run test --workspace @work-station/mobile
npm run typecheck
npm run lint
npm run build:web
npm run build:static --workspace @work-station/mobile
```

The PostgreSQL runner refuses any host except `127.0.0.1` and any database name
except `ai_workspace_test`. It also refuses to run unless `DATABASE_URL` points
to a different application database. Real runtime scripts apply the disposable
test database restriction.

## Environment modes

Safe templates live under `config/environments`:

- `development.env.example`: Vite plus loopback API
- `local.env.example`: compiled PWA served from the backend
- `remote-self-hosted.env.example`: private HTTPS gateway mode

The templates contain no secrets and are not runtime credentials.
