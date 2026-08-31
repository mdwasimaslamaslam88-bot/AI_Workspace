# Rollback and recovery

There are three separate recovery mechanisms.

## Application update rollback

The managed self-update layout retains the previous immutable release path when
UPDATE atomically switches `current`. `check_health_and_rollback` accepts a fixed
health probe. A false result or probe exception atomically switches `current`
back to the previous managed release and records `rolled_back` with
`post_activation_health_failed`. If no prior managed release exists, it fails
closed and reports that automatic rollback was unavailable.

The CLI health command accepts only the exact loopback readiness path:

```bash
backend/.venv/bin/python scripts/self_update_tool.py \
  --state-root /absolute/private/self-update \
  health --url http://127.0.0.1:8000/api/v1/health/ready
```

An external service supervisor must restart the managed service after a symlink
change and call this health gate. The current repository-installed service is
not claimed to use the managed symlink unless it was configured that way.

## Source/configuration recovery

Every update checkpoint contains a verified Git bundle for the previous commit,
tracked dependency/routing snapshots and encrypted private configuration. The
bundle is independently parsed with `git bundle verify`; every checkpoint file
is SHA-256 checked, the checksum index is HMAC-authenticated, and symlinks or
group/world-accessible checkpoint entries are refused before activation or
restoration.

## Owner data recovery

`scripts/backup_tool.py` creates and verifies PostgreSQL custom dumps and an
optional safe asset archive. Restore requires an explicit disposable database
URL and refuses the configured application database. Asset restore requires an
existing empty destination and rejects absolute, traversal, link and device
members. See [BACKUP.md](BACKUP.md) and [RECOVERY.md](RECOVERY.md).

The update checkpoint stores only a verified reference to such a data backup;
it does not copy or restore production data automatically. This separation
prevents a code rollback from silently overwriting newer conversations, memory
or assets. Database migrations must remain backward-compatible or supply a
separately tested recovery procedure.
