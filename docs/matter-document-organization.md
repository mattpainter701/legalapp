# Matter document organization — folders and tags

Case documents used to render as one flat, ever-growing list per matter. This
document describes the folder tree and tag vocabulary that replaced it, how the
storage layer mirrors that tree into the firm's bound cloud share, and where the
current boundaries are.

## Model

| Table | Scope | Notes |
| --- | --- | --- |
| `matter_document_folders` | tenant + matter | Hierarchical, max depth 8. Carries a materialized `path` (`Discovery/Depositions`) rebuilt on every rename and move. |
| `matter_document_tags` | tenant | Firm-wide vocabulary, unique on `lower(name)`, colour drawn from a fixed palette. |
| `matter_document_tag_links` | tenant | Assignment of one tag to one document. Composite FKs to `(tenant_id, id)` on both parents. |
| `matter_documents.folder_id` | tenant | `NULL` means the document sits at the matter root ("Unfiled"). |

All three tables enforce row-level security on `app.current_tenant_id`, the same
fence every other tenant-scoped table uses.

### Documents are never deleted with a folder

`matter_documents.folder_id` references the folder with **`ON DELETE RESTRICT`**,
not `SET NULL`. Deleting a folder therefore cannot silently take its contents
with it at the database level. The API refuses to delete a folder that still
holds documents unless the caller passes
`?move_documents_to_parent=true`, which re-files the whole subtree's documents
into the deleted folder's parent inside the same transaction before the delete.

### System folders

A folder with `kind = 'system'` is owned by the product rather than the firm.
Today there is one: `client_uploads`, displayed as **Client Uploads**. It is
created on the first client portal upload for a matter, cannot be renamed or
deleted (nor can any ancestor of it be deleted), and is where every portal
upload is filed. A firm that had already hand-made a root folder of the same
name has it adopted rather than duplicated.

## Cloud mirroring

The matter's cloud folder is provisioned with a fixed set of canonical
subfolders (`emails`, `client_uploads`, `documents`, `pleadings`,
`correspondence`, `billing`) — see `app/services/cloud_init.py`. Explorer
folders layer on top:

- **No folder** → unchanged behaviour. The file is routed by
  `document_category` into the canonical subfolder, exactly as before this
  change.
- **A user folder** → the file is written to `<matter folder>/<folder path…>`,
  hanging off the matter folder itself rather than off a category subfolder, so
  one explorer folder is one place in the share instead of a name repeated under
  every category. Missing path segments are created on demand
  (`_ensure_onedrive_path`, `_ensure_gdrive_path`, `_ensure_sharepoint_path`).
- **A system folder** → routed to the canonical subfolder its `system_key`
  names, so a matter already provisioned in the share does not grow a second,
  differently-cased `Client Uploads` folder beside `client_uploads`.

Tenants with no cloud binding keep their local layout, mirrored the same way
under `UPLOAD_DIR/<tenant>/matters/<slug>/<folder path…>`.

### Known boundary: moving a document does not move the stored copy

`POST /api/matters/{id}/documents/move` changes where a document appears in the
explorer. A copy already written to the firm's cloud share stays at the path it
was uploaded to. Only new uploads are written to the mirrored folder path.
Relocating provider objects on move is a separate change: it is a write against
the customer's tenant with its own throttling, auth, and partial-failure modes,
and it deserves its own reconciliation story rather than riding along here.

## Is matter-to-cloud binding automated after the setup wizard?

Yes, on the intended path, with one silent-failure route worth knowing about.

1. `POST /api/onboarding/complete` refuses to finish unless at least one
   Microsoft or Google integration is connected, then calls
   `initialize_cloud_root_folder` and stores the result on
   `tenants.cloud_root_folder`.
2. `POST /api/matters` fires `_provision_cloud_folders` as a background task
   **whenever `tenant.cloud_root_folder` is set**, creating the matter folder
   and its canonical subfolders and sharing them with the matter's assignees.
   Failures are written back as `_status: "failed"` on `matter.cloud_folder` so
   the matter's Cloud Storage card can surface them.
3. Re-authorizing an integration from Admin runs `_ensure_cloud_root` after
   every admin OAuth connect, which repairs a tenant whose root was missing.

The gap: if `initialize_cloud_root_folder` raises during step 1 the exception is
logged and swallowed, and onboarding completes with `cloud_root_folder` unset.
`POST /api/onboarding/skip` never sets it at all. In either case every matter
created afterwards skips step 2 entirely and nothing retries on its own —
recovery is manual, via **Set Up Cloud Folder** on the matter's Cloud Storage
card or `POST /api/integrations/cloud-init/retry`, which backfills every matter
in the tenant. Matters created before the root existed are only fixed by that
same retry.

## API

Firm-side, all tenant-scoped and authenticated:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/matters/{id}/document-folders` | Whole tree with per-folder document counts and the unfiled count. |
| `POST` | `/api/matters/{id}/document-folders` | Create a folder under an optional parent. |
| `PATCH` | `/api/matters/{id}/document-folders/{folder_id}` | Rename and/or reparent. Omitting `parent_id` leaves the parent alone; sending `null` moves the folder to the root. |
| `DELETE` | `/api/matters/{id}/document-folders/{folder_id}` | Delete a subtree; `?move_documents_to_parent=true` re-files its documents. |
| `POST` | `/api/matters/{id}/documents/move` | File documents into a folder, or back to the root with `folder_id: null`. |
| `GET` | `/api/matters/{id}/documents` | Now accepts `folder_id` (a UUID or `root`), `include_subfolders`, `q`, repeated `tag_ids`, `sort`, `order`. |
| `POST` | `/api/matters/{id}/documents/upload` | Now accepts a `folder_id` form field. |
| `GET`/`POST` | `/api/document-tags` | List and create firm-wide tags. |
| `PATCH`/`DELETE` | `/api/document-tags/{tag_id}` | Rename/recolour, or delete firm-wide. Deleting a tag removes its assignments and never touches the documents. |
| `PUT` | `/api/matters/{id}/documents/{doc_id}/tags` | Replace a document's tags with exactly the supplied set. |

Filtering by several `tag_ids` is **conjunctive**: a document must carry every
requested tag. `q` matches filename or description, with LIKE wildcards escaped
so a literal `%` in a filename is searchable.

Omitting `folder_id` from the list endpoint returns every document in the
matter, which is what the previous flat list did — existing callers are
unaffected.
