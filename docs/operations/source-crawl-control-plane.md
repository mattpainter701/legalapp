# Source crawl and freshness control plane

The LawHand agent includes an additive, locally executed source-ingestion
control plane in `clarity_agent.crawl_control`. It is disabled by default and
is not wired into the existing matter-scoped file-share scanner or local
full-text index. Activating it requires an explicit composition root to provide
source, extraction, indexing, and (optionally) ACL-refresh adapters.

## Boundaries

- A `SourceRoot` is the crawl authority. Matter IDs are optional enrichment
  metadata and never gate discovery.
- `SourceAdapter` owns provider I/O. `ExtractionSink`, `IndexSink`, and
  `AclRefreshSink` are handoff contracts; the control plane does not implement
  OpenSearch mappings, Tika/OCR, ACL trimming, or portal behavior.
- Provider paths are canonical, traversal-free paths relative to the configured
  source root. Discovery adapters may return an absolute descendant, but stat
  and streaming-read calls receive only the canonical relative path.
- Extraction writes its output to a separately secured local artifact store and
  returns only a bounded opaque reference and hash. The crawl SQLite database
  never stores document text, snippets, parsed fields, or extracted payloads.
- `CallbackHintAdapter` admits Windows USN or SMB change-notify providers as
  low-latency hints. It does not treat those streams as authoritative.
- The manifest must live on a local disk in a dedicated directory. UNC-hosted
  SQLite databases, mapped/network volumes, unknown Linux filesystems, and
  paths whose permissions cannot be restricted are rejected fail closed.
- Extraction and index/delete adapters must be idempotent by source file ID and
  monotonic mutation generation because a crash can occur after a handoff but
  before its queue acknowledgement is durably committed. The later Search Node
  adapter must pass that generation to OpenSearch external-version writes.

## Durability and recovery

The manifest enables SQLite WAL mode, full synchronous writes, foreign keys,
and restrictive local permissions. Discovery, stat, extraction, indexing,
delete, and ACL-refresh work uses unique idempotency keys and fenced leases.
Expired leases are reclaimable after a process or host restart; a stale worker
cannot acknowledge or renew a newer lease. Long-running adapters can renew
their exact lease generation. Failures back off and eventually enter the `dead`
state for operator review.

Each full reconciliation owns a durable, renewable per-source lease. Observe,
finish, failure, and tombstone mutations are compare-and-swap fenced by its
token, so an expired process cannot finish over a newer run. Files absent from
the successful leased run are tombstoned and queued for deletion only after the
source walk finishes without an exception. Partial walks, SMB disconnects,
queue backpressure, notification overflow, and cursor failure all retain live
files and set `reconciliation_required`.

Stable provider file IDs preserve identity across renames. When unavailable, a
normalized path hash is used and rename detection is conservative. Path reuse
keeps the displaced identity until a successful reconciliation can tombstone
it. Extraction reads are streamed in bounded chunks, capped by `max_file_size`,
and fenced against the queued manifest stat plus pre/post source stats. Only
stable bytes get a SHA-256 fingerprint and generation-qualified handoff.

Cursor advancement and its STAT enqueue commit in one SQLite transaction.
Cursorless notifications receive a durable per-source sequence, so repeated
changes to the same path re-arm work. Queue coalescing never resets leased,
backed-off, or dead work; long handlers automatically renew their exact lease
and are cancelled if renewal ownership is lost.

## Scheduling and resource controls

Every source has its own five-field reconciliation schedule plus these budgets:

- `max_workers` bounds concurrent crawl work.
- `max_open_handles` bounds simultaneous source handles.
- `read_bytes_per_second` applies cooperative read throttling.
- `max_pending_jobs` stops an authoritative walk before unsafe tombstones when
  downstream queues are saturated.
- `max_file_size` rejects oversized documents before extraction and also caps
  streamed bytes when a provider reports an incorrect size.

`enqueue_due_reconciliations()` persists discovery work. Operators can pause a
source, and `set_interactive_priority()` yields crawl reads while interactive
local search is active. One scheduled reconciliation may be outstanding per
source. Worker budgets cover every queue stage, and handle budgets cover source
walk/stat/read I/O. Change streams never replace scheduled reconciliation.

## Operator status and response

Call `pipeline.status()` to inspect the default-off flag, per-source pause and
reconciliation state, last successful walk, last error, queue counts by
stage/state, and process counters. Alert on:

- any `*.dead` queue count;
- repeated `reconciliations_failed` or `hint_stream_failures` increments;
- a source remaining `reconciliation_required` past its schedule;
- sustained ready/retry growth or `reconciliations_backpressured` increments.

After repairing credentials, connectivity, adapter availability, or downstream
capacity, run a full reconciliation before retrying delete work. Do not clear
tombstones or edit queue rows manually. Preserve the database and its `-wal`
and `-shm` files together when collecting restart diagnostics.

## Rollout checklist

1. Keep `enabled=False` until all local adapters and monitoring are installed.
2. Start with one non-critical source and conservative handle/read budgets.
3. Exercise disconnect, overflow, restart, path-reuse, and partial-walk tests
   against that provider.
4. Verify a full reconciliation completes and queues drain without dead letters.
5. Enable additional source roots individually; matter enrichment may be added
   independently.
