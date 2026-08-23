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

The destination must already exist and must be outside Git. `pg_dump` receives
the database password only through its process environment, never a command
argument or report. The output contains:

- `database.dump`: custom-format PostgreSQL dump
- `assets.tar.gz`: optional safe relative asset archive
- `manifest.json`: format, timestamp, commit, and component names only
- `SHA256SUMS`: integrity checks

Verification checks all hashes, rejects extra/unsafe archive members, and asks
`pg_restore` to parse the dump. A backup is not considered usable until this
verification passes.

## Schedule

Use an owner-only systemd timer or encrypted backup product to invoke
`scripts/backup.sh`. Set directory permissions to `0700`, monitor failures, and
retain at least one disconnected copy. Do not synchronize raw backups to a
public bucket.

Suggested retention for a single-owner workstation is seven daily, four weekly,
and six monthly verified snapshots, adjusted to available encrypted capacity.
