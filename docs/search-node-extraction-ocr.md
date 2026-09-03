# Search Node extraction and OCR operations

## Status and boundary

This is an additive, default-off worker foundation for the Firm Memory Search
Node. It is deliberately outside the SMB agent and LawHand backend processes.
The implementation ends at an engine-neutral search sink; it does not implement
OpenSearch, crawler reconciliation, portal UI, native per-user ACL filtering, or
embeddings.

`ManifestQueue` is the durable source of leased extraction/OCR jobs and terminal
status. `SearchSink.publish()` accepts idempotent normalized revisions. A sink
acknowledgement is required before the queue records extraction complete. The
cross-stage identity is `(source_id, file_id, content_version)` and every claim
carries an opaque lease token/generation. Completion, retry, and renewal must
compare that token and identity so a stale worker cannot acknowledge a newer
file version. `content_fingerprint` is SHA-256 evidence; paths are mutable
metadata, never identity. Sink idempotency additionally fences on
`pipeline_version`, and adapters must reject an older content or pipeline
revision overwriting a newer acknowledged record.

The manifest adapter must stage an immutable, regular file beneath
`SEARCH_NODE_STAGING_ROOT` before leasing it. The job's `size_bytes` must match
the staged file. Symlinks and paths outside that root fail closed. Source-share
reading and change reconciliation remain crawler responsibilities and are not
part of this package.

## Processing sequence

1. The extraction pool leases a manifest job.
2. Its supervisor validates the staged path and size, creates a private temp
   directory, then starts a one-shot parser child. The child receives a bounded
   JSON request and returns one bounded JSON response.
3. A successful native record is sent to the search sink immediately with
   `indexed-ready`. Low-text PDF page numbers are then placed on the OCR queue.
4. The separately scheduled OCR pool renders only those pages and runs
   Tesseract with configured local language packs and a timeout per page.
5. OCR text is published as an `ocr-enrichment` revision with page provenance
   and mean recognized-word confidence. An OCR failure is terminal and visible,
   but does not retract the already searchable native revision.

Queue adapters should use expiring leases, retry backoff, and dead-letter views.
The OCR adapter must atomically renew the short claim to the per-page timeout
budget before OCR begins. Adapters should not treat a lease as completion or
delete the previous good search
record before a replacement is acknowledged.

## Planned FM-03/FM-04 adapters

This draft intentionally does not import either parallel, unmerged branch. The
later FM-04 queue adapter maps its extraction request to `ManifestJob`, retaining
`source_id`, `file_id`, `content_version`, SHA-256 fingerprint, lease token,
optional `matter_ids`, and stable delete/tombstone semantics. Tombstones remain
an FM-04 reconciliation outcome and address stable file identity, not path.

The later FM-03 sink adapter maps each `Section` to `DocumentChunk` and calls the
local `LocalSearchEngine.bulk_index` boundary. The normalized record already
carries deterministic `chunk_id`, content, page number, section path, ordinal,
offsets, share/source identity, relative path, filename, extension, optional
matter IDs, and explicit pending ACL state. ACL tokens remain empty here until
FM-06 supplies reviewed tokens. This package does not add a second full-text
store.

## Terminal status contract

Every extraction/OCR attempt ends in exactly one of:

| Status | Meaning |
|---|---|
| `indexed-ready` | A normalized native or OCR revision has been acknowledged by the sink. |
| `unsupported` | The format or required local parser runtime is unavailable. |
| `encrypted` | A password/encryption boundary prevented extraction. |
| `corrupt` | The container, parser envelope, or document was malformed. |
| `too-large` | An input, output, page, archive, embedded-file, unpacked-byte, or temp budget fired. |
| `permission-denied` | Staging access or path containment failed. |
| `timed-out` | The parser or page OCR wall-time limit fired. |
| `ocr-failed` | Rendering, Tesseract, language/runtime, or OCR temp processing failed. |
| `skipped` | The immutable staging precondition changed (for example size mismatch). |

Error codes name the precise guard without echoing source text or file paths.

## Format phases

Phase 1 is implemented with inert parsers inside the child:

- PDF native text, page by page; encrypted PDFs are classified;
- OOXML Word/Excel/PowerPoint XML, including macro-enabled containers without
  ever executing their macro payloads;
- RTF, TXT/LOG/Markdown, HTML, XML, CSV, and JSON;
- EML headers, bodies, and bounded attachments;
- ZIP archives with depth, member-count, path, compression-ratio, and cumulative
  unpacked-byte limits.

Phase 2 is enabled by mounting a reviewed Apache Tika application JAR and setting
`SEARCH_NODE_TIKA_APP_JAR`. It covers legacy DOC/XLS/PPT, Outlook MSG, and
OpenDocument ODT/ODS/ODP. Tika runs below the same disposable child timeout and
resource envelope. Those formats return `unsupported/tika-runtime-unavailable`
when the reviewed runtime is absent. This is intentional capability reporting,
not silent omission. Tika output for these formats is section-aware but may not
have reliable page numbers.

