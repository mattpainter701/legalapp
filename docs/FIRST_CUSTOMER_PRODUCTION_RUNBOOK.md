# First-customer production runbook

This is the release gate for the initial Call Intake + Tasks + Zoom Phone customer. MCP is not part of this launch and must remain disabled.

## 1. Provision production configuration

Generate secrets on the target host; never paste their values into Git, tickets, or command output:

```bash
umask 077
openssl rand -hex 32   # POSTGRES_PASSWORD
openssl rand -hex 32   # CLARITY_APP_PASSWORD
openssl rand -hex 32   # REDIS_PASSWORD
openssl rand -hex 32   # LITELLM_DB_PASSWORD
openssl rand -hex 32   # LITELLM_API_KEY / platform keys as applicable
openssl rand -hex 32   # LITELLM_SALT_KEY (generate once; never rotate with the API key)
```

Set these non-secret relationships in `.env`:

- `MIGRATOR_DATABASE_URL` uses the `legalapp` owner and is used only by Alembic.
- `APP_DATABASE_URL` uses `clarity_app`; this role must be `NOSUPERUSER NOBYPASSRLS`.
- `DATABASE_URL` may remain the owner URL for operator tooling; runtime Compose explicitly maps `DATABASE_URL=APP_DATABASE_URL`.
- `REDIS_URL` includes the Redis password and the `redis` service hostname.
- `LITELLM_SALT_KEY` is a dedicated, permanent encryption key for values in the LiteLLM database. Back it up with the environment and keep it unchanged when `LITELLM_API_KEY` rotates. The image is digest-pinned; an upstream one-shot migrator and an exact reviewed schema reconciler must both finish before the proxy can start. Runtime schema writes are disabled, and monitoring requires a zero Prisma diff plus authenticated model discovery.
- `TOKEN_ENCRYPTION_KEYS` is newest-first (`new_key,old_key`) during staged rotation. Keep the old key until all stored credentials have been re-encrypted and verified. This first-customer preflight deliberately requires both distinct keys; do not collapse the keyring until rotation and provider reconnect tests are recorded.
- `MCP_PRODUCT_ENABLED=false`. When `MCP_SERVER_URL` is non-empty, `MCP_UPSTREAM_API_KEY` must be a separate 32+ character server credential.
- `PUBLIC_SIGNUP_ENABLED=false` and `VITE_PUBLIC_SIGNUP_ENABLED=false`. New
  tenants are operator/invite-provisioned until paid conversion and expiry
  enforcement are implemented and proven; marketing CTAs use `VITE_CONTACT_URL`.
- `VITE_CONTACT_URL` is the verified sales/contact destination used by the public site.
- `UPLOADS_HOST_DIR` is one absolute, non-symlink host directory used by Compose
  and backup tooling in both topologies. For the existing Skynet checkout set
  `/home/varta/legalapp/uploads`; a conventional VPS may use
  `/data/legalapp/uploads`. Deployment changes only the directory root to
  UID/GID `10001:10001` (mode 0750), never recursively rewrites customer files,
  then proves write/read/delete through the backend container.
- `OFFSITE_BACKUP_REQUIRED=true`. Deployment will not build or migrate until a
  fresh encrypted Restic snapshot succeeds or the bounded one-time manual
  off-host handshake below is consumed.
- When Restic is not configured, `OFFSITE_RESTORE_PUBLIC_KEY_FILE` points to the
  pinned public half of a dedicated recovery-evidence signing key. Keep the
  private half only in the encrypted off-host recovery environment.
- `ZOOM_REQUIRED_TENANT_ID` is the sold tenant UUID and
  `ZOOM_REQUIRED_TENANT_PLAN=intake-only`. Strict checks are bound to that exact
  active tenant and plan; another configured/demo tenant cannot satisfy launch.
- `EMAIL_ENABLED` must be explicit. It may remain `false` only during an
  explicitly non-go-live bootstrap deployment while the SMTP account is being provisioned,
  but assignment alerts are then logged as unavailable and explicit reminder
  requests return a clear service-unavailable error. Tasks and intake records
  remain durable even when an alert cannot be sent. For customer go-live, set
  it to `true` and provide `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER`,
  `EMAIL_PASS`, and a provider-authorized `EMAIL_FROM` address.
