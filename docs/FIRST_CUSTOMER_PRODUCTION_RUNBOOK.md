# First-customer production runbook

The concise evidence matrix for presentation and launch decisions is
[`CUSTOMER_PRESENTATION_CHECKLIST.md`](CUSTOMER_PRESENTATION_CHECKLIST.md).

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
- `PLATFORM_LEGACY_BOOTSTRAP_ENABLED=false` must be present explicitly. The
  static bridge is not an accepted production fallback even when its secret is
  currently empty.
- `PUBLIC_SIGNUP_ENABLED=false` and `VITE_PUBLIC_SIGNUP_ENABLED=false`. New
  tenants are operator/invite-provisioned until paid conversion and expiry
  enforcement are implemented and proven; marketing CTAs use `VITE_CONTACT_URL`.
- `VITE_CONTACT_URL=mailto:support@getlawhand.com` is the canonical sales,
  legal, privacy, and support destination used by the public site.
- `VITE_PUBLIC_SITE_URL=https://<DOMAIN>` is required and must match `DOMAIN`
  exactly (apart from one optional trailing slash). It is baked into legal-page
  canonicals, social metadata, `robots.txt`, and the sitemap, so rerun the
  frontend build whenever the production hostname changes.
- `UPLOADS_HOST_DIR` is one absolute, non-symlink host directory used by Compose
  and backup tooling in both topologies. For the existing Skynet checkout set
  `/home/varta/legalapp/uploads`; a conventional VPS may use
  `/data/legalapp/uploads`. Deployment changes only the directory root to
  UID/GID `10001:10001` (mode 0750), never recursively rewrites customer files,
  then proves write/read/delete through the backend container.
- `HOST_STATUS_HOST_DIR` is a dedicated, non-symlink host directory (for
  example `/home/varta/.local/state/clarity-legal/host-status` or
  `/data/legalapp/host-status`). Only this aggregate-status directory is mounted
  read-only at `/run/legalapp-host-status`; DockerRootDir, the Docker socket,
  and database paths are never mounted into the application. Keep
  `HOST_DISK_STATUS_FILE=/run/legalapp-host-status/disk-status.json` and
  `HEALTH_HOST_DISK_MAX_AGE_SECONDS=180`.
- `OFFSITE_BACKUP_REQUIRED=true`. Deployment will not build or migrate until a
  fresh encrypted Restic snapshot succeeds or the bounded one-time manual
  off-host handshake below is consumed.
- When Restic is not configured, `OFFSITE_RESTORE_PUBLIC_KEY_FILE` points to the
  pinned public half of a dedicated recovery-evidence signing key. Keep the
  private half only in the encrypted off-host recovery environment.
- `ZOOM_REQUIRED_TENANT_ID` is the sold tenant UUID and
  `ZOOM_REQUIRED_TENANT_PLAN=intake-only`. Strict checks are bound to that exact
  active tenant and plan; another configured/demo tenant cannot satisfy launch.
- `EMAIL_ENABLED=false` is the intentional production policy. LawHand does not
  operate an SMTP sender. Assignment alerts are logged as unavailable and
  explicit reminder requests return a clear service-unavailable error; tasks
  and intake records remain durable. Keep `EMAIL_FROM=support@getlawhand.com`
  as the canonical identity. Operator incidents are delivered by GitHub's
  scheduled production-health issues.
- Zoom Phone requires the tenant-owned app stored through Admin > Zoom. Save its client ID, client secret, and webhook secret; do not copy the numeric Account Number from Zoom Account Profile. OAuth plus an account call-history probe establishes API readiness immediately. A correctly signed completion event and exact provider fetch then learn or confirm Zoom's opaque `payload.account_id` for real-time delivery without blocking Test Connection or history sync. Shared platform/S2S Phone credentials are prohibited. The production check requires an active tenant app secret, refresh token, healthy API grant, required read scopes, public CRC, and live webhook/provider proof. Remove unused scopes in the Zoom Marketplace app and reauthorize only when the grant or scopes are invalid; the application cannot revoke provider-side grants.

