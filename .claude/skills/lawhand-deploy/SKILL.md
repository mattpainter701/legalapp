---
name: lawhand-deploy
description: Deploy LawHand to dev1 (Skynet) and to IONOS production, and manage the dev1 to production promotion gate. Use for any request to deploy, release, promote, stage, roll out, or verify a deployment, to check what commit an environment is running, or to enable and troubleshoot the QA acceptance gate and dev1 health monitoring.
---

# LawHand Deployment and Promotion

The canonical procedure lives in `.agents/skills/lawhand-deploy/SKILL.md`, shared
with Codex and other agents so both toolchains follow one runbook.

**Read `.agents/skills/lawhand-deploy/SKILL.md` now and follow it.** Do not
duplicate its content here — update the canonical file instead, so the two
toolchains cannot drift.

## Required release sequence

When the user asks to deploy, promote, release, or roll out LawHand, execute
this sequence rather than searching for an older deployment path:

1. Fetch `origin/main`, resolve its full 40-character SHA, and require a
   successful exact-SHA `ci.yml` run. Require successful exact-SHA `codeql.yml`
   before IONOS.
2. For a production candidate, dispatch `qa-acceptance.yml` from `main` with
   `release_sha=<SHA>`. It deploys isolated dev1 and validates authenticated
   readiness, exact version, and TLS. Do not treat a queued or merely green
   workflow summary as deployed evidence; read the completed run's evidence.
3. Reconfirm `origin/main` is still that SHA. Dispatch
   `deploy-ionos-candidate.yml` with `operation=verify`; only after it passes,
   dispatch `operation=stage` with confirmation `STAGE-IONOS-CANDIDATE`.
4. Treat IONOS `stage` as a real public-stack restart, not a preview. Wait for
   `IONOS_STAGE_COMMIT=<SHA>` and successful staging evidence.
5. Reconfirm `main` has not moved, then dispatch `production-acceptance.yml`
   from `main` with `release_sha=<SHA>`. Completion is only valid after its
   IONOS acceptance succeeds and the remote `production` Git ref resolves to
   the accepted SHA.

Never use legacy `deploy.yml`, deploy from a local worktree or non-`main` ref,
copy populated environment files or secrets into chat/logs, bypass preflight
or data guards, or launch overlapping runner operations. The required Cube M
preflight and all secret-handling rules remain in the canonical runbook.
