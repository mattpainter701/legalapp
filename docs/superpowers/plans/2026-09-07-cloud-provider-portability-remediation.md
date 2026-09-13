# Cloud Provider Portability & Tier Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make LawHand honest about which cloud account tier a firm is on, correct in how it names and binds matter folders, and capable of moving a firm from one provider to another without rebuilding their matters.

**Origin:** Compiled 2026-09-07 from a code audit of `origin/main` at `5cf087a`, prompted by a request to onboard a firm on a personal Google account with a custom domain. Every finding below is cited to file and line.

**Architecture:** Three independent workstreams that share one ordering constraint. Tier detection (A) is self-contained and was already designed in June. Folder-structure correction (B) must land before provider migration (C), because migration reconciles on folder identity and today that identity is inconsistent. The corpus-posture decision (D) is a product decision that gates public documentation, not an implementation task.

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, pytest/pytest-asyncio, React (Vite), Vitest.

**Related:** `docs/superpowers/specs/2026-06-14-provider-tier-detection-capabilities-design.md` (approved, unimplemented), `docs/workspace-ecosystem-trials-onboarding-plan-2026-08-27.md` (personal vs Workspace decision).

---

## Findings summary

| # | Finding | Severity | Workstream |
| --- | --- | --- | --- |
| 1 | Alias/send-as addresses are invisible; users and contacts match on one exact address | Blocks personal-tier onboarding | A |
| 2 | Directory sync on personal Google records `failed` rather than `not_applicable` | Misleading; support load | A |
| 3 | Approved tier-detection design was never implemented | Root cause of 2 | A |
| 4 | Upload fallback can create a second, slug-named folder tree per matter | Live data-integrity bug | B |
| 5 | Three folder-naming conventions coexist; only one carries the matter ID | Blocks reconciliation | B |
| 6 | `cloud_folder["path"]` is recomputed and overwritten on every provision | Blocks reconciliation | B |
| 7 | Matter folder provisioning is fire-and-forget, racing the first upload | Cause of 4 | B |
| 8 | No marker file inside matter folders; folder identity dies on rename | Reconciliation fragility | B |
| 9 | Changing `primary_cloud_provider` re-routes writes with no migration semantics | Silent split-brain risk | C |
| 10 | `cloud_metadata_index` orphans on provider cutover; nothing prunes by provider | Stale search results | C |
| 11 | Onboarding cannot be re-run; `/complete` overwrites `cloud_root_folder` | No migration entry point | C |
| 12 | Google Drive search omits `supportsAllDrives`; Shared Drive content invisible | Affects paying Workspace tenants | B |
| 13 | `cloud_metadata_index.snippet` is unbounded `Text` | Erodes the no-content invariant | D |
| 14 | Two corpora with opposite storage postures; no stated policy | Gates public claims | D |
| 15 | `emails` subfolder provisioned but unused; `.eml` files land in `correspondence` | Cosmetic | B |
| 16 | No audit-log or conversation export into matter cloud folders | Unbuilt requirement | Deferred |

---

## Requirements this plan serves

- A firm on any supported tier sees an accurate statement of what that tier can do, and never sees a tier limit reported as a failure.
- A matter has exactly one cloud folder per provider, discoverable by matter ID, and repointable by an administrator.
- A firm that migrates between providers keeps every matter, document, and history record, with pointers rebound rather than files copied.
- Search over cloud content works without LawHand becoming a second copy of the firm's data — or, where it does hold content, that is stated deliberately.

---

## Workstream A — Account tier honesty

Implements the approved June design unchanged, plus the alias gap that design did not cover.

### Task A1: Tier detection and capability resolver

**Files:**
- Follow `docs/superpowers/plans/2026-06-14-provider-tier-detection-capabilities.md` in full — it is still accurate against current `main`.

- [x] **Step 1:** Confirm the current Alembic head on `origin/main` and renumber the migration accordingly (the design says `056_account_tier`; verify before claiming that number, per AGENTS.md §1).
- [x] **Step 2:** Execute that plan's tasks 1–8 as written.
- [x] **Step 3:** Verify the regression case: a personal-Google tenant shows `not_applicable` and a capability matrix on `/status`, not a red failure banner.

