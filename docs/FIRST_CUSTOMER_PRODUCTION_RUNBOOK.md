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
```

Set these non-secret relationships in `.env`:

- `MIGRATOR_DATABASE_URL` uses the `legalapp` owner and is used only by Alembic.
- `APP_DATABASE_URL` uses `clarity_app`; this role must be `NOSUPERUSER NOBYPASSRLS`.
- `DATABASE_URL` may remain the owner URL for operator tooling; runtime Compose explicitly maps `DATABASE_URL=APP_DATABASE_URL`.
- `REDIS_URL` includes the Redis password and the `redis` service hostname.
- `TOKEN_ENCRYPTION_KEYS` is newest-first (`new_key,old_key`) during staged rotation. Keep the old key until all stored credentials have been re-encrypted and verified. This first-customer preflight deliberately requires both distinct keys; do not collapse the keyring until rotation and provider reconnect tests are recorded.
- `MCP_PRODUCT_ENABLED=false`. When `MCP_SERVER_URL` is non-empty, `MCP_UPSTREAM_API_KEY` must be a separate 32+ character server credential.
- `VITE_CONTACT_URL` is the verified sales/contact destination used by the public site.
- Zoom may use the platform environment app or the tenant-owned Zoom Phone app stored through Admin > Zoom. The production check validates that an active tenant has both an app secret and OAuth grant.

Validate without printing secret values:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml bash scripts/prod_env_preflight.sh
```

On a new public host, point DNS at the instance, open inbound TCP 80/443 only, and provision the first certificate before deployment:

```bash
bash nginx/init-letsencrypt.sh "$DOMAIN" operations@example.com
```

The certificate initializer and renewal cron use the same hypervisor Compose topology as deployment.

## 2. Prove a fresh host

The rehearsal uses a unique Compose project, generated non-production secrets, empty volumes, randomized loopback-only HTTP/TLS ports, and automatic teardown:

```bash
bash scripts/rehearse_fresh_host.sh
```

It must prove all of the following:

- first-boot creation of the `clarity_app` role;
- migration to the current Alembic head;
- API and frontend container health;
- nginx configuration validation plus internal and loopback-host HTTP/self-signed TLS routing to the API and frontend;
- `clarity_app` is not superuser and cannot bypass RLS;
- the dedicated scheduler writes exactly one reusable heartbeat row for a seeded active tenant;
- `/health/readiness` reports disk, database, Redis, scheduler, and durable queue healthy.

The same proof can be run manually from the **Fresh-host production rehearsal** GitHub workflow.

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

`backup_db.sh` refuses corrupt dumps, writes SHA-256 and schema/count manifests, includes uploads, creates an encrypted off-host snapshot, and samples repository data. `restore_rehearsal.sh` restores into a disposable `--network none` database and compares the checksum, Alembic version, key row counts, and upload count.

Pre-release evidence recorded 2026-07-10: an off-host dump copy had matching local/remote SHA-256 (`596aedd5…`); an isolated pgvector PostgreSQL 16 restore reached revision `085_durable_jobs`, restored 102 tables, and matched all 175 recorded table/tenant count metrics. Repeat after the release migration and record the new head.

## 4. Deploy

The checked-out production revision is deployed through one hardened path. The script runs the secret/config preflight, captures a database dump and exact counts, builds the hypervisor topology, gates API startup on the owner-role migrator, recreates the dedicated scheduler, checks nginx/readiness/Zoom/TLS, and refuses any post-deploy count decrease:

```bash
bash scripts/deploy_prod.sh --build
```

The default is the single-file hypervisor topology. A clean Lightsail or other
VPS can use the equivalent base-plus-production topology without publishing the
database, Redis, backend, or frontend directly:

```bash
COMPOSE_FILES="docker-compose.yml docker-compose.prod.yml" bash scripts/deploy_prod.sh --build
```

Only nginx binds host ports 80/443 in either production topology.

Alembic also prefers `MIGRATOR_DATABASE_URL` when invoked inside `backend`, so an idempotent compatibility migration command cannot accidentally grant DDL to `clarity_app`.

## 5. Verify the release

Run the complete operator gate:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml bash scripts/production_check.sh
```

It fails on excessive disk use, missing/unhealthy containers, PostgreSQL, authenticated Redis, missing per-tenant scheduler heartbeats, stale/exhausted durable jobs, incomplete Zoom Phone configuration, failed Zoom CRC ingress, public HTTP, or a TLS certificate inside the 14-day floor.

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

Then:

1. Connect/reconnect the tenant from Admin > Zoom and run the API connection test.
2. Run `scripts/production_check.sh`; its CRC request must return an `encryptedToken` through nginx/TLS.
3. Place a real inbound test call. Confirm it appears once in Call Intake.
4. Save the intake with a specific staff task. Confirm the task appears in Tasks, is tenant-scoped, and notification/read-receipt behavior works.

The automated regression is:

```bash
pytest backend/tests/test_intake_dashboard.py -k zoom_ingress_to_intake_to_assigned_task_e2e
```

## 7. Alerts

The scheduled **Production public health** workflow polls `/health/readiness`, the frontend, and TLS every ten minutes. It creates or updates one deduplicated `[production-alert]` GitHub issue on failure and closes it on recovery. Enable scheduled Actions and issue notifications for the operator repository.

`ALERT_WEBHOOK_URL` is an optional second delivery path for both the GitHub workflow and the on-host operator script. If configured, new-failure and recovery transitions are sent without repeating every interval. The public endpoint contains component states only—no tenant IDs, queue data, credentials, or infrastructure addresses.

## Go/no-go

Go only when fresh-host rehearsal, off-host restore, production check, real Zoom call-to-task, and post-deploy backup all pass. Keep `MCP_PRODUCT_ENABLED=false`; do not market, issue, or accept MCP product keys for this customer.
