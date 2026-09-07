# LawHand GitHub deployment runners

## Operating model

- CI, browser tests, migration safety, and scheduled public health checks run on
  GitHub-hosted runners. Untrusted pull-request code never runs on Skynet.
- IONOS is production. Its dedicated repository runner is labeled `ionos` and
  `lawhand-prod`; `.github/workflows/deploy-ionos-candidate.yml` and the manual
  `.github/workflows/production-acceptance.yml` use those labels. The Skynet
  runner is retained only for verification and disaster-recovery operations.
- Production deploys are manual. A deployment must be dispatched from `main`,
  and the exact commit must have a successful `CI` push run.
- The moving `production` Git tag identifies the accepted migration baseline.
  Migration safety always compares the candidate to that tag, and only a
  successful IONOS production acceptance advances it.
- The workflow performs no checkout on the runner. The IONOS runner account can
  sudo only `/usr/local/sbin/lawhand-ionos-deploy-from-github`.
- The root-owned entrypoint accepts only the current `origin/main` SHA, serializes
  deploys with `flock`, refuses tracked host changes, and runs the data-guarded
  deployment as `lawhandadmin`.
- The isolated Skynet `dev1` stack is the QA promotion gate. It has separate
  development-only volumes and its public writers remain disabled. It is not a
  production failover or a source of production DNS changes.

## Run from a phone

1. Open `mattpainter701/legalapp` in GitHub.
2. Open **Actions**.
3. If the QA gate is enabled, choose **QA acceptance** first, keep the branch
   set to `main`, and enter that exact full SHA. It deploys the isolated dev1
   stack and proves Cloudflare Access, readiness, exact version, and TLS.
   If the environment-scoped demo code is configured, it also runs the
   authenticated synthetic-demo API smoke.
4. Choose **Deploy IONOS candidate**, then **Run workflow**; keep the branch
   set to `main` and select `stage`.
   Enter `STAGE-IONOS-CANDIDATE` as the confirmation. The stage requires green
   CI and CodeQL for the exact SHA and, when enabled, a successful QA acceptance
   for that same SHA.
5. Follow the job log and record the staged SHA and backup evidence.
6. Choose **Production acceptance**, enter the full staged SHA, and run it from
   `main`. A successful run validates production and advances the release tag.

Until the blue/green IONOS edge work lands, `stage` is a real public production
restart: it rebuilds and force-recreates the public Compose stack. Schedule it
as maintenance work; it is not a private candidate or an instant deployment.

After staging, run **Production acceptance** from `main` with the full SHA
recorded by the stage run. The workflow requires that SHA to still be `main`
and a forward update from the existing `production` tag, then invokes the IONOS
root-owned entrypoint's `accept` operation. That operation checks the production
checkout, runs the strict `scripts/production_check.sh` gate as `lawhandadmin`,
and records only sanitized readiness, host-disk, backup, public health, and
exact-version evidence. After the gate succeeds, a separate GitHub-hosted job
rechecks that neither `main` nor the previous release marker moved and advances
the `production` tag. Provider configuration and secret values are never
printed or copied by the workflow.

## IONOS environment preflight

Before dispatching a mutating IONOS stage, validate the protected host
environment with the exact Cube M Compose pair. Do not use a generic Compose
profile: it selects the standard capacity floor instead of the reviewed Cube M
profile.

Keep this runbook and `.agents/skills/lawhand-deploy/SKILL.md` aligned whenever
the IONOS deployment contract changes; the skill is the operator entrypoint and
this document is its canonical repository reference.

```bash
cd /srv/lawhand/app
ENV_FILE=/etc/lawhand/core.env \
COMPOSE_FILES="/srv/lawhand/app/docker-compose.hypervisor.yml /srv/lawhand/app/docker-compose.cube-m.yml" \
  bash scripts/prod_env_preflight.sh
```

`TEMPLATE_STUDIO_RENDER_ENABLED=false` must be present explicitly. If
`MCP_SERVER_URL` is configured, `MCP_CITATOR_SCOPE_ASSERTION_SECRET` must be a
dedicated 32+-character secret, distinct from the other MCP secrets and all
token-encryption keys. Keep both values in `/etc/lawhand/core.env`; do not
print, commit, or copy the populated file into a local backup. Repairing that
host-managed file requires explicit operator authorization, a permissions-
preserving on-host backup, and a fresh preflight.

## Machine-readable release checks

`scripts/release_evidence.py --sha <sha> --workflow ci.yml --workflow codeql.yml`
reads GitHub's workflow-runs REST API and emits versioned JSON plus an Actions
summary. Each check includes SHA, workflow, event, run ID, attempt, URL, status,
conclusion, and a stable reason. Only a completed successful **newest** matching
run qualifies; an older green run never masks a newer failed, cancelled, or
pending run. The selected run is fetched again to observe its latest attempt.
QA uses the same policy with `--workflow qa-acceptance.yml --event workflow_dispatch`.
API errors, missing runs, and indeterminate evidence block promotion.