### Task A2: Accept alias addresses on a user record

**Problem:** `app/routers/auth.py:649` matches users on `func.lower(User.email) == email.lower()`. `app/services/correspondence_capture.py:127` builds its party set from single `Contact.email` values. A grep across `backend/app/services` finds no send-as, alias, or `proxyAddresses` handling anywhere. A firm signing in as `name@gmail.com` while corresponding as `name@theirfirm.com` gets no association between the two.

**Files:**
- Create: `backend/migrations/versions/<next>_user_alias_addresses.py`
- Modify: `backend/app/models/user.py`
- Modify: `backend/app/services/correspondence_capture.py`
- Modify: `backend/app/routers/auth.py`
- Test: `backend/tests/test_user_alias_matching.py` (new)

- [x] **Step 1: Write the failing test** — a user with a verified alias; correspondence addressed to the alias resolves to that user; correspondence to an unverified alias does not.
- [x] **Step 2:** Add an alias table (or a JSONB column) keyed by tenant and user, each entry carrying the address, a verification state, and provenance. Addresses must be unique per tenant to avoid two users claiming one address.
- [x] **Step 3:** Extend the identity lookup to consider verified aliases, primary address first. Never let an unverified alias satisfy a sign-in match — only correspondence matching.
- [x] **Step 4:** Extend `_matter_party_addresses` to include verified aliases for internal users.
- [x] **Step 5:** Surface aliases in the user admin UI so a firm can add one without support involvement.
- [x] **Step 6:** Run the correspondence-capture suite; verify no regression in exact-match behavior.

---

## Workstream B — Folder structure correctness

Must land before Workstream C. Tasks B1–B3 are also live bug fixes independent of any migration.

### Task B1: One folder-naming convention

**Problem:** Three conventions coexist. `app/routers/matters.py:288` creates `"{Matter Name} ({matter_id[:8]})"`. `app/services/cloud_init.py:40` returns `claritylegal-records/{matter_slug}`. `app/services/matter_file_store.py:723,730` (and `915,922` for Google) traverse `["claritylegal-records", matter_slug, category]`. Only the first carries the matter ID.

**Files:**
- Modify: `backend/app/services/cloud_init.py`
- Modify: `backend/app/services/matter_file_store.py`
- Test: `backend/tests/test_matter_folder_naming.py` (new)

- [x] **Step 1: Write the failing test** — resolving a matter's folder path through the provisioner, the canonical helper, and the upload fallback yields the same folder name.
- [x] **Step 2:** Introduce one function that derives the canonical matter folder name from `(matter_name, matter_id)`, and make all three call sites use it. Keep it tolerant of a missing name by falling back to the slug, but always append the ID suffix.
- [x] **Step 3:** Verify against both provider paths (OneDrive and Google Drive) and the SharePoint variant.

### Task B2: Resolve provisioning before the first upload

**Problem:** `_provision_cloud_folders` (`app/routers/matters.py:270`) is fire-and-forget on matter create. When an upload arrives before it completes, `matter_folder_id` is absent, the upload falls to the slug traversal, and `_ensure_onedrive_path` / `_ensure_gdrive_path` (`matter_file_store.py:1569,1582`) create folders as needed — producing a second tree for the same matter.

**Files:**
- Modify: `backend/app/routers/matters.py`
- Modify: `backend/app/services/matter_file_store.py`
- Test: `backend/tests/test_matter_folder_provisioning_race.py` (new)

- [x] **Step 1: Write the failing test** — an upload issued while provisioning is still in flight must not create a second folder.
- [x] **Step 2:** Have the upload path wait on, or retry against, in-flight provisioning rather than falling through to path traversal. The existing `_status` metadata in `cloud_folder` already distinguishes provisioning states — use it rather than adding a new signal.
- [x] **Step 3:** When provisioning genuinely failed, fail the upload with an actionable error instead of silently creating a divergent folder.
- [x] **Step 4:** Add a maintenance command that reports matters holding both a slug-named and an ID-named folder, so existing tenants can be surveyed. Report only — do not merge automatically.

### Task B3: Make the logical path canonical

**Problem:** `app/routers/matters.py:2456` recomputes `cloud_folder["path"]` and `subfolder_paths` on every provision from `provider_metadata.get("folder_name") or matter.slug`. The one provider-independent field is overwritten by whichever provider provisioned last, and inherits whichever naming convention ran.

