# Import existing matters and historical correspondence

Matter managers (including authorized secretaries, paralegals and attorneys) can
use **New Matter → Import existing matters**, or **Documents → Import files &
emails** within an existing matter. The backend requires `manage_matters`, then
checks owner/assignment/admin access for every existing destination. A client
portal user cannot access these staff endpoints. A saved batch is private to its
creator and tenant.

Select a folder or ZIP, choose the folder level that represents individual
matters, and review every group. Select an existing destination or enter a new
matter and client; an existing contact can be reused for multiple matters. No
contact is merged based on a guessed name. Groups may be excluded explicitly.
New matters can be marked existing engagement, transfer review required, or
fresh intake required. These stage labels do not send any communication; start
the intake packet separately after reviewing the transferred matter.

The confirmed manifest binds every source path to a size, SHA-256 digest and
destination. Confirmation creates matters in one transaction. File results are
committed individually. Save the displayed import ID to resume, reselect the
same source, and retry unfinished files. Do not remove the source until the
report accounts for every file. Failed files are listed and never counted as
success. A lost confirmation response replays the original mappings rather than
creating another set of matters. Changing confirmed mappings requires a new
batch. Cross-batch content duplicates are linked within the destination matter;
the same content can legitimately be imported into another matter.

Folder hierarchy and displayed filenames are preserved. Physical storage names
include a content-hash prefix to prevent an unrelated file from being overwritten.
Storage uses the existing MatterFileStore policy for OneDrive, SharePoint or
Google Drive, including provider object IDs. A configured cloud failure does
not fall back to local storage. The existing legacy development storage fallback
is unchanged. Uploaded matter documents remain private until explicitly shared.

EML files produce a normal Correspondence entry plus the untouched original
email document. Original headers, attachment names, source path and import
attribution remain in provenance. Attachments remain available inside the
original EML; extracting them as separate matter documents is not part of this
release. Plain text is displayed; HTML and remote images are not rendered by
the importer. An optional list of former attorney addresses determines direction;
unmatched messages retain unknown direction. Missing/unparseable historical
dates are flagged, with import time used for timeline ordering. Message-ID and
References support basic threading. No provider mailbox or former domain login
is required, and subject task tags never create tasks or deadlines.

Limits: 10,000 files, 64 MiB per file, ZIP 512 MiB compressed and 1 GiB expanded,
and the existing folder depth/name limits. Encrypted ZIP entries, symlinks,
unsafe paths and colliding paths are rejected. Folder uploads send one file at
a time; ZIP processing saves each entry separately. Closing during hashing or
upload requires source reselection. These are resumable file-level operations,
not resumable byte offsets or a background upload from an unplugged USB drive.
Reverse proxies must permit the documented upload sizes and processing time.

This release uses the existing external import tables; there is no migration,
no automatic extraction of case facts, no inferred legal deadlines and no
automatic intake completion from historical documents. These REST routes are
not exposed as Workspace MCP tools. Related artifact and review clients must
continue to respect the destination matter's authorization and visibility.

Validation covers archive bounds/traversal, unchanged email bytes, unsafe HTML,
confirmation replay, per-file retry, cloud routing, duplicate filing and failed
authorization. PostgreSQL tests exercise persisted records and replay with the
real CI database; provider calls use controlled test adapters.

## Client portal transfers and shared links

In **Portal → Documents**, clients can select multiple files, choose a folder,
or drop files/folders. They review the source paths before uploading. Every file
gets a result, and **Upload remaining files / retry failures** skips successful
items. Keep the page and source available during transfer. After a browser
restart, reselecting the same paths and bytes reuses the received documents;
changed bytes or source paths create separate documents. Staff-hidden documents
are not automatically shared again by replaying an upload.

The portal is restricted to its invited matter. A USB drive containing a dozen
different clients belongs in the staff **New Matter → Import existing matters**
workflow, where destinations can be reviewed individually. Client folders are
filed beneath **Client Uploads**, with up to eight source folder levels and
10,000 files per selection. The portal's configured per-file upload limit and
file-type allowlist still apply. It stores ZIPs as original bundles; the staff
ZIP import unpacks them. EML uploads preserve the original bytes and add plain
text historical correspondence without sending mail or creating tasks. Embedded
attachments remain in the original email. PNGs, scans and older Word documents
can be stored without implying their contents have been extracted.

Clients can submit an HTTPS cloud/fileshare source link from Documents. This
creates a portal message in the matter's Correspondence for staff review; it
does not fetch the URL or import its contents automatically. Staff must have
provider access. For connected OneDrive/Google Drive folders, use the matter's
**File Shares** controls to bind a folder URL or add a context folder, then sync.
For other providers, download the reviewed source and use the folder/ZIP importer.

In **Matter → Documents → Portal upload-folder link**, authorized matter staff
can explicitly publish an HTTPS client-safe file-request or upload-folder link.
Configure the provider's client access before publishing. This is separate from
the firm's private matter-folder URL, which is never exposed automatically.
Clearing and saving removes the link from the portal; revoke provider sharing
separately if the old link must stop working. Changes are recorded as matter
events. The published link does not itself configure a cloud sync binding.

## Refresh, sync and indexing are separate

- **Refresh document list** reloads saved attachments and folder counts. The
  portal refresh shows only documents visible to that portal matter.
- **Sync folder** refreshes metadata for connected cloud folders. SMB shares use
  their configured agent scanner and matter binding. Neither action means that
  every file's text has been extracted or OCR completed.
- Ordinary matter uploads and the folder importer save `MatterDocument` records;
  they currently do **not** enqueue a content extraction/indexing job. Staff do
  not have an automatic matter-wide OCR/indexing control in this release.

The general `/documents` upload pipeline queues extraction/chunking/embedding for
its own document corpus; it is not the matter attachment importer. Do not copy
private matter files into that index without implementing matter-level retrieval
authorization. The Firm Memory search-node pilot separately indexes authorized
SMB sources, with runtime/configuration requirements and partial OCR coverage
described in [Firm Memory launch readiness](firm-memory-launch-readiness.md).

Remaining indexing work is a durable matter-scoped pipeline: source version/hash
tracking, extraction for email/Word/PDF, OCR for image-only pages, per-file
pending/ready/partial/failed states, retry and reindex controls, and live matter
authorization on search and preview. Deletes, source edits and permission changes
must invalidate stale searchable content. This release does not claim those
capabilities or activate customer indexing.
