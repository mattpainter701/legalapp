# Production Hardening (Workstream D)

Operator reference for the container, TLS, and reverse-proxy hardening applied in
the production-readiness sprint. Pair this with `docs/SPRINT_PROD_READINESS.md`.

## 1. Non-root containers

`backend/Dockerfile` now runs as an unprivileged user.

- Fixed **UID/GID `10001`** (`appuser`). A fixed id lets you chown host-mounted
  volumes to a known owner.
- The absolute `UPLOADS_HOST_DIR` bind mounted at `/app/uploads` must be writable
  by that UID. `deploy_prod.sh` changes only the directory root to UID/GID 10001
  and mode 0750, then proves a backend write/read/delete. Do not recursively
  `chown` existing customer files as a generic deployment step.
- Python deps install to `/usr/local` (world-readable), so the non-root user can
  still import them.
- A stdlib `HEALTHCHECK` hits `/health` (the slim image has no `curl`).

**Base-image pinning:** production Dockerfiles pin Python, Node, nginx, and
LiteLLM bases by digest. The base, local, hypervisor, and CourtListener Compose
files also pin PostgreSQL/pgvector and Redis references by digest. Human-readable
tags remain beside the digest for maintenance context. A dependency update must
change the reviewed digest, rebuild, run the full test/rehearsal gates, and
regenerate the SBOM inventory.

**Image scanning:** add a container scan (e.g. Trivy) to CI and fail on
HIGH/CRITICAL CVEs:
```bash
trivy image --severity HIGH,CRITICAL --exit-code 1 <image>
```

## 2. TLS & security headers (nginx)

The production listeners in `nginx/nginx.conf` enforce:

- `ssl_protocols TLSv1.2 TLSv1.3;` only.
- `Strict-Transport-Security: max-age=63072000; includeSubDomains` on direct
  TLS and on edge-terminated HTTPS only when the forwarding peer is in the
  reviewed Cloudflare/cloudflared ranges.
- HTTP 301 for every untrusted/plain request except ACME HTTP-01. A direct
  client cannot bypass the redirect merely by spoofing `X-Forwarded-Proto`.