For a general single-host VPS, provision at least 8 vCPU and 32 GB advertised memory.
The supported Lightsail starting size is the general-purpose
[2Xlarge-32GB Linux bundle](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html)
with 640 GB SSD; a 16 GB plan is not safe for this topology. Compose currently
defines 17.5 GiB of memory limits and 9 vCPU limits. Limits are ceilings, not
reservations: uncapped services, builds, migrations, Docker, and Linux consume
additional memory and disk, while CPU is time-shared. Preflight therefore
requires 8 online CPUs and 24 GiB guest-visible RAM. Every distinct filesystem
used by `UPLOADS_HOST_DIR`, the checkout/release backups, Docker's root, the
application/LiteLLM database binds, or any other bind in the exact resolved
production Compose model must provide at least 160 GiB total and 25 GiB free.
The 25 GiB value is the VPS profile floor, not always the effective minimum:
preflight reserves another 5 GiB for transient build and recovery artifacts and
requires enough free space for `df` to remain strictly below
`DISK_MAX_PERCENT` after that headroom is consumed. The larger requirement
wins. At the default 85% threshold, a 160 GiB usable filesystem requires about
30.6 GiB free before deployment: 16% of `df`'s used-plus-available capacity,
plus the 5 GiB headroom.
For the VPS topology, preflight additionally requires the reviewed database
sources `/data/legalapp/postgres` and `/data/legalapp/litellm-postgres`; do not
relocate them without updating and re-proving the topology and capacity gate.
Python 3 is required to inspect the canonical resolved Compose model safely.

The reviewed exception is the first-customer IONOS Memory Cube M: 4 vCPU,
16 GB advertised RAM, and 240 GB local disk. It is supported only with
`docker-compose.hypervisor.yml docker-compose.cube-m.yml`, two backend workers,
the checked-in resource ceilings, sequential image builds, and the `cube-m`
capacity profile (4 online CPUs, 14 GiB guest-visible RAM, 200 GiB total disk,
and at least 30 GiB free before the runtime threshold/build reserve is applied).
The approximately 129-GB CourtListener/vector database and 58-GB staged source
corpus remain on Skynet and are reached over a Tailscale-only authenticated
sidecar path. Do not attach or copy those volumes to the Cube's core disk.
The full placement, staging, cutover, and rollback procedure is
[`IONOS_CUTOVER_RUNBOOK.md`](IONOS_CUTOVER_RUNBOOK.md).

Validate without printing secret values:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml bash scripts/prod_env_preflight.sh
```

The capacity gate accepts only the repository's exact production Compose profiles
and inspects every resolved host bind source before deployment.
Base-plus-production uses the VPS disk floor above. The Skynet hypervisor keeps
the same 8 CPU / 24 GiB RAM floor and uses its established 80 GiB total /
15 GiB free profile floor on every distinct checked filesystem. The runtime
threshold reserve plus 5 GiB build headroom is layered on top. At the default
85% threshold, the computed requirement is 16% of `df`'s used-plus-available
capacity plus 5 GiB; preflight prints the exact effective requirement for each
filesystem. This deliberately prevents a host from passing preflight and then
crossing the runtime disk gate during the build. A reviewed exception for
another partitioned/nonstandard host is
process-only, requires a specific operational reason, and is not go-live
capacity evidence:

```bash
HOST_CAPACITY_OVERRIDE=true \
HOST_CAPACITY_OVERRIDE_REASON="change record: dedicated storage/capacity reviewed" \
  ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml \
  bash scripts/prod_env_preflight.sh
```

Never persist either override variable in `.env`. Resolve the undersizing or
record/load-test the nonstandard host before using the same process variables
with `deploy_prod.sh`.

The IONOS profile is selected only by the exact ordered pair:

```bash
ENV_FILE=/etc/lawhand/core.env \
COMPOSE_FILES="docker-compose.hypervisor.yml docker-compose.cube-m.yml" \
  bash scripts/prod_env_preflight.sh
