# IONOS Cube M production cutover

## Decision and release state

The IONOS Memory Cube M is the reviewed first-customer core host. It is a
4-vCPU, 16-GB, 240-GB instance running the existing Docker Compose production
stack with `docker-compose.cube-m.yml` layered over
`docker-compose.hypervisor.yml`.

This document is a cutover procedure, not evidence that cutover has happened.
Until every gate below passes, Skynet remains the public production origin and
the `production` Git tag continues to describe that public deployment.

The placement is intentionally split:

| Component | First-customer placement | Boundary |
| --- | --- | --- |
| Portal, API, tenant database, Redis, LiteLLM, scheduler, public MCP gateways | IONOS Cube M | Customer identity, entitlement, billing, and audit source of truth |
| CourtListener/vector database, source corpus, authority sync, embedding workers, private research sidecar | Skynet | No public listener; IONOS-only tailnet path plus `MCP_UPSTREAM_API_KEY` |
| `getlawhand.com`, `mcp.getlawhand.com`, `research.getlawhand.com` | One new IONOS Cloudflare Tunnel | Nginx hostname/path isolation; Research remains 404 while disabled |

Current corpus placement is not optional for this cut: the vector database is
about 129 GB and the staged source corpus about 58 GB. Moving those volumes to
the 240-GB core disk would erase the build, backup, and database headroom that
the Cube profile requires. A later research host can replace Skynet without
changing customer keys because the IONOS gateway remains the public identity
and billing boundary.

## Hard stop conditions

Do not move DNS when any item below is true:

- the candidate is not the current `origin/main` SHA with green CI and CodeQL;
- `/etc/lawhand/core.env` is absent, symlinked, broadly readable, or contains
  development/shared credentials;
- the IONOS runner is the interactive administrator, belongs to `docker`, or
  has general passwordless sudo;
- no fresh encrypted off-host backup and isolated restore evidence exists;
- the IONOS stage check is not green on the exact candidate SHA;
- Tailscale cannot reach the private Skynet manifest with the dedicated
  upstream credential;
- either host exposes an unauthenticated Docker TCP API or the raw research
  sidecar on a public/LAN interface;
- the new Tunnel lacks pinned private-origin TLS or its final
  `http_status:404` rule;
- Research MCP is anything other than disabled/404;
- rollback CNAME targets, Skynet service state, and an operator are not ready.

## One-time host preparation

1. Keep SSH key authentication. In the IONOS network firewall, allow inbound
   SSH only from an explicit administration source. Do not expose 80, 443,
   PostgreSQL, Redis, LiteLLM, or the backend. Cloudflare Tunnel and the GitHub
   runner use outbound HTTPS.
2. Create a dedicated `lawhand-runner` system user. It must not belong to
   `docker`, `sudo`, or the deployment user's group and must not be able to read
   `/srv/lawhand/app`, `/etc/lawhand/core.env`, or production TLS keys.
3. Register the repository runner under that account with labels
   `ionos,lawhand-prod`. Registration tokens are one-time values; do not record
   them in shell history or files.
4. Clone `mattpainter701/legalapp` to `/srv/lawhand/app` as `lawhandadmin`.
   Keep the checkout free of tracked host edits.
5. Install the fixed root boundary and its single-command sudo policy:

   ```bash
   sudo install -o root -g root -m 0755 \
     /srv/lawhand/app/scripts/lawhand-ionos-deploy-from-github \
     /usr/local/sbin/lawhand-ionos-deploy-from-github
   sudo install -o root -g root -m 0440 \
     /srv/lawhand/app/scripts/lawhand-ionos-github-runner.sudoers \
     /etc/sudoers.d/lawhand-ionos-github-runner
   sudo visudo -cf /etc/sudoers.d/lawhand-ionos-github-runner
   ```

6. Remove all general passwordless-sudo rules from both the runner and
   interactive administrator after the fixed entrypoint is proven. The
   interactive account may retain ordinary password-gated sudo for break-glass
   administration after its previously exposed password is rotated.
7. Install Tailscale on IONOS and authorize it into the existing tailnet. On
   Skynet, publish only `127.0.0.1:8021` to the tailnet and restrict the ACL to
   the IONOS node. Set the IONOS `MCP_SERVER_URL` to the private Tailscale IP or
   MagicDNS name; never publish the raw sidecar through Cloudflare. Remove any
   unauthenticated Docker daemon TCP listener (`2375`) from both hosts; remote
   Docker administration must use SSH or a mutually authenticated endpoint.
