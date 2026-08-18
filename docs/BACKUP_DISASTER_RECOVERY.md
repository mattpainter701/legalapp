# Production backup and disaster recovery

Every production deployment creates a new encrypted Restic snapshot before any
service build, replacement, or migration. The GitHub runner is noninteractive:
if Restic, its password file, integrity check, or fresh snapshot proof fails,
the deployment stops before production mutation.

The backup includes the LegalApp database (including private vector data), the
LiteLLM database, immutable `uploads` artifact and manifest, TLS material when
present, and a temporary encrypted environment/key escrow. The successful job
writes a mode-600, atomic `backup-status.json` in `HOST_STATUS_HOST_DIR` for
readiness monitoring, plus a timestamped evidence record under
`backups/offsite-evidence/`.

CourtListener is deliberately classified separately: its `courtlistener_pgdata`,
`courtlistener_bulk`, and `legal_authority_cache` volumes are a rebuildable
public/legal-source corpus and are not in the LegalApp customer-data snapshot.

## CourtListener legal-authority RAG protection

When the public corpus would be expensive to rebuild, protect it with its own
encrypted Restic path; it is intentionally not folded into `backup_db.sh`.
Run this on the CourtListener host after setting its ordinary `.env` and an
escrowed Restic repository/password (never put either secret in CI):

```bash
COURTLISTENER_RAG_BACKUP_REQUIRED=true \
RESTIC_REPOSITORY=... RESTIC_PASSWORD_FILE=/run/secrets/courtlistener-restic-password \
bash scripts/courtlistener_rag_backup.sh
```

The job takes one exported PostgreSQL snapshot for a custom `pg_dump` and
dynamic exact table counts; then creates immutable, per-file hash manifests for
`courtlistener_bulk` and `legal_authority_cache` through read-only Compose
mounts. It verifies every checksum, runs `restic check`, tags the snapshot
`courtlistener-rag-production` plus its timestamp, and writes non-overwritable
snapshot evidence. With `COURTLISTENER_RAG_BACKUP_REQUIRED=true`, unavailable
Restic or credentials fails closed.

Rehearse recovery on an isolated host, before relying on a snapshot:

```bash
RESTIC_REPOSITORY=... RESTIC_PASSWORD_FILE=/run/secrets/courtlistener-restic-password \
bash scripts/courtlistener_rag_restore_rehearsal.sh
```

The rehearsal restores only into a temporary directory and an ephemeral
PostgreSQL container launched with `--network none`; it validates the dump and
both immutable file manifests/hashes and requires an exact restored table-count
match. It does not contact, attach to, or overwrite production volumes.

Install the separate hourly user timer only after the Restic repository and
password-file path are configured on the CourtListener host:

```bash
ENV_FILE=/home/varta/legalapp/.env \
bash scripts/install_courtlistener_rag_backup_timer.sh
```

The timer forces `COURTLISTENER_RAG_BACKUP_REQUIRED=true` and retains local RAG
artifacts for 48 hours only after a fresh Restic snapshot, repository check,
and immutable evidence file succeed. Its failure unit logs an alert and may
post to `COURTLISTENER_RAG_ALERT_WEBHOOK_URL` (or the generic
`ALERT_WEBHOOK_URL`). Neither credentials nor real repositories belong in CI.

Run a recovery rehearsal from an isolated host with Restic credentials:

```bash
RESTIC_REPOSITORY=... RESTIC_PASSWORD_FILE=... bash scripts/restore_rehearsal.sh
```

It restores both databases into network-isolated temporary containers, checks
archive hashes and exact row counts, verifies upload hashes, and confirms the
encrypted key escrow contains recovery-critical values. It never contacts or
restores over the production database. For the documented manual/offline path,
use `scripts/restore_manual_recovery_bundle.sh` and the existing signed,
short-lived attestation workflow.

Install recurring backups on the production account with:

```bash
ENV_FILE=/home/varta/legalapp/.env bash scripts/install_backup_timer.sh
```

The hourly systemd timer logs failures and posts an actionable message to
`ALERT_WEBHOOK_URL` when configured. Investigate with
`journalctl --user -u legalapp-backup.service`; a failed or stale status keeps
production readiness unhealthy and blocks the next deployment. After verified
off-site evidence exists, the timer retains two days of local recovery files to
bound production disk usage. Off-site retention and immutability remain storage
policy controls and must be configured independently.