```

`HOST_CAPACITY_OVERRIDE` is not Cube M acceptance evidence. Resize to Cube L if
the bounded load gate cannot retain the documented memory and latency headroom.

On a new hypervisor host, do not open public inbound 80/443. Nginx publishes
only loopback ports and cloudflared uses egress-only Tunnel connections. First
provision the private origin CA/certificate on the VM:

```bash
sudo bash scripts/provision_private_origin_tls.sh \
  --server-name origin.getlawhand.internal \
  --nginx-ssl-dir "$PWD/nginx/ssl" \
  --ca-export /etc/cloudflared/lawhand-origin-ca.pem
ORIGIN_TLS_SERVER_NAME=origin.getlawhand.internal \
ORIGIN_TLS_CA_FILE=/etc/cloudflared/lawhand-origin-ca.pem \
CLOUDFLARED_CONFIG_FILE=/etc/cloudflared/config.yml \
CLOUDFLARED_BIN=/usr/bin/cloudflared \
bash scripts/validate_private_origin_tls.sh --cert-only --require-production-ownership

# Make nginx serve the new leaf, then edit /etc/cloudflared/config.yml using
# ops/cloudflared/config.private-origin.example.yml as the reviewed pattern.
# Keep config.yml root-owned and non-writable by group/others (0644 is safe
# because tunnel credentials remain in the separate root-only JSON file).
docker compose -p legalapp --env-file .env -f docker-compose.hypervisor.yml \
  exec -T nginx nginx -s reload
sudo chown root:root /etc/cloudflared/config.yml
sudo chmod 0644 /etc/cloudflared/config.yml
sudo /usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
# On a new VM, install the service with the explicit reviewed config path. The
# recurring production check verifies this exact executable and --config argv.
sudo /usr/bin/cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl restart cloudflared
sudo systemctl is-active cloudflared
sudo systemctl show cloudflared --property=ExecStart --no-pager

ORIGIN_TLS_SERVER_NAME=origin.getlawhand.internal \
ORIGIN_TLS_CA_FILE=/etc/cloudflared/lawhand-origin-ca.pem \
CLOUDFLARED_CONFIG_FILE=/etc/cloudflared/config.yml \
CLOUDFLARED_BIN=/usr/bin/cloudflared \
bash scripts/validate_private_origin_tls.sh --require-production-ownership
```

The validator confirms the nginx certificate/key, SAN, validity floor, and
Cloudflare Tunnel `caPool`/`originServerName` pin. Keep the private CA on the
production VM only. The installed file-share agent calls the public
`https://getlawhand.com` endpoint and uses the operating system public CA store;
it must not be configured with this origin CA. Cloudflare Flexible mode,
plain-HTTP Tunnel services, and `noTLSVerify` are forbidden. Do not run
`nginx/init-letsencrypt.sh` or leave its `nginx/renew-cert.sh` cron installed:
either can overwrite the pinned private-origin leaf. The hypervisor Compose
ports are loopback-only (`127.0.0.1:80/443`); cloudflared must run on the VM
host and connect to that loopback listener.

Stage and validate the certificate before switching Tunnel ingress from HTTP to
HTTPS, then reload nginx, edit and validate cloudflared, and restart cloudflared
in a controlled window. Keep the catch-all `http_status:404` rule in place
during the switch. The safe order is: provision -> `--cert-only` validation ->
nginx reload -> edit/validate/restart cloudflared -> full validator.
Back up `/etc/lawhand/origin-tls` through a separately encrypted, root-only
recovery process; the root CA private key must never enter Git, GitHub Actions,
the cloudflared trust bundle, or an agent installer.

For routine leaf renewal, run the provisioner with `--force` before the
397-day leaf reaches the monitored validity floor. It reuses the existing root
CA, stages and validates the replacement transactionally, and keeps rollback
copies. Run `--cert-only`, reload nginx, then run the full validator and public
`/health` check. A routine leaf renewal does not require restarting
`cloudflared` because its pinned root does not change.

To rotate the private root CA without a trust gap, use the pending overlap
protocol. `--rotate-ca` writes the new nginx leaf and a dual-trust export
(new CA followed by old CA), then leaves a root-only pending marker. It does
not restart services:

