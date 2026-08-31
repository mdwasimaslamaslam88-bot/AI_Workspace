# Backup

The backup tool captures the authoritative PostgreSQL state and, when
configured, the private asset tree. PostgreSQL includes conversation history,
memory, RAG metadata/embeddings, ownership, token digests, tool executions, and
workflow state. The asset archive includes owner documents and generated media.

It never copies `.env`, plaintext bearer/provisioning tokens, credential files,
private keys, runtime model installations, or certificates. Backups themselves
contain highly private owner content and must be encrypted at rest outside the
source tree.

## Create and verify

```bash
mkdir -p /path/to/encrypted-backups
./scripts/backup.sh /path/to/encrypted-backups
./scripts/verify_backup.sh \
  /path/to/encrypted-backups/work-station-YYYYMMDDTHHMMSSZ
```

The destination must already exist, be owner-only (`0700`), and remain outside
both Git and the configured asset tree. Symbolic-link destinations are refused.
`pg_dump` receives the database password only through its process environment,
never a command argument or report. The output contains:

- `database.dump`: custom-format PostgreSQL dump
- `assets.tar.gz`: optional safe relative asset archive
- `manifest.json`: format, timestamp, commit, and component names only
- `SHA256SUMS`: integrity checks

The published backup directory is mode `0700` and every contained dump,
archive, manifest and checksum file is mode `0600`. Independent verification
rejects permissive files, links and non-regular entries as well as content
integrity failures.

Every creation verifies all hashes, rejects extra/unsafe archive members, and
asks `pg_restore` to parse the dump before publishing the timestamped backup
directory. `verify_backup.sh` provides the same independent validation later.

## Schedule

The service installer includes an opt-in daily timer. It is deliberately not
part of `work-station.target` and remains disabled until the owner chooses an
encrypted destination:

```bash
install -d -m 0700 ~/.config/work-station
install -m 0600 config/environments/backup.env.example \
  ~/.config/work-station/backup.env
# Edit only WORK_STATION_BACKUP_DESTINATION and create it with mode 0700.
./scripts/install_user_services.sh
systemctl --user enable --now work-station-backup.timer
systemctl --user list-timers work-station-backup.timer
```

Monitor `work-station-backup.service` failures and retain at least one
disconnected copy. The timer never deletes old backups automatically. Apply the
retention policy only after separately verifying the copies being retained. Do
not synchronize raw backups to a public bucket.

Suggested retention for a single-owner workstation is seven daily, four weekly,
and six monthly verified snapshots, adjusted to available encrypted capacity.
