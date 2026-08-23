# Deployment

WORK STATION is self-hosted on the owner's Linux workstation. The deployment
unit is one loopback FastAPI process serving both the compiled PWA and the
authenticated API. PostgreSQL and Ollama remain separate local services;
ComfyUI and speech runtimes stay on-demand.

## Build

```bash
npm ci
backend/.venv/bin/pip install -r backend/requirements.txt
./scripts/postgres_integration_check.sh
npm run build:web
npm run build:static --workspace @work-station/mobile
./scripts/desktop_check.sh
npm run check:desktop:launch
```

On an Android build workstation, validate the managed native project without
creating a source-tree `android` directory:

```bash
WORK_STATION_ANDROID_SDK_ROOT=/absolute/path/to/android-sdk \
  ./scripts/mobile_check.sh --require-native-android
```

Set `WORK_STATION_WEB_ROOT` to the absolute `frontend/dist` directory. The
backend fails at startup if it does not contain a compiled `index.html`.

## Database

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

Do not use ORM metadata creation in deployment. Alembic is authoritative.

## Always-on user service

```bash
./scripts/install_user_services.sh
systemctl --user enable --now work-station.target
loginctl enable-linger "$USER"
```

The installer validates the compiled app and backend environment before
creating user units. It does not start services automatically. The backend
service binds `127.0.0.1:8000`, trusts proxy headers only from loopback, uses a
restrictive umask, has restart/time/resource bounds, and writes only to the
private data root. The target also enables a readiness timer for PostgreSQL,
Redis, and Ollama plus a remote-gateway timer that is skipped until the standard
`/usr/bin/tailscale` client is installed.

The installer also installs the disabled `work-station-backup.timer`. Configure
and enable it separately using [BACKUP.md](BACKUP.md); the main target never
chooses a backup destination or retention policy on the owner's behalf.

`loginctl enable-linger` may require local administrator policy and is therefore
an explicit owner action.

## Remote gateway

Complete [REMOTE_ACCESS.md](REMOTE_ACCESS.md) only after local deployment is
healthy. No service file binds the API or an AI runtime to a public interface.

## Platform packages

- Web/PWA: `frontend/dist`
- Linux desktop: Tauri Debian package under `apps/desktop/src-tauri/target`
- Windows/macOS: build on the corresponding operating system
- Android/iOS: use the configured EAS profiles with owner-controlled signing

Never copy `.env`, token files, private keys, database dumps, or asset backups
into a client package.