```bash
sudo bash scripts/provision_private_origin_tls.sh --rotate-ca \
  --server-name origin.getlawhand.internal \
  --nginx-ssl-dir "$PWD/nginx/ssl" \
  --ca-export /etc/cloudflared/lawhand-origin-ca.pem

ORIGIN_TLS_SERVER_NAME=origin.getlawhand.internal \
ORIGIN_TLS_CA_FILE=/etc/cloudflared/lawhand-origin-ca.pem \
CLOUDFLARED_CONFIG_FILE=/etc/cloudflared/config.yml \
CLOUDFLARED_BIN=/usr/bin/cloudflared \
bash scripts/validate_private_origin_tls.sh --cert-only --require-production-ownership

# Load the dual bundle while nginx still serves the old in-memory leaf.
sudo systemctl restart cloudflared
sudo systemctl is-active cloudflared

# Load the newly provisioned leaf; the running Tunnel already trusts both roots.
docker compose -p legalapp --env-file .env -f docker-compose.hypervisor.yml \
  exec -T nginx nginx -s reload

# Prove the new leaf and the exact live dual-trust configuration before
# discarding the previous root.
ORIGIN_TLS_SERVER_NAME=origin.getlawhand.internal \
ORIGIN_TLS_CA_FILE=/etc/cloudflared/lawhand-origin-ca.pem \
CLOUDFLARED_CONFIG_FILE=/etc/cloudflared/config.yml \
CLOUDFLARED_BIN=/usr/bin/cloudflared \
bash scripts/validate_private_origin_tls.sh --require-production-ownership
curl --fail --silent --show-error https://getlawhand.com/health >/dev/null

sudo bash scripts/finalize_private_origin_ca_rotation.sh \
  --state-dir /etc/lawhand/origin-tls \
  --ca-export /etc/cloudflared/lawhand-origin-ca.pem

# Load the reduced current-only bundle after nginx serves the new leaf.
sudo systemctl restart cloudflared
sudo systemctl is-active cloudflared
ORIGIN_TLS_SERVER_NAME=origin.getlawhand.internal \
ORIGIN_TLS_CA_FILE=/etc/cloudflared/lawhand-origin-ca.pem \
CLOUDFLARED_CONFIG_FILE=/etc/cloudflared/config.yml \
CLOUDFLARED_BIN=/usr/bin/cloudflared \
bash scripts/validate_private_origin_tls.sh --require-production-ownership
curl --fail --silent --show-error https://getlawhand.com/health >/dev/null
```

The provisioner refuses another provisioning run while the pending marker is
present. The finalizer checks that the export contains exactly the recorded
old and new CA fingerprints, atomically reduces it to the current CA, and
performs no service restart. If any write fails, the export and marker are
restored.

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
- nginx configuration validation plus internal and loopback-host TLS routing to the API and frontend;
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

Migration `091_pdf_preview_evidence` adds a FORCE-RLS,
tenant-scoped evidence table for representative PDF activation previews and
exact matter/value-bound generation previews. Generation evidence is consumed
with its saved document, and ambiguous storage/database outcomes become
operator-reconcilable retry blocks. Migration
`092_zoom_phone_api_webhook_split` repairs numeric Zoom Account Number bindings
from the retired UI and separates usable Phone API grants from provider-proven
real-time webhook binding. Migrations `093_conf_call_content` and
`094_admin_conf_call_content` backfill confidential-call-content access for the
default internal User and Administrator roles. The current head is
`094_admin_conf_call_content`; a fresh deployment/restore proof must report 094
before this revision is released.

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
and prove the randomized persistent hourly user timer. Local recovery artifacts
are pruned only after verified off-site evidence exists and the systemd service
supplies the explicit destructive confirmation; off-site retention is managed
separately:

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

Release evidence recorded 2026-07-12: the exact dual-database recovery bundle
(`019aa4da5c57c873fc0035e72e58861e04ac6b2f54a0b8408f1f3af8c777fd2a`)
matched its off-host SHA-256 and passed an isolated clean-host restore of both
PostgreSQL 16 databases at LegalApp revision `090_zoom_account_binding`. The
signed restore proof also verified every recorded table/tenant count, immutable
upload hash, TLS key pair, and escrowed token-keyring material. Repeat this
bundle-bound restore and attestation for every production deployment.