**Files:**
- Modify: `backend/app/routers/matters.py`
- Test: `backend/tests/test_matter_cloud_folder_metadata.py`

- [x] **Step 1: Write the failing test** — provisioning a second provider onto a matter leaves `path` unchanged, and each provider binding carries its own realized path.
- [x] **Step 2:** Set `path` once, on first provision, from the canonical name function in B1. Move the per-provider realized path under `cloud_folder[provider]`.
- [x] **Step 3:** Backfill is not required; treat a missing canonical `path` as "derive and store on next touch".

### Task B4: Marker file at provision time

**Problem:** Folder identity depends on the name. A partner renaming the folder in Drive or SharePoint breaks every path- and name-based match. Cheap to add now; impossible to backfill for folders that have already drifted.

**Files:**
- Modify: `backend/app/services/cloud_init.py`
- Test: `backend/tests/test_matter_folder_marker.py` (new)

- [x] **Step 1: Write the failing test** — provisioning a matter folder writes a marker whose contents identify tenant, matter, and schema version.
- [x] **Step 2:** Write `.lawhand-matter.json` into each matter folder at provision time. Keep it small and non-secret: tenant ID, matter ID, canonical name, schema version, created timestamp. It is visible to the firm — do not put anything in it that should not be.
- [x] **Step 3:** Write the marker on remap as well, so folders bound manually gain the same anchor.
- [x] **Step 4:** Treat a marker naming a different matter as a hard conflict, never a silent overwrite.

### Task B5: Include Shared Drives in Drive search

**Problem:** `app/services/cloud_search.py` never passes `supportsAllDrives` or `includeItemsFromAllDrives`, so Google Shared Drive content is absent from results. Affects Workspace tenants today, independent of everything else in this plan.

**Files:**
- Modify: `backend/app/services/cloud_search.py`
- Test: `backend/tests/test_cloud_search.py`

- [x] **Step 1: Write the failing test** — the Drive query includes both parameters.
- [x] **Step 2:** Add them to the Drive list and search calls. Confirm `corpora` handling is correct for a tenant with both My Drive and Shared Drives.
- [x] **Step 3:** Confirm personal-tier Google accounts, which have no Shared Drives, are unaffected.

### Task B6: Route `.eml` to the provisioned subfolder

**Problem:** `emails` is one of the six `MATTER_SUBFOLDERS` (`cloud_init.py:28`) but nothing writes to it; `correspondence_capture.py:291` files `.eml` under `correspondence`.

- [x] **Step 1:** Decide whether `emails` or `correspondence` is the intended destination, then make provisioning and capture agree. Do not move existing files.
- [x] **Step 2:** If `emails` is dropped, remove it from `MATTER_SUBFOLDERS` so new matters stop provisioning an empty folder.

---

## Workstream C — Provider migration

Depends on Workstream B. The manual remap endpoint (`PATCH /matters/{id}/cloud-folder/{provider}/remap`) and the context-folder endpoint already exist and are the primitives this workstream orchestrates.

### Task C1: Migration state on the tenant

**Problem:** `TenantSettings.primary_cloud_provider` takes effect on the next write (`matter_file_store.py:127`). Existing documents keep pointing at the old provider and nothing reconciles them.

**Files:**
- Create: `backend/migrations/versions/<next>_storage_migration_state.py`
- Modify: `backend/app/models/tenant.py`
- Create: `backend/app/services/storage_migration.py`
- Test: `backend/tests/test_storage_migration_state.py` (new)

- [x] **Step 1: Write the failing test** — changing the provider while a migration is pending does not silently re-route writes.
- [x] **Step 2:** Add migration state: target provider, phase (`planning`, `reconciling`, `awaiting_confirmation`, `cutover`, `complete`, `abandoned`), started/completed timestamps, and the operator who confirmed.
- [x] **Step 3:** Gate direct edits of `primary_cloud_provider` behind the migration flow when a migration is in progress. Preserve today's behavior when none is.
- [x] **Step 4:** Keep both provider bindings live through the window — `cloud_folder` is already keyed by provider and merges, so no schema change is needed for dual binding.

