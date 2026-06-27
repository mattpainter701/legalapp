# Production Hardening (Workstream D)

Operator reference for the container, TLS, and reverse-proxy hardening applied in
the production-readiness sprint. Pair this with `docs/SPRINT_PROD_READINESS.md`.

## 1. Non-root containers

`backend/Dockerfile` now runs as an unprivileged user.

- Fixed **UID/GID `10001`** (`appuser`). A fixed id lets you chown host-mounted
  volumes to a known owner.
- The production uploads bind-mount (`/data/legalapp/uploads` → `/app/uploads`)
  must be writable by that UID. On the host, once:
  ```bash
  sudo chown -R 10001:10001 /data/legalapp/uploads
  ```
- Python deps install to `/usr/local` (world-readable), so the non-root user can
  still import them.
- A stdlib `HEALTHCHECK` hits `/health` (the slim image has no `curl`).

**Base-image pinning:** the Dockerfile keeps `python:3.11-slim` with a clearly
marked TODO to pin by digest in prod. Get the digest with
`docker buildx imagetools inspect python:3.11-slim` and replace the `FROM` line.

**Image scanning:** add a container scan (e.g. Trivy) to CI and fail on
HIGH/CRITICAL CVEs:
```bash
trivy image --severity HIGH,CRITICAL --exit-code 1 <image>
```

## 2. TLS & security headers (nginx)

The production `:443` server block in `nginx/nginx.conf`:

- `ssl_protocols TLSv1.2 TLSv1.3;` only.
- `Strict-Transport-Security: max-age=63072000; includeSubDomains` (TLS block only).
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
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

When using **cloudflared tunnel** (as in the current PoC), traffic does not arrive
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

- Run `backend/scripts/provision_app_role.sql` once as a superuser/DB owner.
- Set `APP_DATABASE_URL` in production env to the `clarity_app` DSN:
  - Example: `postgresql+asyncpg://clarity_app:...@localhost:5432/legalapp?ssl=require`
- Deploy services. `backend` and `scheduler` in compose now read:
  - `DATABASE_URL=${APP_DATABASE_URL:-${DATABASE_URL}}`
- Keep migrations on owner role:
  - `migrator` resolves `DATABASE_URL` from `MIGRATOR_DATABASE_URL` when set, else falls back to `DATABASE_URL`.
- Confirm startup logs show `DB role check passed` and direct DB checks show:
  - `rolsuper = false` and `rolbypassrls = false` for `clarity_app`.

## 6. Secrets

- Move secrets out of plaintext `.env` into Docker secrets / a secret manager.
- Rotate `SECRET_KEY`, `PLATFORM_SECRET_KEY`, DB and Redis passwords; document the
  rotation cadence.
- `REDIS_PASSWORD` is enforced in `docker-compose.prod.yml`
  (`redis-server --requirepass`).

## Related changes in this sprint

- **Auth/session (C):** short-lived access tokens + rotating refresh tokens,
  Redis-backed revocation, hardened cookie flags.
- **Tenant isolation (A):** `FORCE ROW LEVEL SECURITY` everywhere + a non-owner
  runtime DB role (`backend/scripts/provision_app_role.sql`).
- **Scheduler (B):** single-runner via `RUN_SCHEDULER` + Postgres advisory locks.