## 4. Deploy

The checked-out production revision is deployed through one hardened path. The
script runs the secret/config preflight, captures a database dump and exact
counts, builds the hypervisor topology, and gates API startup on the owner-role
migrator. Immediately before recreation it stops the previous scheduler and
records the database clock; the replacement must then complete a heartbeat for
every active tenant after that marker. Only that release-specific proof can
advance to the bounded HTTP readiness poll (which shows each safe component
state). The script then checks nginx/Zoom/TLS and refuses any post-deploy count
decrease:

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
Deployment idempotently installs `legalapp-host-disk.timer` for the deploy user,
requires systemd user lingering, and proves one immediate run. If deployment
reports that lingering is disabled, run
`sudo loginctl enable-linger <deploy-user>` and rerun deployment; do not bypass
the timer gate. CI renders the same unit files used by the installer and runs
`systemd-analyze verify`; this is a syntax/dependency gate, not a substitute for
the live host lifecycle proof.

After every production deployment, retain this acceptance output with the
release record (run it as the deploy user, not root):

```bash
loginctl show-user "$USER" -p Linger --value
systemctl --user is-enabled legalapp-host-disk.timer
systemctl --user is-active legalapp-host-disk.timer
systemctl --user start legalapp-host-disk.service
systemctl --user show legalapp-host-disk.service -p Result -p ExecMainStatus
stat "$(grep '^HOST_STATUS_HOST_DIR=' .env | tail -1 | cut -d= -f2-)/disk-status.json"
curl -fsS "https://$DOMAIN/health/readiness"
```

Accept only `yes`, `enabled`, `active`, `Result=success`, `ExecMainStatus=0`, a
fresh regular artifact, and readiness with both `status=ok` and
`components.host_disks=ok`. The deployment check already enforces these live
conditions; the captured output is the operator evidence after remote start.
Successful deployment tags prior/current application image IDs and records a
mode-600 release manifest under
`${XDG_STATE_HOME:-$HOME/.local/state}/clarity-legal/releases` for rollback.

Alembic also prefers `MIGRATOR_DATABASE_URL` when invoked inside `backend`, so an idempotent compatibility migration command cannot accidentally grant DDL to `clarity_app`.

## 5. Verify the release

Run the complete operator gate:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml bash scripts/production_check.sh
```

For repeatable post-deploy evidence, dispatch the GitHub Actions
**Production acceptance** workflow from `main` with the exact deployed SHA.
It verifies that SHA is both current `main` and the `production` tag, then runs
the same strict host gate on Skynet and checks public readiness, host disks,
backups, frontend health, and `/api/version` commit identity. It is read-only
with respect to provider configuration and prints no secret values. The
runner's root-owned `lawhand-deploy-from-github` entrypoint must include its
`accept` operation before dispatching the workflow.

`ZOOM_REQUIRED=true` is the validated default. It requires
`ZOOM_REQUIRED_TENANT_ID` to be active, configured, healthy, and on the expected
`intake-only` plan. This strict command must pass
after tenant Zoom setup and before go-live, even when the host was created with
`BOOTSTRAP_MODE=true`.

It fails on excessive disk use, a missing/inactive host disk timer, a stale or
malformed aggregate, any monitored filesystem at the configured threshold,
missing/unhealthy containers, PostgreSQL, authenticated Redis, missing
per-tenant scheduler heartbeats, stale/exhausted durable jobs, incomplete Zoom
Phone configuration, failed Zoom CRC ingress, public HTTP, or a TLS certificate
inside the 14-day floor. The host probe deduplicates filesystem identities while
covering `DISK_PATH`, uploads, backups, every resolved Compose bind, and
DockerRootDir (which covers named volumes). The check verifies the intentional
email-disabled policy and relies on the GitHub production-health issue workflow
for operator incident delivery. If SMTP is ever explicitly enabled later, the
check still fails closed on incomplete or unauthenticated configuration.

Confirm scheduler ownership and RLS evidence:

```bash
docker compose -f docker-compose.hypervisor.yml ps backend scheduler migrator
docker compose -f docker-compose.hypervisor.yml exec -T postgres psql -U legalapp -d legalapp -c \
  "SELECT tenant_id, status, run_at FROM scheduler_logs WHERE agent_name='scheduler-heartbeat' ORDER BY tenant_id;"
