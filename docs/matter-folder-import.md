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
