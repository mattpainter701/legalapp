# Skynet development and disaster recovery

## Service roles

| Site | Role | Writer state | Public hostname |
| --- | --- | --- | --- |
| IONOS Cube M | Production primary | Enabled | `getlawhand.com`, `mcp.getlawhand.com`, `research.getlawhand.com` |
| Skynet `law-hand-dev1` | Development | Customer scheduler disabled | `dev1.getlawhand.com` |
| Skynet DR rehearsal | Isolated restore validation | Fenced/stopped | None; status is tailnet-only |
| Skynet research stack | CourtListener/RAG upstream | Corpus sync only | Reached through the authenticated IONOS research gateway |

Dev1 and DR never share volumes with each other, the legacy `legalapp_*`
volumes, or the research database. Dev1 starts empty and may contain only test
or synthetic records. Its outbound email, public signup, tenant scheduler,
workspace MCP, and research MCP product surfaces are disabled.

## Initial Skynet setup

1. Copy `config/dev1.env.example` to
   `/home/varta/.config/lawhand/dev1.env`, replace every example secret with a
   unique dev-only value, and set mode `600`. Do not copy production OAuth,
   database, signing, or token-encryption credentials.
2. Install the dev entrypoint once:
   `sudo bash scripts/install_dev1_deploy_entrypoint.sh`.
3. Add the exact entrypoint and DR wrapper sudo rules created by the installers;
   do not grant the runner general passwordless sudo.
4. Create a separately credentialed Restic configuration at
   `/home/varta/.config/lawhand/dr.env` with mode `600`, then run
   `sudo bash scripts/install_skynet_dr_services.sh`.
5. Protect the GitHub environments `skynet-development` and
   `skynet-disaster-recovery`. Store backup credentials on the host, not in
   workflow inputs or repository secrets.
6. Dispatch **Deploy Skynet dev1** from `main`, first with `verify`, then with
   `deploy` and confirmation `DEPLOY-SKYNET-DEV1`.

7. After the public dev probe and first isolated restore both pass, set the
   repository variables `LAWHAND_DEV1_ENABLED=true` and
   `LAWHAND_SKYNET_DR_ENABLED=true`. Until then, their scheduled workflows are
   intentionally dormant and manual dispatch remains available for acceptance.
The deployment workflow pins current `origin/main`, requires CI for that exact
SHA, and calls a root-owned entrypoint. The entrypoint refuses a dirty checkout,
non-main commit, wrong origin, or non-isolated environment.

## Cloudflare ingress

Publish dev1 only after its private TLS probe passes. Add this rule immediately
before the Tunnel's terminal `http_status:404` rule:

```yaml
- hostname: dev1.getlawhand.com
  service: https://127.0.0.1:18443
  originRequest:
    originServerName: origin.getlawhand.internal
    caPool: /etc/cloudflared/lawhand-origin-ca.pem
```

Create a proxied CNAME for `dev1.getlawhand.com` to the Skynet Tunnel target.
Keep the IONOS production records unchanged. Gate dev1 with Cloudflare Access
before it contains any non-public feature or test data. Preserve the terminal
404 rule and verify that an unknown hostname does not reach nginx.

## DR backup and rehearsal

IONOS must write encrypted off-host Restic snapshots at least hourly. A Skynet
rehearsal runs daily and calls the existing network-isolated
`restore_rehearsal.sh`; it validates hashes, exact row counts, uploads, LiteLLM,
and key escrow without attaching to running volumes. The result is exposed only
on Skynet's Tailscale address at port `19090` and creates or resolves a GitHub
`[dr-alert]` issue.

The CourtListener/vector corpus follows its separate RAG backup and restore
rehearsal. It is not duplicated into the small platform DR snapshot.

## Platform status and alerts

The operator-only page is `/platform/infrastructure`. Configure IONOS with a
single JSON environment value; URLs are not accepted from API callers:

```text
PLATFORM_INFRASTRUCTURE_TARGETS_JSON=[{"id":"primary","label":"IONOS primary","role":"primary","url":"https://getlawhand.com/health/readiness"},{"id":"dev1","label":"Skynet dev1","role":"development","url":"https://dev1.getlawhand.com/health/readiness"},{"id":"dr","label":"Skynet DR rehearsal","role":"disaster-recovery","url":"http://100.108.171.10:19090/status","max_age_seconds":93600},{"id":"research","label":"Research MCP gateway","role":"research","url":"https://research.getlawhand.com/.well-known/oauth-authorization-server"}]
```

Only HTTPS or loopback/Tailscale HTTP targets are accepted. The page reports
sanitized reachability, release drift, DR writer fencing, and active warnings;
it never returns credentials or raw network errors. Scheduled GitHub health
workflows remain the durable alert source. Configure `ALERT_WEBHOOK_URL` only
for an approved secondary notification channel.

## Manual production failover

Failover is deliberately not automatic. A production outage does not authorize
two writable sites.

1. Declare the incident and record the latest verified snapshot and expected
   RPO. Stop/fence the IONOS scheduler and application writer if reachable.
2. Take and verify a final snapshot if the primary remains readable. Otherwise,
   explicitly accept the measured data-loss window.
3. Restore the verified snapshot into the dedicated `law-hand-dr` volumes on
   Skynet. Never restore into dev1, research, or legacy volumes.
4. Run migrations and private acceptance with the scheduler still disabled.
5. Confirm the IONOS writer is fenced, then enable exactly one Skynet scheduler.
6. Change the production Cloudflare records together to the staged Skynet
   Tunnel and run the complete public production-health workflow.
7. Record the release SHA, snapshot, row counts, DNS record IDs, and time of
   writer transition. Rollback uses the inverse sequence and fences Skynet
   before IONOS becomes writable.

This is a warm-backup design with a rehearsed restore, not active-active
replication. It keeps cost and split-brain risk low for the first customers and
leaves a clear path to managed PostgreSQL replication when RTO requirements
tighten.