8. Create `/etc/lawhand/core.env` as `root:lawhandadmin` mode `0640`. Start from
   `.env.prod.example`, carry production values through an encrypted host-to-
   host transfer, and change host paths to `/srv/lawhand/uploads` and
   `/srv/lawhand/host-status`. Set
   `APP_ENV_FILE=/etc/lawhand/core.env` so container `env_file` mounts resolve
   outside the checkout. Keep `MCP_PRODUCT_ENABLED=false`. Point Restic at an
   actual second host or object store. The first-customer topology uses a
   key-only, chrooted SFTP account on Skynet over Tailscale; its private key is
   dedicated to IONOS backups and the authorized key is source-restricted to
   the tailnet. A repository such as `/srv/legalapp-backups` on Skynet's own
   root disk is not off-host evidence for a Skynet deployment and must not be
   counted as the pre-cut recovery copy.
9. Provision the private origin CA/leaf with
   `scripts/provision_private_origin_tls.sh`. Create a new, separately
   credentialed IONOS Tunnel whose ingress has the four canonical hostnames,
   pinned HTTPS origin settings, and final `http_status:404`. Do not change DNS
   yet.
10. Create the GitHub `ionos-production` environment restricted to `main`.
    Do not store the production `.env`, Tunnel credential JSON, Tailscale auth
    key, or provider secrets in repository variables.

## Candidate deployment

After the infrastructure pull request is merged and its exact `main` push CI
is green:

1. Run **Deploy IONOS candidate** with `operation=verify`.
2. Run it with `operation=stage` and confirmation
   `STAGE-IONOS-CANDIDATE`. This runs the full preflight, backup, data guard,
   sequential image build, migrations, scheduler proof, private-origin checks,
   hostname isolation, and private research manifest probe. It deliberately
   does not claim public readiness. The one-shot LiteLLM Prisma migrator has a
   1,280-MiB cgroup allowance because an initial production-data reconciliation
   can exceed 768 MiB; it exits before the steady-state LiteLLM proxy starts.
3. Record `IONOS_STAGE_COMMIT`, the off-host backup evidence path, container
   health, private origin TLS result, and `IONOS_PUBLIC_CUTOVER=not-yet-approved`.
4. Run a bounded load test at two backend workers while watching container RSS,
   host available memory, CPU saturation, PostgreSQL connections, and p95/p99
   latency. The acceptance floor is no OOM/restart, at least 2 GB host memory
   available, healthy database/Redis/scheduler, and recoverable latency. If it
   fails, resize to Cube L before cutover; do not weaken the limits.

## Final data handoff and DNS cut

Use a scheduled maintenance window even before the first paying tenant.

1. Confirm the exact candidate is deployed on Skynet and staged on IONOS.
   Capture the current Tunnel CNAME target and the new IONOS target for rollback.
2. Put the public application into maintenance by stopping the Skynet nginx,
   backend, scheduler, LiteLLM, frontend, and Office add-in services. Keep the
   two PostgreSQL services running for a consistent final export. Do not allow
   both schedulers to run against cloned production state.
3. Run the existing encrypted off-host backup and isolated restore rehearsal.
   Export the final LegalApp and LiteLLM databases from one stopped-writer
   window, archive uploads with its manifest, transfer over authenticated SSH,
   and verify checksums on IONOS before import. For the first cut, the encrypted
   Skynet recovery bundle copied to IONOS is the off-host source evidence; after
   IONOS becomes primary, its scheduled Restic repository is the restricted
   Skynet SFTP target.
4. Stop all IONOS application writers, take a target-side safety snapshot, then
   restore the two final dumps and uploads. Start the IONOS Cube profile and
   rerun `ionos_stage_check.sh`. Abort on a count decrease or checksum mismatch.
5. Change only the apex, `www`, `mcp`, and `research` proxied CNAME targets from
   the old Tunnel to the new IONOS Tunnel. Preserve the final ingress catch-all.
6. Run **Deploy IONOS candidate** with `operation=accept` and confirmation
   `ACCEPT-IONOS-PRODUCTION`. Then run the scheduled public-health workflow and
   verify exact `/api/version`, readiness, OAuth challenge/metadata, hostname
   isolation, Research 404 behavior, HSTS, and public TLS.
7. Advance the `production` tag only after public acceptance identifies the
   exact IONOS SHA. Update the normal production deployment and acceptance
   workflows to the IONOS runner in the immediately following protected change.
8. Keep Skynet's core containers stopped but its database volumes and previous
   Tunnel credentials intact for the rollback window. Keep the research
   database, sidecar, sync, backup, and embedding services running.

## Rollback

If public acceptance fails, restore the four CNAMEs to the recorded Skynet
Tunnel target, restart the stopped Skynet core services, and run the Skynet
production acceptance gate. Stop the IONOS scheduler before restoring Skynet so
only one scheduler can own durable work. Do not copy partially written IONOS
state back automatically; reconcile any writes that reached IONOS during the
window from audit/provider evidence.

Do not enable Research MCP, broaden the Tailscale ACL, expose port 8021, or
move the corpus as a cutover recovery action.
