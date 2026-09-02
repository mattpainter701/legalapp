# Agent guidance for this repository

Hard-won rules to avoid re-breaking CI on multi-PR work. Read before touching
migrations, release notes, the CI workflow, or rebasing a feature branch.

## 1. Migration head protocol (linear, never parallel)

- Alembic has **one linear chain**. There is never more than one head.
- The next head number is assigned **centrally**, never independently by two
  parallel PRs. In the studio/comp/fm wave, `149` was claimed by 4 PRs at once;
  each had to be renumbered to `151`/`152`/`153` after their predecessors merged.
- When you start a branch that adds a migration, confirm the current head on
  `origin/main` first, and pin `down_revision` to it.
- After rebasing a branch onto a moved `main`, re-verify and update every hardcoded
  head expectation:
  - `backend/tests/test_migrations.py` (`test_alembic_revision_graph_resolves_heads`)
  - `backend/tests/test_studio_render_migration.py`
  - `backend/tests/test_sms_migration.py` / `test_configurable_workflow_migration.py`
  - `scripts/rehearse_configurable_workflows.py` (`EXPECTED_HEAD`)
  - `.github/workflows/ci.yml` (rehearsal `alembic upgrade <head>` lines)

## 2. CI gates you must build to, not discover later

- **Diff coverage ≥ 80%** (`diff-cover coverage.xml --fail-under=80`). Big feature
  PRs add thousands of lines — budget test-writing time up front, or the merge
  gate will block you at the end. Per-file floors matter too (a module at 50%
  hides real gaps even when the aggregate is green).
- **Release notes**: `python scripts/generate_release_notes.py --check` must pass.
  `backend/app/release_notes.json` releases must be ordered **newest first** by
  `id`; regenerate `RELEASE_NOTES.md` after any edit.
- **Merge policy**: `scripts/verify_merge_policy.py` requires the PR body to check
  exactly one documentation option, exactly one release-note option, and
  "Security and privacy impact reviewed". Use the template in
  `.github/pull_request_template.md`.

## 3. Dependabot PRs

Dependabot does not inherit the PR body template, so its PRs fail the merge-policy
gate. Fix with `gh pr edit <n> --body-file` adding the three checkboxes
(No documentation impact / No customer-facing release note / Security and privacy
impact reviewed), then `gh pr update-branch <n>`.

## 4. Rebase hygiene on this repo

- `ci.yml` is huge and shared; auto-merge during rebase has twice produced
  **duplicate job keys** (two `test-backend:`) or a name-only job with no body,
  which fails the whole workflow silently. After resolving conflicts, validate:
  `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` and
  check job keys are unique.
- `release_notes.json` and `CHANGELOG.md` and test files with shared constants also
  get corrupted by naive "keep both sides" auto-merge. After merging, run
  `python -m json.tool` on the JSON and `python -m py_compile` on edited test files.

## 5. Test/async footguns

- `pytest --maxfail=1` masks downstream failures; a branch can look like it has one
  failure when it has ten. When a branch has been failing for a while, run the
  whole suite (or at least the affected files) before declaring it fixed.
- On the async SQLAlchemy session, `db_session.expire_all()` and
  `db_session.rollback()` expire persistent ORM objects. Accessing `.id`/attributes
  on those objects afterward triggers a sync refresh → `MissingGreenlet`. Snapshot
  ids into plain locals before expiring, or `await db_session.refresh(obj)`.

## 6. Sequencing when many PRs target main

- Merge in dependency order; migration PRs form a chain, non-migration PRs
  (agent/search-node/doc changes) can merge in any order.
- After each merge, rebase the remaining branches onto the new `main` (new head
  may have moved). `gh pr update-branch <n>` is not a rebase; use git rebase and
  re-run the head checks from section 1.