The helpers are checked out only on GitHub-hosted jobs. The self-hosted deploy
jobs still invoke only the fixed root-owned entrypoint with a pinned main SHA.

`scripts/check_readiness.py --origin https://getlawhand.com --sha <sha>` verifies
readiness and commit identity from a single response, requires all core
components to be healthy, and rejects observations older than 60 seconds or
more than 30 seconds in the future. Requests have a 15-second timeout, bounded
response size, and no redirect following. Cloudflare Access credentials are
read from environment variables; neither credentials nor raw bodies are logged.
This requires the readiness schema shipped with this change; older releases
without freshness metadata cannot satisfy the new acceptance check.

QA records the actual synthetic smoke outcome (`success`, `failure`, `skipped`,
or `cancelled`) in its summary and the `qa-acceptance-evidence-<attempt>` JSON
artifact. A skipped smoke is explicitly incomplete coverage, even when the
optional-smoke policy allows the overall QA run to pass. No promotion switch or
demo credential is enabled by this change.

CI's PR policy check reads the current description through the pull-request
REST API and validates it against the run's head and base. After correcting an
attestation, rerun the failed policy job; no empty commit is required. If head
or base moved, run fresh CI. API failure blocks the check instead of falling
back to the stale event body. The token has only contents/read and PR/read
permissions for this job, including untrusted PRs.

API references: [workflow runs](https://docs.github.com/en/rest/actions/workflow-runs)
and [pull requests](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request).

## Accepted-tag recovery procedure

The acceptance workflow uses the successful Git-ref update response as its
release-marker evidence. If an older run reports a tag-recording failure after
the IONOS acceptance job has passed, first read
`repos/mattpainter701/legalapp/git/ref/tags/production`. If it already resolves
to the accepted SHA and `main` is unchanged, rerun **Production acceptance**
for that exact SHA to obtain a clean, idempotent record. Do not move the tag by
hand or accept a different SHA.

Before invoking `accept`, the workflow performs a non-secret host preflight. It
requires the fixed entrypoint to be an executable `root:root` file with mode
`0755` and to advertise the `verify|stage|deploy|accept` operation set. If this
preflight fails, do not retry repeatedly or broaden the runner's sudo policy:
an operator must install the versioned repository file as root, then rerun the
workflow:

```bash
sudo install -o root -g root -m 0755 \
  /srv/lawhand/app/scripts/lawhand-ionos-deploy-from-github \
  /usr/local/sbin/lawhand-ionos-deploy-from-github
```

When installing or refreshing the runner boundary, install the versioned
`scripts/lawhand-ionos-deploy-from-github` file at the path above, root-owned
with mode `0755`. The existing sudoers entry remains intentionally path-scoped;
`accept` is an operation of that same entrypoint, not a second privileged
command. The acceptance workflow cannot run until this host copy includes the
`accept` operation.

Codex or a terminal can dispatch the same workflow:

```bash
gh workflow run deploy-ionos-candidate.yml --repo mattpainter701/legalapp \
  --ref main -f operation=verify
gh workflow run qa-acceptance.yml --repo mattpainter701/legalapp \
  --ref main -f release_sha=<full-current-main-sha>
gh workflow run deploy-ionos-candidate.yml --repo mattpainter701/legalapp \
  --ref main -f operation=stage -f confirmation=STAGE-IONOS-CANDIDATE
gh workflow run production-acceptance.yml --repo mattpainter701/legalapp \
  --ref main -f release_sha=<full-staged-main-sha>
```

## Host layout

```text
Runner user:       lawhand-runner
Runner directory:  /home/lawhand-runner/actions-runner
Runner labels:     self-hosted, Linux, X64, ionos, lawhand-prod
Production user:   lawhandadmin
Production repo:   /srv/lawhand/app
Entrypoint:        /usr/local/sbin/lawhand-ionos-deploy-from-github
Sudo policy:       /etc/sudoers.d/lawhand-ionos-github-runner
Deploy logs:       /var/log/lawhand-ionos-deploy/
Public origin:     https://getlawhand.com
```

The runner service name is assigned during GitHub runner registration. Find it
without assuming a host-specific name, then inspect the fixed entrypoint:

```bash
systemctl list-units 'actions.runner.mattpainter701-legalapp.*.service'
sudo -u lawhand-runner sudo -n \
  /usr/local/sbin/lawhand-ionos-deploy-from-github verify <origin-main-sha>
```

The registration token is one-time and is not stored in the repository or the
workflow. Re-register the runner from GitHub **Settings > Actions > Runners** if
its credentials are revoked. Do not give the runner account Docker membership,
read access to `/srv/lawhand/app` or `/etc/lawhand/core.env`, or general sudo
rights.

## Recovery

If the IONOS runner is offline, inspect its systemd service and outbound HTTPS
access. Do not fall back to the retired Skynet deployment workflow or point
public DNS at Skynet as a release shortcut. Any break-glass IONOS invocation
must use the same fixed root-owned entrypoint and be coordinated so it cannot
overlap a runner operation.
