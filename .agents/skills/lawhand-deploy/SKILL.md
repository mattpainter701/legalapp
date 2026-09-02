---
name: lawhand-deploy
description: Deploy LawHand to dev1 (Skynet) and to IONOS production, and manage the dev1 to production promotion gate. Use for any request to deploy, release, promote, stage, roll out, or verify a deployment, to check what commit an environment is running, or to enable and troubleshoot the QA acceptance gate and dev1 health monitoring.
---

# LawHand Deployment and Promotion

Canonical references: `docs/GITHUB_DEPLOY_RUNNER.md`, `docs/IONOS_CUTOVER_RUNBOOK.md`,
`docs/LIVE_DEMO_RUNBOOK.md`. Read those for host-side detail; this skill is the
workflow-level procedure and the safety rules.

## Environments

- **dev1** — `https://dev1.getlawhand.com`, self-hosted Skynet runner, isolated
  dev-only volumes. Scheduler, email, signup, and MCP products are disabled.
- **IONOS production** — the real production target. `deploy.yml` is a legacy
  verify-only stub for the *retired* Skynet prod runner; it cannot deploy. Never
  treat it as the production deploy path.

## The pipeline

1. `ci.yml` must be `success` for the exact commit.
2. `deploy-dev1.yml` (`operation=deploy`, confirmation `DEPLOY-SKYNET-DEV1`) —
   plain dev1 deploy, or
   `qa-acceptance.yml` (`release_sha=<40-hex>`) — deploys the same commit to dev1
   **and** validates it. Prefer this when the commit is a production candidate.
3. `deploy-ionos-candidate.yml` (`operation=stage`, confirmation
   `STAGE-IONOS-CANDIDATE`) — deploys to IONOS production.
4. `production-acceptance.yml` (`release_sha=<40-hex>`) — validates production
   after the deploy.

Every workflow refuses to run from anything but `main` and pins the exact SHA.

## Safe workflow

1. `git fetch origin main` and resolve `origin/main`. `workflow_dispatch` always
   runs against the branch HEAD at dispatch time, so a stale local clone does not
   affect what ships — but you must know the real SHA to verify against.
2. Confirm CI is `success` **for that exact SHA** before dispatching:
   `gh run list --workflow ci.yml --commit <sha> --event push --limit 1 --json conclusion`
   A queued or failing CI means the release gate will reject the deploy anyway.
3. Re-check `origin/main` has not moved between your CI check and the dispatch.
   Another agent or person may have merged underneath you.
4. Dispatch, then **read back what actually landed**. A green check is not
   evidence. For dev1, the runner log ends with `DEV1_DEPLOYED_COMMIT=<sha>`;
   confirm it equals the intended SHA. Report failures with their real output.
5. Never enable the promotion gate as part of a deploy. That is a separate,
   deliberate change (see below).

## Commands

```bash
REPO=mattpainter701/legalapp
SHA=$(git rev-parse origin/main)

# CI evidence for the exact commit
gh run list --repo $REPO --workflow ci.yml --commit "$SHA" --event push \
  --limit 1 --json conclusion,status,url

# dev1
gh workflow run deploy-dev1.yml --repo $REPO --ref main \
  -f operation=deploy -f confirmation=DEPLOY-SKYNET-DEV1

# dev1 + acceptance (production candidate)
gh workflow run qa-acceptance.yml --repo $REPO --ref main -f release_sha="$SHA"

# IONOS production
gh workflow run deploy-ionos-candidate.yml --repo $REPO --ref main \
  -f operation=stage -f confirmation=STAGE-IONOS-CANDIDATE

# validate production afterwards
gh workflow run production-acceptance.yml --repo $REPO --ref main -f release_sha="$SHA"
```

Use `operation=verify` on either deploy workflow to exercise the runner
entrypoint without changing the environment.

## Configuration switches

| Setting | Scope | Effect |
| --- | --- | --- |
| `LAWHAND_DEV1_ENABLED` | repo variable | Gates `dev1-health.yml`. When not `true`, every scheduled health run is skipped and dev1 is unmonitored. |
| `LAWHAND_QA_GATE_REQUIRED` | repo variable | When `true`, `deploy-ionos-candidate.yml` refuses to stage unless a successful `qa-acceptance` run exists for that exact SHA. |
| `LAWHAND_QA_HOSTNAME` | repo variable | dev1 hostname used by health and acceptance. |
| `LAWHAND_QA_ACCESS_CLIENT_ID` / `_SECRET` | `skynet-development` env secrets | Cloudflare Access service token. **Without these, health and acceptance fail at their credentials guard before contacting dev1** — a failure that says nothing about dev1's real health. |
| `LAWHAND_QA_DEMO_ACCESS_CODE` | secret | Optional. When unset, the synthetic API smoke step *silently skips*, degrading acceptance to readiness, version, and TLS only. |

### Enabling the promotion gate — order matters

Turning the gate on before a green acceptance exists **blocks every production
deploy**, because no SHA will have a passing `qa-acceptance`. Correct order:

1. Configure demo mode on the dev1 host and seed the fixture
   (`docs/LIVE_DEMO_RUNBOOK.md`). Demo mode is how acceptance exercises real
   authenticated APIs; without it the smoke step cannot run.
2. Add the Cloudflare Access service-token secrets, then the demo access code.
3. Set `LAWHAND_DEV1_ENABLED=true`.
4. Run `qa-acceptance` on current main and get it genuinely green.
5. **Only then** set `LAWHAND_QA_GATE_REQUIRED=true`.

Each switch defaults off, so dev1 work never changes IONOS behaviour until step 5.

## Gotchas

- **`continue-on-error` steps report `conclusion: success` even when they fail.**
  The real result is `outcome`. Read `outcome`, or you will report a broken probe
  as healthy.
- `qa-acceptance` requires `release_sha` to equal the current `main` HEAD exactly;
  it re-checks against the API and rejects anything else.
- `deploy-dev1.yml` and `qa-acceptance.yml` share concurrency group
  `law-hand-skynet-dev1`; `deploy-ionos-candidate.yml` and
  `production-acceptance.yml` share `law-hand-ionos-production`. They queue rather
  than race.
- dev1 responds `302` to unauthenticated requests — that is Cloudflare Access, not
  an origin error. Probing it meaningfully requires the service token.
- **A Cloudflare Access service token needs two things, and missing either looks
  identical.** The token must exist (client id `<32 hex>.access`, secret 64
  lowercase hex, shown once at creation) — *and* the Access application must
  carry a `non_identity` policy whose `include` names it. An application holding
  only an email policy rejects every service token. Diagnose from the 302
  `Location` header: decode its `meta` JWT, and `service_token_status: false`
  with `auth_status: NONE`, byte-identical with and without the headers, means
  the token was never recognised rather than rejected. Such a policy grants
  non-interactive access to the whole hostname, so treat adding one as an
  access-widening change, not a config detail.
- Never write an unverified service token into GitHub secrets. Absent credentials
  fail at the workflow's own guard and name their own cause; a bad token fails at
  the HTTPS call instead and reads as “dev1 is down”.
- dev1 uses isolated dev-only volumes. Fixtures and tenants from other
  environments do not exist there and must be seeded locally.
- Never copy production customer data into dev1 to make it look realistic. This is
  a legal product; that content is client matter data and likely privileged, and
  dev1 fronts a public unauthenticated demo endpoint. Enrich the demo fixture
  instead — `backend/scripts/seed_demo_fixture.py` and
  `backend/app/services/demo_clone.py` exist for exactly this.