- Zoom Phone requires the tenant-owned app stored through Admin > Zoom. Copy the firm's exact Zoom Account ID from Zoom Account Management > Account Profile; this explicit binding avoids an extra Zoom user-profile scope. A token account is compared when Zoom supplies one, but every new grant remains `account_verification_required` until a correctly signed v3 call event matches `payload.account_id` and the pending grant successfully fetches that exact call from Zoom. The event proves the app account; the fetch proves grant access. Shared platform/S2S Phone credentials are prohibited. The production check requires an active tenant app secret, refresh token, exact app-to-grant account match, `healthy` verification state, required read scopes, and public CRC. Remove unused scopes in the Zoom Marketplace app and reauthorize the tenant before go-live; the application cannot revoke provider-side grants.

Validate without printing secret values:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml bash scripts/prod_env_preflight.sh
```

On a new public host, point DNS at the instance, open inbound TCP 80/443 only, and provision the first certificate before deployment:

```bash
bash nginx/init-letsencrypt.sh "$DOMAIN" operations@example.com
```

The certificate initializer and renewal cron accept the same `ENV_FILE` and
space-separated `COMPOSE_FILES` as deployment. For Lightsail, export
`COMPOSE_FILES="docker-compose.yml docker-compose.prod.yml"` before initializing;
the installed renewal cron records the resolved files.

## 2. Prove a fresh host

The rehearsal uses a unique Compose project, generated non-production secrets, empty volumes, randomized loopback-only HTTP/TLS ports, and automatic teardown:

```bash
bash scripts/rehearse_fresh_host.sh
FRESH_HOST_TOPOLOGY=base-prod bash scripts/rehearse_fresh_host.sh
```

It must prove all of the following:

- first-boot creation of the `clarity_app` role;
- migration to the current Alembic head;
- API and frontend container health;
- nginx configuration validation plus internal and loopback-host HTTP/self-signed TLS routing to the API and frontend;
- `clarity_app` is not superuser and cannot bypass RLS;
- the dedicated scheduler writes exactly one reusable heartbeat row for a seeded active tenant;
- `/health/readiness` reports disk, database, Redis, scheduler, and durable queue healthy.
- the absolute upload bind is owned by UID/GID 10001 and a real backend process
  can write, read, and delete a probe through `/app/uploads`;
- both `hypervisor` and `base-prod` variants resolve and pass, including the
  dedicated scheduler healthcheck.

The same proof can be run manually from the **Fresh-host production rehearsal** GitHub workflow.

Migration evidence recorded 2026-07-10: a clean PostgreSQL 16 database owned by
a `NOSUPERUSER NOBYPASSRLS` role upgraded from an empty schema through
`090_zoom_account_binding`. The 089 duplicate-key rehearsal failed closed with
both rows unchanged, `FORCE ROW LEVEL SECURITY` restored, and the Alembic
version still at 088; after operator cleanup, partial-index `ON CONFLICT`
inference passed. A seeded 089 Zoom app/grant then upgraded to 090 with the
account mapping backfilled, both source tables returned to FORCE RLS, and zero
rows visible without tenant context. Downgrade to 089 and re-upgrade to 090
preserved the source rows and reproduced the binding.

## 3. Back up and rehearse restore

Before and after deployment, create a custom-format dump, checksum it, copy it off-host, verify the remote checksum, and restore it into an isolated pgvector PostgreSQL 16 container. Never restore a rehearsal over production.

The generalized recurring command supports any Restic repository (S3-compatible, SFTP, Azure, or local removable/off-host storage):

```bash
export RESTIC_REPOSITORY='s3:https://object.example/legalapp-production'
export RESTIC_PASSWORD_FILE='/run/secrets/legalapp-restic-password'
export AWS_ACCESS_KEY_ID='from-secret-manager'
export AWS_SECRET_ACCESS_KEY='from-secret-manager'
OFFSITE_BACKUP_REQUIRED=true bash scripts/backup_db.sh
bash scripts/restore_rehearsal.sh
```

`backup_db.sh` refuses corrupt dumps and makes each database dump/count manifest
from one exported PostgreSQL snapshot. It packages uploads once into an immutable
archive while hashing the exact archived bytes, rejects symlinks or concurrent
tree changes, and writes a sorted path/size/SHA-256 manifest. The encrypted
Restic snapshot includes both databases, the upload archive/manifest, TLS
material, and temporary production environment/key escrow.
`restore_rehearsal.sh` restores both databases into disposable `--network none`
containers, verifies checksums/counts, safely extracts only regular upload paths,
and verifies every upload hash plus key escrow presence. Redis is rebuildable
operational state; PostgreSQL remains the durable job and business record.

After configuring the separately escrowed Restic repository/password, install
and prove the randomized persistent daily user timer (retention is deliberately
disabled unless an operator separately supplies the destructive confirmation):

```bash
ENV_FILE=.env COMPOSE_FILES="docker-compose.hypervisor.yml" bash scripts/install_backup_timer.sh
sudo loginctl enable-linger "$USER"   # once, if installer reports lingering disabled
systemctl --user start legalapp-backup.service
systemctl --user status legalapp-backup.service
systemctl --user list-timers legalapp-backup.timer
journalctl --user -u legalapp-backup.service --since today
```

The Restic repository password and any S3/SFTP/cloud credentials must be
escrowed separately from both the application host and the Restic repository.
Otherwise a total-host-loss snapshot is encrypted but unrecoverable.

Pre-release evidence recorded 2026-07-10: an off-host dump copy had matching local/remote SHA-256 (`596aedd5…`); an isolated pgvector PostgreSQL 16 restore reached revision `085_durable_jobs`, restored 102 tables, and matched all 175 recorded table/tenant count metrics. Repeat after the release migration and record the new head.

## 4. Deploy

The checked-out production revision is deployed through one hardened path. The script runs the secret/config preflight, captures a database dump and exact counts, builds the hypervisor topology, gates API startup on the owner-role migrator, recreates the dedicated scheduler, checks nginx/readiness/Zoom/TLS, and refuses any post-deploy count decrease:

```bash
bash scripts/deploy_prod.sh --build
```

Restic is preferred and automatic. If it is not configured, start deploy with a
new, nonexistent `OFFSITE_BACKUP_ATTESTATION_FILE` path. Before any build or
migration, the script creates one mode-600 recovery bundle containing the exact
snapshot-consistent dual dumps/counts, immutable uploads, TLS files, and key
escrow, prints its SHA-256, and waits at most 15 minutes. Copy the bundle and
checksum off-host, verify them there, create the short-lived evidence using the
printed `offsite_backup_attestation.py create` command, and copy the mode-600
attestation to the requested path. Deployment atomically consumes its nonce and
keeps an audit receipt; the file cannot be a persistent `.env` boolean or reused.
The attestation command requires the signed restore proof emitted by this
successful off-host command. Production verifies it against the pinned public
key, so a copy receipt or hand-written JSON cannot release the deployment:

```bash
OFFSITE_RESTORE_SIGNING_KEY_FILE=/secure/offhost/restore-evidence-private.pem \
  bash scripts/restore_manual_recovery_bundle.sh \
  /secure/offhost/legalapp-predeploy-recovery-<timestamp>.tar \
  /secure/offhost/legalapp-predeploy-recovery-<timestamp>.tar.sha256