```

There must be one current heartbeat row for every active tenant and none with a null tenant ID. API has `RUN_SCHEDULER=false`; scheduler has `RUN_SCHEDULER=true`.

## 6. Verify Zoom through production ingress

Follow the complete [Zoom Phone tenant app setup](ZOOM_PHONE_TENANT_APP_SETUP.md)
for customer prerequisites, current Marketplace screens, OAuth and webhook
secret handling, authorization, rotations, and troubleshooting. The checks
below are the release-gate summary, not a substitute for that onboarding
procedure.

Register these exact HTTPS URLs in the Zoom app:

- OAuth callback: `https://<DOMAIN>/api/integrations/zoom-phone/callback`
- Event subscription: use the tenant-specific webhook URL shown in Admin > Zoom: `https://<DOMAIN>/api/integrations/zoom-phone/webhook/<tenant-id>`
- Required events: `phone.callee_call_element_completed` and
  `phone.caller_call_element_completed`. Existing call-history-completed v2
  subscriptions remain compatible, while v3 is preferred for new apps.

Do not enter the numeric Account Number from Zoom Account Profile. Zoom's API
and webhook `account_id` is a different opaque identifier and is learned
automatically.

Then:

1. Connect the tenant from Admin > Zoom. Run **Test connection** and a manual history sync; both must work before webhook delivery is proven. Re-authorize only to recover a wrong, revoked, or under-scoped grant.
2. Place a real inbound test call so a correctly signed completion event reaches the tenant-specific webhook. Confirm the worker uses the same grant to fetch that exact call. The first event learns the opaque account binding atomically; later events must match it.
3. Run `scripts/production_check.sh`. The gate's CRC request must return an `encryptedToken` through nginx/TLS and its live provider proof must pass.
4. Confirm the call appears once in Call Intake.
5. Confirm assignment creation remains durable while the email delivery result
   reports `disabled`; the UI must not claim that an email was sent.
6. Save the intake with a specific staff task. Confirm the task appears in Tasks, is tenant-scoped, and notification/read-receipt behavior works.

The automated regression is:

```bash
pytest backend/tests/test_intake_dashboard.py -k zoom_ingress_to_intake_to_assigned_task_e2e
```

## 7. Alerts

The scheduled **Production public health** workflow polls `/health/readiness`,
requires the `host_disks` component to be present and healthy, checks the
frontend and TLS every ten minutes, creates or updates one deduplicated
`[production-alert]` GitHub issue on failure, and closes it on recovery. Enable
scheduled Actions and issue notifications for the operator repository. The host
timer refreshes its aggregate every minute; a stopped timer becomes stale and
alertable within three minutes.

`ALERT_WEBHOOK_URL` is an optional second delivery path for both the GitHub workflow and the on-host operator script. If configured, new-failure and recovery transitions are sent without repeating every interval. The public endpoint contains component states only—no tenant IDs, queue data, credentials, or infrastructure addresses.

By default the on-host monitor stores transition state outside the Git checkout
at `${XDG_STATE_HOME:-$HOME/.local/state}/clarity-legal/production-check.state`.
The state directory is mode 700. Set `MONITOR_STATE_FILE` only when the service
manager provides another persistent, operator-owned path.

## Go/no-go

Go only when fresh-host rehearsal, off-host restore, strict production check,
real Zoom call-to-task, durable assignment behavior with email disabled, GitHub
production-health issue delivery, and post-deploy backup all pass. Keep
`MCP_PRODUCT_ENABLED=false`; do not market, issue, or accept MCP product keys
for this customer.
