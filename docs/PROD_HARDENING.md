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

## 4. Secrets

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