```

That command safely extracts an exact-layout bundle, verifies every nested
checksum, restores LegalApp and LiteLLM into separate disposable
`--network none` PostgreSQL containers, compares every table/tenant count,
checks upload hashes, validates the TLS key pair and escrowed keyring, and emits
mode-600 bundle-bound `.restore-proof.json` and `.sig` files. Supply both to the
printed attestation command; only the public key is present on the VPS.
The manual bundle contains production key escrow: transfer it only over an
authenticated encrypted channel into access-controlled encrypted off-host
storage, and remove transient workstation copies after the restore proof.

On a genuinely empty host, Admin > Zoom cannot be reached until the first
deployment finishes. Use the explicit bootstrap exception for that first
infrastructure deployment only:

```bash
BOOTSTRAP_MODE=true bash scripts/deploy_prod.sh --build
```

Bootstrap success is not deployment completion and is **NOT GO-LIVE**. It skips
only the Zoom launch checks; database, Redis, scheduler, queue, HTTP, TLS,
migration, and post-deploy data-count checks still run. After signing in,
configure the tenant-owned Zoom app and grant, then run the strict production
check below. Never use bootstrap mode for a customer launch or an ordinary
production update.

The default is the single-file hypervisor topology. A clean Lightsail or other
VPS can use the equivalent base-plus-production topology without publishing the
database, Redis, backend, or frontend directly:

```bash
COMPOSE_FILES="docker-compose.yml docker-compose.prod.yml" bash scripts/deploy_prod.sh --build
```

Only nginx binds host ports 80/443 in either production topology.
Successful deployment tags prior/current application image IDs and records a
mode-600 release manifest under
`${XDG_STATE_HOME:-$HOME/.local/state}/clarity-legal/releases` for rollback.

Alembic also prefers `MIGRATOR_DATABASE_URL` when invoked inside `backend`, so an idempotent compatibility migration command cannot accidentally grant DDL to `clarity_app`.

## 5. Verify the release

Run the complete operator gate:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml bash scripts/production_check.sh
```

`ZOOM_REQUIRED=true` is the validated default. It requires
`ZOOM_REQUIRED_TENANT_ID` to be active, configured, healthy, and on the expected
`intake-only` plan. This strict command must pass
after tenant Zoom setup and before go-live, even when the host was created with
`BOOTSTRAP_MODE=true`.

