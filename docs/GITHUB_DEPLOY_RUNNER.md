# LawHand GitHub deployment runner

## Operating model

- CI, browser tests, migration safety, and scheduled public health checks run on
  GitHub-hosted runners. Untrusted pull-request code never runs on Skynet.
- The dedicated repository runner is labeled `skynet` and `lawhand-prod`.
  `.github/workflows/deploy.yml` and the manual
  `.github/workflows/production-acceptance.yml` use those labels.
- Production deploys are manual. A deployment must be dispatched from `main`,
  and the exact commit must have a successful `CI` push run.
- The moving `production` Git tag identifies the deployed migration baseline.
  Migration safety always compares the candidate to that tag, and only a
  successful deployment advances it.
- The workflow performs no checkout on Skynet. The runner account can sudo only
  `/usr/local/sbin/lawhand-deploy-from-github`.
- The root-owned entrypoint accepts only the current `origin/main` SHA, serializes
  deploys with `flock`, refuses tracked host changes, and runs the data-guarded
  deployment as `varta`.

## Run from a phone

1. Open `mattpainter701/legalapp` in GitHub.
2. Open **Actions** and choose **Deploy to Production**.
3. Choose **Run workflow**, keep the branch set to `main`, and select:
   - `verify` to check the runner, pinned main SHA, and public readiness without
     changing production.
   - `deploy` to require green CI and deploy the exact selected commit.
4. Follow the job log. The summary records the operation, SHA, runner, and site.

After a deploy, run **Production acceptance** from `main` with the full SHA
recorded by the deploy run. The workflow requires that SHA to still be `main`
and the `production` tag, then invokes the root-owned entrypoint's `accept`
operation. That operation checks the production checkout, runs the strict
`scripts/production_check.sh` gate as `varta`, and records only sanitized
readiness, host-disk, backup, public health, and exact-version evidence. It
does not change provider configuration or inspect/print secret values.

Before invoking `accept`, the workflow performs a non-secret host preflight. It
requires the fixed entrypoint to be an executable `root:root` file with mode
`0755` and to advertise the `verify|deploy|accept` operation set. If this
preflight fails, do not retry repeatedly or broaden the runner's sudo policy:
an operator must install the versioned repository file as root, then rerun the
workflow:

```bash
sudo install -o root -g root -m 0755 \
  /home/varta/legalapp/scripts/lawhand-deploy-from-github \
  /usr/local/sbin/lawhand-deploy-from-github
```

When installing or refreshing the runner boundary, install the versioned
`scripts/lawhand-deploy-from-github` file at
`/usr/local/sbin/lawhand-deploy-from-github` (root-owned, mode 0755). The
existing sudoers entry remains intentionally path-scoped; `accept` is an
operation of that same entrypoint, not a second privileged command. The
acceptance workflow cannot run until this host copy includes the `accept`
operation.

Codex or a terminal can dispatch the same workflow:

```bash
gh workflow run deploy.yml --repo mattpainter701/legalapp --ref main -f operation=verify
gh workflow run deploy.yml --repo mattpainter701/legalapp --ref main -f operation=deploy
```

## Host layout

```text
Runner user:       lawhand-runner
Runner directory:  /home/lawhand-runner/actions-runner
Runner service:    actions.runner.mattpainter701-legalapp.skynet-lawhand-prod.service
Production user:   varta
Production repo:   /home/varta/legalapp
Entrypoint:        /usr/local/sbin/lawhand-deploy-from-github
Sudo policy:       /etc/sudoers.d/lawhand-github-runner
Deploy logs:       /var/log/lawhand-deploy/
Public origin:     https://getlawhand.com
```

Useful checks:

```bash
systemctl status actions.runner.mattpainter701-legalapp.skynet-lawhand-prod.service
journalctl -u actions.runner.mattpainter701-legalapp.skynet-lawhand-prod.service -n 100
sudo -u lawhand-runner sudo -n /usr/local/sbin/lawhand-deploy-from-github verify <origin-main-sha>
```

The registration token is one-time and is not stored in the repository or the
workflow. Re-register the runner from GitHub **Settings > Actions > Runners** if
its credentials are revoked. Do not give the runner account Docker membership,
read access to `/home/varta`, or general sudo rights.

## Recovery

If the runner is offline, inspect its systemd service and outbound HTTPS access.
The manual `legalapp-deploy-prod` skill remains the break-glass route. Never run
the manual and runner paths together; the host entrypoint lock protects runner
runs, while an operator must coordinate any manual invocation.