### Task C2: Discovery and reconciliation

**Approach:** The firm has usually already copied their files using their own provider's migration tooling. LawHand re-attaches to files that have arrived; it does not copy them. Match in a ladder, strongest signal first, and record which rung matched on every document so a bad migration is auditable.

Ladder: (1) marker file from B4 — survives rename; (2) matter ID suffix in the folder name — survives move; (3) canonical path and known subfolders — survives rename but not restructure; (4) `document_sha256` per file — survives everything, used for file-level verification once a folder matches.

**Files:**
- Modify: `backend/app/services/storage_migration.py`
- Test: `backend/tests/test_storage_migration_reconcile.py` (new)

- [x] **Step 1: Write the failing tests** — one per ladder rung, plus an ambiguous case and a missing case.
- [x] **Step 2:** Implement folder-level discovery against the target provider, walking from the tenant cloud root.
- [x] **Step 3:** Treat a duplicate 8-hex ID suffix as ambiguous rather than assuming uniqueness — 32 bits collides well within a large tenant's matter count.
- [x] **Step 4:** Implement file-level verification on `document_sha256`, falling back to filename and size.
- [x] **Step 5:** Bucket every document as matched, missing, or ambiguous, and persist the bucket plus the matching rung.
- [x] **Step 6:** Rebind pointers on matched documents only. Never copy file content. Never delete from the source provider.

### Task C3: Cutover, confirmation, and index purge

**Problem:** `cloud_metadata_index` is keyed `(tenant_id, provider, object_type, object_id)` (`app/models/cloud_metadata.py:22`) and nothing prunes by provider, so post-cutover search returns hits resolving against a disconnected provider.

**Files:**
- Modify: `backend/app/services/storage_migration.py`
- Modify: `backend/app/services/cloud_sync.py`
- Create: `backend/app/routers/storage_migration.py`
- Test: `backend/tests/test_storage_migration_cutover.py` (new)

- [x] **Step 1: Write the failing test** — after cutover, no `cloud_metadata_index` rows remain for the previous provider, and a reindex has been scheduled for the new one.
- [x] **Step 2:** Expose the three bucket counts to an administrator and require explicit confirmation before cutover.
- [x] **Step 3:** On confirmation, flip `primary_cloud_provider`, purge the old provider's index rows, and trigger a reindex.
- [x] **Step 4:** Keep the migration record after completion — it is the audit trail for a firm's file custody changing hands.

### Task C4: Re-runnable onboarding

**Problem:** `onboarding_completed` is a boolean with `/complete` and `/skip` but no reset (`app/routers/onboarding.py`). `/complete` overwrites `tenant.cloud_root_folder` (line 173), so re-running it repoints the tenant root with no record of the previous value.

**Files:**
- Modify: `backend/app/routers/onboarding.py`
- Test: `backend/tests/test_onboarding_rerun.py` (new)

- [x] **Step 1: Write the failing test** — re-entering setup on a configured tenant does not discard the existing cloud root.
- [x] **Step 2:** Make re-entry a distinct flow from first-run: preserve the existing root, and route a provider change into the Workstream C migration rather than re-initializing.
- [x] **Step 3:** Record the previous cloud root whenever it changes.

---

## Workstream D — Corpus posture (decision, then documentation)

Not an implementation task. This is a product decision that gates what the public support page may claim.

**The tension:** `cloud_metadata_index` documents an invariant — routing metadata only, "NEVER stores full document content", fetched live at query time. `firm_memory_*` states it "intentionally do[es] not create a second cloud corpus". But `chunks` (`app/models/document.py:88`) stores `content` as `Text NOT NULL` alongside a tsvector and a pgvector embedding. Semantic search requires vectors; vectors require content to embed. Both postures currently ship without a stated boundary between them.

- [ ] **Decision D1:** Choose the posture for the cloud corpus — metadata-only with weaker recall; embeddings without source text, accepting that embeddings are derived and partially invertible; or stored chunks, dropping any claim that client content is not held. Record the decision and its scope.
- [x] **Task D2:** Bound `cloud_metadata_index.snippet` in the column and document the cap, so the no-content invariant is enforced rather than conventional (`app/models/cloud_metadata.py:59`).
- [ ] **Task D3:** Write the decision into `frontend/platform_docs/administrative-guide/16-integration-data-visibility.md` so administrators can answer a client's data-residency question from the product documentation.