- API locations inherit the same HSTS, CSP, frame, content-type, referrer,
  permissions, and robots policies; the SSE proxy disables buffering without
  declaring a location-level `add_header` that would shadow them.
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`,
  `Referrer-Policy: strict-origin-when-cross-origin`.
- A conservative `Content-Security-Policy`. **Caveat:** the CSP may need tuning
  for the SPA (fonts, inline styles, API origin). It is intentionally tight —
  relax specific directives if assets are blocked, rather than removing it.
- `client_max_body_size 55m;` — aligns with `MAX_FILE_SIZE_MB=50` plus multipart
  overhead. Keep these two in sync.

## 3. Trusted-proxy client IP (anti-spoofing)

`X-Forwarded-For` is appended left-to-right by each proxy, so the **leftmost**
entries are client-controlled and spoofable. `_client_ip` in
`backend/app/middleware/rate_limit.py` now takes the client IP from the **right**
of the list — `xff[-TRUSTED_PROXY_HOPS]` — i.e. the entry your own infrastructure
appended.

- Set **`TRUSTED_PROXY_HOPS`** (config / env) to the exact number of reverse
  proxies between the public internet and the app. A single nginx hop → `1`.
- Too small and a spoofed value leaks through; too large and a proxy's own IP is
  used instead of the client's. nginx must forward
  `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` (it does).

## 4. Cloudflare tunnel — real-IP and rate limiting

### The problem

When using a **cloudflared tunnel**, traffic does not arrive
at nginx from Cloudflare's public edge IPs. Instead, cloudflared connects from inside
the Docker network (e.g., `172.24.0.0/16`). The `set_real_ip_from` block lists
only Cloudflare's public ranges, so nginx's `real_ip_module` never activates, and
`$remote_addr` stays as the tunnel container's Docker IP.

**Effect:** every user shares one rate-limit bucket. The `auth` zone (10 r/m, burst 5)
and `oauth` zone (30 r/m, burst 15) are exhausted by a handful of concurrent logins,
returning 429 to everyone.

### Current fix (PoC / self-hosted)

`nginx/nginx.conf` now adds the RFC-1918 Docker ranges to `set_real_ip_from`:

```nginx
set_real_ip_from 172.16.0.0/12;   # Docker bridge networks
set_real_ip_from 10.0.0.0/8;      # Docker overlay / cloud VPC ranges
real_ip_header CF-Connecting-IP;   # Cloudflare/cloudflared forwards this
```

cloudflared forwards `CF-Connecting-IP` with the real browser IP, so once nginx
trusts the tunnel container nginx properly replaces `$remote_addr` and each user
gets their own rate-limit bucket.

**Security trade-off:** any container on the Docker network could forge
`CF-Connecting-IP`. Acceptable in a single-host Docker Compose environment where
only trusted containers share the bridge.

### Cloud production — choose one

| Deployment model | nginx config change |
|-|-|
| **cloudflared tunnel** (same arch, cloud VM) | Same fix — add the VPC/internal subnet to `set_real_ip_from`. Find it with `docker network inspect`. |
| **Direct Cloudflare proxy** (no tunnel, public ingress port 80/443) | No change needed — traffic arrives from Cloudflare's public edge IPs already in `set_real_ip_from`. `CF-Connecting-IP` is real. |
| **Cloud load balancer** (AWS ALB, GCP LB, etc.) | Replace `real_ip_header CF-Connecting-IP` with `real_ip_header X-Forwarded-For` and add the LB's subnet to `set_real_ip_from`. Also set `TRUSTED_PROXY_HOPS` to match the proxy depth. |

### Verify the fix is working

After deploy, hit an auth endpoint and check the nginx access log:

```bash
docker exec legalapp-nginx-1 tail -20 /var/log/nginx/access.log
```

The `$remote_addr` column in the log should show real client IPs (e.g., `1.2.3.4`),
not the Docker bridge IP (`172.24.0.x`). If it still shows the bridge IP, the
`set_real_ip_from` range doesn't cover your network.

## 5. RLS-enabled runtime DB cutover

RLS is already defined and forced in tables, but enforcement is only real when the
app connects as a non-owner, no-`BYPASSRLS` role.

- Fresh databases create the role through
  `scripts/init_clarity_app_role.sh` mounted in `/docker-entrypoint-initdb.d`.
  For an existing database, run `backend/scripts/provision_app_role.sql` once
  as the database owner with its placeholders replaced through a protected
  operator workflow.
- Set `APP_DATABASE_URL` in production env to the `clarity_app` DSN:
  - Docker-local example:
    `postgresql+asyncpg://clarity_app:...@postgres:5432/legalapp`
  - Use the managed provider's TLS DSN for an off-host database.
- Deploy services. `backend` and `scheduler` in compose now read:
  - `DATABASE_URL=${APP_DATABASE_URL}` in production topologies.
- Keep migrations on owner role:
  - `migrator` resolves `DATABASE_URL` from `MIGRATOR_DATABASE_URL`.
- Confirm startup logs show `DB role check passed` and direct DB checks show:
  - `rolsuper = false` and `rolbypassrls = false` for `clarity_app`.

## 6. Secrets

- The current Compose implementation reads a host `.env` and injects values as
  container environment variables. Protect it with owner-only permissions,
  restrict host/Docker access, exclude it from backups that are not encrypted,
  and never print it during preflight. Native Docker secrets or a cloud secret
  manager remain a hardening improvement; do not claim that integration exists
  today.
- Rotate `SECRET_KEY`, `PLATFORM_TOKEN_SIGNING_KEY`, hashed bootstrap
  credentials, the staged Fernet keyring, the dedicated MCP upstream key, DB,
  LiteLLM, and Redis passwords; document the rotation cadence. Leave the legacy
  `PLATFORM_SECRET_KEY` bridge unset.
- `REDIS_PASSWORD` is enforced in `docker-compose.prod.yml`
  (`redis-server --requirepass`).

