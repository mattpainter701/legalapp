# Production Tasks — Outstanding Action Items

Created 2026-08-16 after a full-repo code review and cleanup pass. These items
remain open because they require production console access, external provider
dashboards, or operator decisions. Nothing here is optional unless marked so.

---

## P0 — Rotate production secrets (MANDATORY — exposure on record)

During an earlier incident inspection, the backend environment was accidentally
printed to a tool log. Every secret in the production `.env`
(`/home/varta/legalapp/.env` on the hypervisor, `172.16.16.202`) must be
treated as exposed. The **local** `.env` was rotated on 2026-08-16; production
is the remaining exposure.

Rotation order (cheapest blast radius first):

- [ ] **SECRET_KEY** — generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
      Set in prod `.env`, restart backend. **Effect: all users re-login.** Schedule quietly.
- [ ] **TOKEN_ENCRYPTION_KEY** — use the keyring, never a bare swap:
      `TOKEN_ENCRYPTION_KEYS=<new>,<old>` (new first = encrypts new tokens, old stays
      listed = existing stored Google/Microsoft OAuth tokens still decrypt).
      A bare swap disconnects every tenant integration at once.
- [ ] **Google** — console.cloud.google.com → APIs & Services → Credentials → OAuth
      client `935084470846-vql4uv5e5pmig5fqlaf1niaqk4i31l97` → reset secret → update prod + local `.env`.
- [ ] **Microsoft** — Entra portal → App registrations → `c805316c-ac2c-4f68-8ace-a5522889bf50`
      → Certificates & secrets → new client secret → update prod + local `.env` →
      delete old secret after cutover is verified.
- [ ] **Zoom** — marketplace.zoomworks.com → app `5ECXgmpOQ3KRfZAV4gGb7w` → regenerate
      client secret and webhook token → update prod + local `.env`.
- [ ] **DEEPSEEK_API_KEY** — OpenCode dashboard → revoke `sk-o95x3zen...` → issue new →
      update both `.env` files (LiteLLM gateway also reads this value).
- [ ] **Database passwords** — `legalapp` and `litellm` Postgres roles:
      `ALTER ROLE ... PASSWORD ...`, update `DATABASE_URL` / `LITELLM_DATABASE_URL`,
      rolling restart.
- [ ] **Infra account passwords** — the `varta` password for the hypervisor and Jetson
      (`varta123!`) is in the env file. `passwd` on both boxes; treat anything else
      using that password as exposed.
- [ ] **PLATFORM_SECRET_KEY** (prod, if set) — rotate with the rest.

After each: `curl https://getlawhand.com/health`, one OAuth login, one integration check.

Follow-up after rotation:
- [ ] Drop the old key from `TOKEN_ENCRYPTION_KEYS` once all tenants have re-connected
      (their tokens are then all encrypted with the new key).
- [ ] Locate and scrub/rotate the tool log that captured the environment dump.

## P0 — Pre-deploy check (30 seconds, do before every deploy until habitual)

- [ ] `ssh varta@172.16.16.202 "grep DEV_MODE /home/varta/legalapp/.env"` must say
      `DEV_MODE=false`. The boot-time validator added 2026-08-16
      (`validate_dev_mode_urls` in `backend/app/config.py`) refuses to start with
      `DEV_MODE=true` on a non-localhost URL — if this check fails, the deploy fails
      closed **by design**. Fix the env, not the validator.

## P1 — MCP upstream key gap (latent config fault)

- [ ] Local `.env` sets `MCP_SERVER_URL=http://courtlistener-mcp:8021` but has no
      `MCP_UPSTREAM_API_KEY`. Compose files pass the var through empty, which fails
      backend startup validation where mapped, and silently degrades public-authority
      retrieval to the legacy `public_chunks` path where unmapped. Generate a 32+
      char key, set it in `.env` **and** in the `courtlistener-mcp` service
      environment, and verify `/api/mcp` tool calls return results.

## P1 — Branches and worktrees

- [ ] Push `agent/source-provenance-tracking` (commit `2dd6ec3` — source provenance in
      chat citations) and open its PR, or delete the branch if superseded.
- [ ] Two worktrees appeared mid-review owned by another session:
      `opencode-go-prod-fix` (on `main`) and `standard-premium-product-plan`.
      Confirm what they are for; do not delete while the other session is active.

## P2 — Housekeeping

- [ ] `F:\deepseek\legalapp\tmp\` — 63 files of CI/preview artifacts (`ci-e2e-*`,
      `demo_seed`, `lawhand-index`, preview logs). Safe to delete once confirmed
      nothing references those paths.
- [ ] `F:\deepseek\legalapp\offhost-backups\` — contains real Postgres dumps
      (pre-deploy + litellm repair). Decide a retention policy; do not bulk-delete.
- [ ] Wire `window.__LAWHAND_ERROR_REPORTER__` to Sentry (or chosen vendor) at the
      frontend edge. All 29 former `console.error` call sites already funnel through
      `frontend/src/utils/reportError.js` — this is the only integration point needed.

---

## Completed in the 2026-08-16 review (context, no action needed)

- Removed 21 stale git worktrees (~380k files); branches preserved.
- Verified `.env` never committed to git history (GitHub remote included); `.gitignore` covers it.
- Verified tracked `.env.hypervisor` contains only placeholders.
- Verified live `DEV_MODE=false` (`/docs` 404 on getlawhand.com); added boot-time fail-closed validator.
- Removed dead `RAGService` stub; fixed `react/jsx-uses-vars` (890 phantom lint warnings → real signal);
  removed 110 dead `React` imports + 18 unused imports; removed `useCallDrafts` hotfix residue.
  ESLint now reports **0 problems**; frontend build + 117 vitest tests pass.
- Local `.env` secrets rotated (`SECRET_KEY`, `PLATFORM_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY` via keyring).