---

## Deferred

- **Audit log and conversation export into matter folders.** The only writes into a matter's cloud folder today are documents and captured `.eml` correspondence; the audit models that exist are operator- and MCP-scoped. Storing matter audit trails and assistant conversations in the firm's cloud is a distinct feature with its own retention, privilege, and discoverability questions. Scope separately.
- **Public support page.** The tier matrix now ships in the administrative guide (`18-cloud-provider-support.md`). A public-facing version belongs in `frontend/src/marketing/`, whose capability catalog already models reviewed public claims with an owner and review date. Gate it on decision D1, since it will make a data-handling claim.

---

## Sequencing

1. **A1, B1, B2, B5** — independent, and each fixes something wrong today. Start here.
2. **B3, B4, B6** — structural preparation for migration.
3. **A2** — alias support; gates any personal-tier onboarding with a custom domain.
4. **D1** — decide before anything public is written.
5. **C1–C4** — migration, once B is complete.

## Verification

- Backend: `py -m pytest` from `backend/`. Tests build schema via `Base.metadata.create_all`, so new columns are available without running Alembic.
- Frontend: `npm test` from `frontend/`. Adding a guide chapter requires updating the length expectations in `src/platformDocs.test.js`.
- Migrations: confirm the head on `origin/main` before claiming a number, and update every hardcoded head expectation listed in AGENTS.md §1.
- Diff coverage must clear 80%; budget test-writing time up front rather than at merge.

## Execution record — 2026-09-07

A1–A2, B1–B6, C1–C4 and D2 are implemented on `feat/cloud-provider-remediation`.
The broader D1 policy and decision-dependent D3 remain pending; the admin guide
records the existing metadata-index versus persisted-chunks boundary without
claiming a new platform-wide retention policy. The public page stays deferred.

The root remains `claritylegal-records`; new matter folders carry the canonical
name and eight-character UUID suffix. Existing folders are retained by ID; new
captured EML files use `correspondence`. Staff and portal imports keep their
explorer-relative paths below the same bound matter root. The independently
merged matter-import changes from PR #350 are included in final integration review.

Migration revisions are 160–162 after `159_navigation_profiles`. The existing
RBAC capability catalog is preserved; tier capabilities extend it. Cutover and
uploads serialize on tenant state after credential refresh; stale bindings fail
with a retry message. Once a tenant has completed a migration, subsequent provider
changes also require migration so an old reindex cannot restore retired pointers.
The pre-existing direct setting behavior remains available for tenants with no
migration history.

Local validation covers provider transport behavior, aliases and tier detection,
provisioning and upload races, reconciliation and cutover, retryable indexing,
offline migration upgrade/downgrade SQL and the admin UI. PostgreSQL lifecycle
checks and the complete required CI gate remain required before merge.

The preview cap uses an additive `char_length(snippet) <= 500` database check
with a scoped cleanup of legacy oversized previews. It preserves the existing
TEXT type for rolling-release compatibility and passes the tenant migration
safety gate without an in-place type change.

## Live-validation follow-up — 2026-09-07

The existing CyberSafeAdvisor Microsoft 365 production tenant was exercised with synthetic matter files and a no-attendee calendar event. Direct OneDrive upload/read and provider discovery worked. The deployed `c52cb84b` release exposed multipart import failures, missing fetched-content display, matter content retrieval gaps, a calendar time mismatch, and an SMTP-only matter-email route. The approved synthetic self-email failed before delivery.

The corrections from PRs #357, #358, and #360 are consolidated with connected-mail and captured `.eml` identity corrections in [PR #361](https://github.com/mattpainter701/lawhand/pull/361). Focused tests cover provider/matter boundaries, drive-qualified SharePoint identities, calendar deletion outcomes, and capture followed by cloud-file reads. Final-head CI, deployment, and the complete live roundtrip remain separate acceptance steps; local tests do not establish production success.

D1 and D3 remain pending the owner's corpus-policy decision. No public support page, provider migration, root rename, or SharePoint team site was introduced by this validation follow-up.