Validate configuration without printing values:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml \
  bash scripts/prod_env_preflight.sh
```

Production preflight also requires the private-origin TLS contract:
`ORIGIN_TLS_SERVER_NAME`, `ORIGIN_TLS_CA_FILE`, `CLOUDFLARED_CONFIG_FILE`, and
the absolute `CLOUDFLARED_BIN=/usr/bin/cloudflared`. It invokes
`scripts/validate_private_origin_tls.sh`
and fails closed if nginx's certificate/key or the Tunnel `caPool` and
`originServerName` pin is missing. The Tunnel-to-VM hop is TLS 1.2/1.3 only;
customer file-share agents use public HTTPS system trust and never receive the
private origin CA. Flexible mode, plain-HTTP origin services, and
`noTLSVerify` are prohibited.

Private-origin root rotation uses an overlap window to avoid a trust gap. Run
`scripts/provision_private_origin_tls.sh --rotate-ca` to install the new leaf
and export a new-plus-old CA bundle. Validate it, restart `cloudflared` to load
the dual bundle, reload nginx to serve the new leaf, then run
`scripts/finalize_private_origin_ca_rotation.sh`. Finally restart
`cloudflared` once more to load the current-only bundle and run the full
validator. Neither script restarts services; the pending root-only marker
blocks another provisioning run until finalization succeeds. Both scripts
rollback their managed files if a deployment step fails.

Routine leaf renewal uses `provision_private_origin_tls.sh --force`, which
reuses the private root. Validate the staged identity, reload nginx, then run
the full validator and public health probe. The recurring production check
alerts before the leaf or root reaches `TLS_MIN_VALID_DAYS`; do not wait for
that floor to schedule renewal.

## 7. Production verification and deployment authority

Pushes to `main` do not automatically mutate production. The GitHub **Deploy to
Production** workflow is operator-triggered and has `verify` and `deploy`
operations. Pull-request CI and public health checks stay on GitHub-hosted
runners; only the deployment job targets the dedicated Skynet runner labels.

The workflow does not check out or execute arbitrary workflow-branch code on
Skynet. Its GitHub-hosted gate requires the selected ref to be `main` and, for a
deployment, requires a successful `CI` push run for that exact commit. The
production environment accepts only `main`. The runner can sudo only the
root-owned `/usr/local/sbin/lawhand-deploy-from-github` entrypoint, which locks
deployments, fetches the canonical repository, rejects anything except the
current `origin/main` commit, refuses a dirty production checkout, and then
invokes `scripts/deploy_skynet_runner.sh` as the production user.

The `production` Git tag records the deployed migration baseline. Tenant data
safety checks always diff and rehearse from that tag, so a later green commit
cannot conceal an unsafe migration from an earlier failed `main` run. The
workflow advances the tag only after a successful production deployment.

The runner deployment path preserves the production skill's data guard: it
creates validated LegalApp and LiteLLM dumps/count manifests before rebuilding,
requires non-decreasing counts afterward, and verifies readiness plus exact
release metadata at `https://getlawhand.com`. Do not launch a runner deployment
and a manual/skill deployment concurrently. See
`docs/GITHUB_DEPLOY_RUNNER.md` for installation, phone operation, and recovery.

The scheduled **Production public health** workflow checks readiness, frontend,
and the 14-day TLS floor every ten minutes, reconciles one GitHub issue, and can
send an optional transition webhook. The on-host `production_check.sh` adds
disk, container, database, authenticated Redis, tenant scheduler, durable queue,
Zoom CRC, public HTTP, and TLS checks.

## Related changes in this sprint

- **Auth/session (C):** short-lived access tokens + rotating refresh tokens,
  Redis-backed family revocation, expiring consumed-token replay tombstones,
  atomic rotate/revoke conflict handling, and hardened cookie flags.
- **Tenant isolation (A):** `FORCE ROW LEVEL SECURITY` everywhere + a non-owner
  runtime DB role (`backend/scripts/provision_app_role.sql`).
- **Scheduler (B):** single-runner via `RUN_SCHEDULER` + Postgres advisory locks.
