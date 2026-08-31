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