It fails on excessive disk use, missing/unhealthy containers, PostgreSQL, authenticated Redis, missing per-tenant scheduler heartbeats, stale/exhausted durable jobs, incomplete Zoom Phone configuration, failed Zoom CRC ingress, public HTTP, or a TLS certificate inside the 14-day floor. For the strict sold-tenant gate, email must be enabled: the check connects to SMTP, negotiates TLS where configured, authenticates, and disconnects without issuing `MAIL`, `RCPT`, or `DATA`. Only a `ZOOM_REQUIRED=false` bootstrap check may remain email-disabled, and that check explicitly reports that it is not go-live evidence.

Confirm scheduler ownership and RLS evidence:

```bash
docker compose -f docker-compose.hypervisor.yml ps backend scheduler migrator
docker compose -f docker-compose.hypervisor.yml exec -T postgres psql -U legalapp -d legalapp -c \
  "SELECT tenant_id, status, run_at FROM scheduler_logs WHERE agent_name='scheduler-heartbeat' ORDER BY tenant_id;"
```

There must be one current heartbeat row for every active tenant and none with a null tenant ID. API has `RUN_SCHEDULER=false`; scheduler has `RUN_SCHEDULER=true`.

## 6. Verify Zoom through production ingress

Register these exact HTTPS URLs in the Zoom app:

- OAuth callback: `https://<DOMAIN>/api/integrations/zoom-phone/callback`
- Event subscription: use the tenant-specific webhook URL shown in Admin > Zoom: `https://<DOMAIN>/api/integrations/zoom-phone/webhook/<tenant-id>`
- Required events: `phone.callee_call_element_completed` and
  `phone.caller_call_element_completed`. The older call-history-completed
  events remain compatibility inputs only and are not valid production proof.

In Admin > Zoom, enter the firm's Zoom Account ID exactly as shown under Zoom
Account Management > Account Profile. It is an identifier, not a credential,
and may be displayed after save. Do not add a user-profile scope to discover it.

Then:

1. Connect/reconnect the tenant from Admin > Zoom. The new grant must show **Account proof pending**. Re-authorize only to recover a wrong or revoked grant.
2. Place a real inbound test call so a correctly signed v3 call-element event reaches the tenant-specific webhook. Confirm its `payload.account_id` matches the saved app account and the worker uses the pending grant to fetch that exact call history/detail. Only their combined proof may atomically mark the grant healthy and import the call.
3. Run **Test connection**, then run `scripts/production_check.sh`; listing, synchronization, import, and the strict gate intentionally remain blocked until the signed event and matching provider fetch both succeed. The gate's CRC request must return an `encryptedToken` through nginx/TLS.
4. Confirm the call appears once in Call Intake.
5. Send one assignment to a controlled firm test mailbox and confirm receipt,
   sender alignment, links, text/HTML rendering, and reply handling. The safe
   production probe proves connectivity and authentication but intentionally
   does not send mail, so this external acceptance step cannot be automated
   without customer SMTP credentials.
6. Save the intake with a specific staff task. Confirm the task appears in Tasks, is tenant-scoped, and notification/read-receipt behavior works.

The automated regression is:

```bash
pytest backend/tests/test_intake_dashboard.py -k zoom_ingress_to_intake_to_assigned_task_e2e
```

## 7. Alerts

The scheduled **Production public health** workflow polls `/health/readiness`, the frontend, and TLS every ten minutes. It creates or updates one deduplicated `[production-alert]` GitHub issue on failure and closes it on recovery. Enable scheduled Actions and issue notifications for the operator repository.

`ALERT_WEBHOOK_URL` is an optional second delivery path for both the GitHub workflow and the on-host operator script. If configured, new-failure and recovery transitions are sent without repeating every interval. The public endpoint contains component states only—no tenant IDs, queue data, credentials, or infrastructure addresses.

By default the on-host monitor stores transition state outside the Git checkout
at `${XDG_STATE_HOME:-$HOME/.local/state}/clarity-legal/production-check.state`.
The state directory is mode 700. Set `MONITOR_STATE_FILE` only when the service
manager provides another persistent, operator-owned path.

## Go/no-go

Go only when fresh-host rehearsal, off-host restore, strict production check, real Zoom call-to-task, controlled-mailbox assignment delivery, and post-deploy backup all pass. `EMAIL_ENABLED=false` is accepted only for an explicitly non-go-live bootstrap check. Keep `MCP_PRODUCT_ENABLED=false`; do not market, issue, or accept MCP product keys for this customer.
