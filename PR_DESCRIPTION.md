## Summary

Matter documents rendered as one flat, ever-growing list. This adds a
file-explorer view over them — a per-matter folder tree, a firm-wide tag
vocabulary, server-side search/scoping/sorting — and makes the firm's bound
cloud share mirror the same tree.

**Backend**

- Migration `154_matter_document_folders` adds `matter_document_folders`
  (hierarchical, materialized `path`, depth ≤ 8, case-insensitive sibling
  uniqueness folded over a NULL-parent sentinel), `matter_document_tags`
  (tenant-unique on `lower(name)`, fixed colour palette),
  `matter_document_tag_links`, and `matter_documents.folder_id`. All three new
  tables enable and **force** RLS on `app.current_tenant_id`.
- `matter_documents.folder_id` is **`ON DELETE RESTRICT`**, not `SET NULL`: a
  folder can never delete or orphan the documents filed in it. The delete
  endpoint refuses a non-empty folder unless the caller passes
  `?move_documents_to_parent=true`, which re-files the whole subtree's documents
  into the parent in the same transaction before the delete.
- Tree rules live in `app/services/matter_document_organization.py` so the firm
  UI, the client portal, and any later importer enforce the same shape: sibling
  name collisions, depth, path re-materialization on rename and move, cycle
  rejection via path containment, and protected `kind='system'` folders.
- `GET /api/matters/{id}/documents` gains `folder_id` (a UUID or `root`),
  `include_subfolders`, `q`, repeated `tag_ids`, `sort`, and `order`. Tag
  filtering is conjunctive (one `EXISTS` per tag). `q` escapes LIKE wildcards so
  a literal `%` in a filename is searchable. **Omitting `folder_id` returns
  every document in the matter, exactly as before — existing callers are
  unaffected.**
- New endpoints: folder CRUD, bulk document filing, firm-wide tag CRUD, and
  per-document tag assignment.

**Cloud share parity**

- `store_matter_file_result` takes an optional `folder_path`. When present the
  file is written to `<matter folder>/<folder path…>` — hanging off the matter
  folder rather than a category subfolder, so one explorer folder is one place
  in the share — creating missing segments on demand for OneDrive, SharePoint,
  and Google Drive, and mirroring the same tree locally for unbound tenants.
  Uploads with no folder keep the historical category layout untouched.
- System folders route to the canonical subfolder their `system_key` names, so a
  matter already provisioned in the share does not grow a second,
  differently-cased `Client Uploads` beside `client_uploads`.

**Client portal**

- Portal uploads already worked; they now also file themselves into the matter's
  protected **Client Uploads** folder, created once on first use (and adopting a
  hand-made root folder of the same name rather than colliding with it), so the
  firm's explorer groups them without any manual step.

**Frontend**

- `MatterDocumentsTab` becomes an explorer: a folder rail with counts, inline
  create/rename/delete, breadcrumbs, drag-and-drop filing of document rows onto
  folders, search, tag chips and a conjunctive tag filter, sort controls, and a
  folder picker on upload that defaults to the folder currently open. Filtering
  is server-side, so a matter with thousands of files never ships every row to
  the browser to render one folder.

### Double-check: is matter→cloud-share binding automated after the wizard?

Yes on the intended path, with one silent-failure route worth knowing about.
`POST /api/onboarding/complete` refuses to finish without a connected Microsoft
or Google integration, then stores `tenants.cloud_root_folder`; `POST
/api/matters` fires background folder provisioning whenever that is set; and
re-authorizing from Admin repairs a missing root. **The gap:** if
`initialize_cloud_root_folder` raises during `/complete` the exception is logged
and swallowed and onboarding still completes with the root unset, and
`POST /api/onboarding/skip` never sets it at all. Every matter created afterwards
then skips provisioning with nothing retrying on its own — recovery is manual
via **Set Up Cloud Folder** or `POST /api/integrations/cloud-init/retry`. That
is pre-existing behaviour; this PR documents it in
`docs/matter-document-organization.md` rather than changing onboarding.

### Stated boundary

Moving an existing document between folders changes where it appears in the
explorer; the copy already written to the cloud share stays at the path it was
uploaded to. Relocating provider objects is a write against the customer's
tenant with its own throttling, auth, and partial-failure modes and deserves its
own reconciliation story rather than riding along here. Called out in the docs
and in the endpoint's docstring.

## Validation

- `backend`: full `pytest tests/` suite green, including 42 new tests in
  `tests/test_matter_document_folders.py` (tree rules, depth and cycle limits,
  system-folder protection, folder-scoped/recursive/search/tag/sort listing,
  bulk filing, tag lifecycle, cross-tenant fences) and a new client-portal test
  proving uploads file into `Client Uploads` and create it exactly once.
- Migration exercised against a real PostgreSQL 
  (`alembic upgrade head` → `downgrade 153_sms_lifecycle` → `upgrade head`),
  with `relrowsecurity`/`relforcerowsecurity` and the RESTRICT delete rule
  verified on the resulting schema.
- Head expectations updated per `AGENTS.md` §1: `tests/test_migrations.py`,
  `tests/test_studio_render_migration.py`, `scripts/rehearse_configurable_workflows.py`.
  `tests/test_sms_migration.py` now asserts 153 is an *ancestor* of the pinned
  head instead of hard-coding it as the head, so it stops needing an edit per
  migration.
- `test_demo_registry.py` caught the new tables missing a purge policy; all three
  are registered as clone tables.
- `frontend`: `vitest run` — 550 tests across 96 files green, including 9 new
  explorer tests; `eslint src` clean; jest-axe accessibility assertions still
  pass on the reworked layout.
- `ruff check` / `ruff format --check` on `backend/app/` clean.
- `python scripts/generate_release_notes.py --check` passes.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [x] Customer release notes updated
- [ ] No customer-facing release note
- [x] Security and privacy impact reviewed

Security and privacy: three new tenant-scoped tables, all with FORCE RLS and
composite `(tenant_id, id)` foreign keys so a link row cannot span tenants. No
data access was widened — folders and tags are additive, the document list
without `folder_id` returns exactly what it returned before, and client-portal
visibility is still governed solely by `portal_visible`. The folder reference is
RESTRICT specifically so document deletion can never become a side effect of
folder deletion. Folder names are validated at the service layer and the storage
layer independently refuses any path segment that could escape the matter
folder.

## MCP documentation handoff

- [ ] MCP documentation updated
- [x] MCP documentation not needed
- MCP area: backend app composition root (`backend/app/main.py`) only — one
  `include_router` line for the new document-explorer router.
- Wiki handoff note: no MCP endpoint, tool, protocol, authorization boundary,
  artifact/review workflow, or client contract changed. The new routes are
  firm-session REST endpoints under `/api`, authenticated by the existing
  `get_current_user` dependency and fenced by the same tenant RLS as the rest of
  the matter surface; no MCP tool exposes them and no MCP client contract
  references them. Feature documentation lives in
  `docs/matter-document-organization.md`.
