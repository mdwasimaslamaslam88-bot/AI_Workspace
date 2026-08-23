# Recovery

Recovery is intentionally fail-closed. The restore tool refuses the configured
application database and requires a separately supplied disposable database
URL plus an exact confirmation flag.

## Validate before restoring

```bash
./scripts/verify_backup.sh /path/to/work-station-backup
```

## Disposable restore rehearsal

Create an empty PostgreSQL database that is not the live application database.
Keep its credential out of shell history by exporting it from a protected local
environment file or password manager, then run:

```bash
export WORK_STATION_CONFIRM_DISPOSABLE_RESTORE=YES
export WORK_STATION_RESTORE_DATABASE_URL='postgresql://.../disposable_restore'
mkdir -m 700 /path/to/empty-restored-assets
./scripts/restore.sh /path/to/work-station-backup \
  --asset-destination /path/to/empty-restored-assets
unset WORK_STATION_RESTORE_DATABASE_URL WORK_STATION_CONFIRM_DISPOSABLE_RESTORE
```

The asset destination must be an existing empty directory outside the source
tree. The archive contains no links/devices and every member is rooted below
`assets/`.

## Application recovery

1. Stop the backend and remote gateway.
2. Preserve the failed data set; do not overwrite the only copy.
3. Verify the selected backup and rehearse it against a disposable database.
4. Create a new empty production database and private asset directory.
5. Restore into those new targets, update the protected backend environment,
   and run `alembic upgrade head` followed by `alembic check`.
6. Start the loopback backend, verify `/health/live`, `/health/ready`, owner
   authentication, chat, and owned media before re-enabling Serve.
7. Rotate the owner bearer token if device compromise was involved.

Do not restore configuration secrets or signing keys from Git. Recreate them
through their owner-controlled systems.