Phase 3 OCR handles image-only or below-threshold PDF pages using local Poppler
and Tesseract. Other image formats, non-ZIP archive types, handwriting-specific
models, and table reconstruction are not claimed by this draft.

## Mandatory sandbox deployment

Do not set `SEARCH_NODE_SANDBOX_VERIFIED=true` until the service manager or
container configuration enforces all of the following. The setting is an
operator attestation and a fail-closed startup gate, not a substitute for the
controls:

- run extraction and OCR as separate, unprivileged services, never in an API or
  long-lived agent process;
- disable the network namespace entirely (`network_mode: none` or equivalent),
  clear proxy variables, and do not mount sockets;
- mount staging and the reviewed Tika JAR read-only; mount no SMB credentials;
- use a private `tmpfs` or quota-limited volume at `SEARCH_NODE_TEMP_ROOT` with
  `noexec,nosuid,nodev` and the configured temp-byte ceiling;
- apply the configured memory limit plus CPU/wall limits, `no-new-privileges`,
  a read-only root filesystem, and no Linux capabilities;
- bound process count with the cgroup's `pids.max`, which is scoped to the
  worker's own tree. The supervisor deliberately does not set `RLIMIT_NPROC`:
  the kernel counts it per real UID rather than per descendant tree, so it does
  not bound the subtree and does fail unsafely — any other process owned by the
  service account consumes the same budget and the parser then dies with EAGAIN
  on a fork it was entitled to make;
- use the included Tika config, do not enable external parsers, and do not add
  LibreOffice/Office automation or macro execution;
- install language packs during image construction or host provisioning. Never
  let a worker download code or data;
- send stdout/stderr only to bounded operational logging. Document text belongs
  in the sink boundary, not logs.

On POSIX, the supervisor additionally sets address-space (or, when a Tika JAR is
configured, data-segment), CPU, file-size, and file-descriptor limits plus
`no_new_privs` before exec, and puts the child in its own session so the whole
tree can be signalled. A JVM reserves far more address space than it commits, so
`RLIMIT_AS` is replaced by `RLIMIT_DATA` whenever `SEARCH_NODE_TIKA_APP_JAR` is
set; without that substitution `java` cannot start under any bound worth setting.

On Windows the supervisor now assigns the child to a job object carrying memory,
active-process, and CPU-time limits that terminates the whole tree, so a Tika JVM
cannot outlive the parser that spawned it. Still deploy in a container/VM or
service wrapper for the network and temp-volume controls the job object does not
provide; a plain Windows subprocess is not a verified production sandbox.

## Configuration

The worker is off unless both `SEARCH_NODE_ENABLED=true` and
`SEARCH_NODE_SANDBOX_VERIFIED=true` are present.

| Variable | Default | Guard |
|---|---:|---|
| `SEARCH_NODE_MAX_INPUT_MIB` | 100 | 1–1024 MiB |
| `SEARCH_NODE_MAX_OUTPUT_MIB` | 20 | 1–256 MiB |
| `SEARCH_NODE_WALL_SECONDS` | 120 | 1–3600 seconds |
| `SEARCH_NODE_MEMORY_MIB` | 768 | 128–8192 MiB |
| `SEARCH_NODE_MAX_EMBEDDED` | 100 | 0–1000 files |
| `SEARCH_NODE_ARCHIVE_DEPTH` | 2 | 0–5 levels |
| `SEARCH_NODE_MAX_UNPACKED_MIB` | 250 | 1–4096 MiB |
| `SEARCH_NODE_TEMP_MIB` | 512 | 16–8192 MiB |
| `SEARCH_NODE_MAX_PAGES` | 2000 | 1–10000 pages |
| `SEARCH_NODE_OCR_PAGE_SECONDS` | 90 | 5–600 seconds/page |
| `SEARCH_NODE_LOW_TEXT_CHARS_PER_PAGE` | 80 | 0–5000 characters |
| `SEARCH_NODE_OCR_LANGUAGES` | `eng` | `+`-separated installed packs |
| `SEARCH_NODE_OCR_START_HOUR` / `END_HOUR` | 20 / 6 | local off-hours window |

The OCR worker does not lease work outside its off-hours window. Queue-side
rate limits should additionally protect source disks and reserve capacity for
interactive use.

## Operator response

- Watch terminal-state counts and error-code rates by extension; never calculate
  coverage only from successful rows.
- A spike in timeouts, archive limits, or corrupt documents is a reason to
  quarantine and sample safely, not to raise every limit globally.
- Keep staging and OCR derivatives under the same customer-controlled retention,
  encryption, backup, and incident-response policy as source documents.
- When a pipeline version changes, replay through new idempotency identities and
  retain the previous acknowledged record until the sink accepts its replacement.
- Test the exact configured Tika, Java, Poppler, Tesseract, and language-pack
  versions before rollout. This repository does not download or auto-upgrade them.

CI creates small malformed, encrypted, traversal, nested-archive, and
high-compression fixtures at runtime. It does not store or inflate a real archive
bomb and does not use customer documents.
