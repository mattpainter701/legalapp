# Firm Memory file-share search: PoC architecture

> **Status — design target, not shipped:** the Tika/OCR/OpenSearch/ACL pipeline
> below is the system the customer PoC must build and test. This repository
> currently contains only a default-off, local SQLite full-text control index
> and evaluation tools. It does not contain an installable 4 TB search service.

## Decision in one paragraph

Treat the existing SMB agent as the source-of-truth scanner and access layer, and add a durable local ingestion pipeline plus OpenSearch on local SSD/NVMe. The first implementation should index a stratified 50–200 GB slice of the share, not claim that an embedded index proves 4 TB readiness. The source server may remain DDR4/HDD: reads, extraction, OCR, and search-index writes must be separately throttled so indexing does not turn the file server into an outage. The checked-in SQLite FTS5 component is a bounded local full-text control implementation; it is not the scale target.

## Scope and non-goals

The PoC answers four questions: (1) can we obtain useful text from the firm’s real, messy formats; (2) can we keep results current as files change; (3) can every result be security-trimmed; and (4) can the measured throughput and storage be extrapolated to 4 TB? It does not promise zero-downtime production search, perfect OCR, semantic/vector retrieval, legal hold, or replacement of the file server. Unsupported, encrypted, corrupt, or timed-out files must be visible as classified failures rather than silently omitted.

The repository's SQLite evaluator is operator-only and remains inside the
customer boundary. It is useful for control measurements, but it is not the
ACL-enforced end-user query service described below.

## Planned logical flow

```text
SMB share -> SMB agent (crawl + change hints + planned ACL capture)
              -> durable manifest / queue (local transactional store)
              -> bounded workers
                 -> Tika Pipes (forked, isolated extraction)
                 -> OCRmyPDF/Tesseract (async, image-only PDFs)
              -> canonical document/page/chunk records
              -> OpenSearch bulk index on local SSD/NVMe
              -> query ACL filter -> snippets/provenance -> UNC/open/download
```

The current scanner records a normalized UNC path, share identifier, size,
last-write time, and a SHA-256 fingerprint of only the first 4 KiB. The PoC
manifest must add stable file identity where available and a full content hash
when policy and source-I/O budgets permit. A changed manifest row creates a new
work item; workers are idempotent on `(file identity, content fingerprint,
pipeline version)`. Keep the source bytes on SMB. Store only extracted text,
bounded metadata, OCR status, ACL snapshot, and provenance locally unless a
separate retention decision authorizes derivatives.

## Scanning and change detection

Run an initial breadth-first or directory-partitioned crawl with a small
concurrency limit (start at 2–4 outstanding file reads). This is a planned
scanner/source-I/O budget, distinct from the checked-in control index's
default of one extraction worker. The SMB change-notification mechanism is
useful for low-latency updates, but it is not a durable journal: reconnects,
renames, dropped notifications, and watcher buffer overflow require
reconciliation. Persist a cursor and run a full manifest reconciliation at
least daily during the PoC. On a notification, re-stat the path before
enqueueing; coalesce bursts and re-check size/mtime after reading to avoid
indexing a file still being written.

Use a service account with the minimum share/NTFS read rights needed. The
current scanner does not capture native Windows ACLs. The PoC must add security
descriptor capture (or a stable ACL digest plus the ACE records needed for
evaluation) while the agent has access. Do not infer permission from the
indexer’s own ability to read a file.

## Durable manifest and queue

The manifest is the operational control plane, not merely a database of search rows. Suggested fields include source path, file ID, fingerprint, size, mtime, ACL digest, detected MIME, extraction/OCR/index pipeline versions, state, attempt count, next retry time, error class, and timestamps. States should include `discovered`, `queued`, `reading`, `extracted`, `ocr_queued`, `ready`, `indexed`, `skipped`, `failed_retryable`, and `failed_terminal`.

Use leases with expiry, exponential backoff, a dead-letter view, and a resumable queue. A successful OpenSearch acknowledgement is the commit point for `indexed`; failed bulk items remain retryable. Never delete a previous good index document until its replacement is acknowledged. Tombstone missing source files after reconciliation, retaining an audit record sufficient to explain why a result disappeared.

## Extraction and OCR isolation

Use Apache Tika Pipes/forked workers rather than calling arbitrary parsers in the API process. Tika supports broad metadata/text extraction and current documentation provides limits for embedded depth/count, output, unpacked bytes, zip-bomb behavior, and total/progress timeouts. Configure explicit per-file byte, character, depth, count, and wall-clock budgets; record which limit fired. Forked/Pipes operation matters because in-process code cannot reliably kill a hung parser thread.

Send only image-only or below-threshold-text PDFs to an asynchronous OCR queue. OCRmyPDF/Tesseract should write a derivative or extracted text into a temporary local SSD directory, then be atomically promoted to the document record. Set language packs deliberately, use page rotation/deskew where valuable, and enforce a per-file Tesseract timeout. OCR failures must not block ordinary text extraction or the rest of the queue. Do not OCR every PDF by default: it will dominate throughput on an HDD-backed source.

## Canonical records and provenance

Index one logical document with fields for path, filename, extension, MIME, size, dates, source identity, ACL principals/digest, extraction status, language, and a normalized full-text field. For long documents, also index page/chunk records (for example 1–4k tokens) carrying `document_id`, page number, character/token offsets, chunk sequence, and a short stored excerpt. Every hit must map back to the source UNC path and, where applicable, page number and extraction method (`native`, `embedded`, or `ocr`). Keep the original text/derivative outside the search response unless the caller passes the same authorization check.

Use OpenSearch fielded queries, exact metadata filters, phrase/Boolean search, and BM25 first. Keep vector/semantic retrieval out of the critical PoC path until extraction coverage, ACL trimming, and baseline keyword recall are proven.

## OpenSearch placement and sizing

Run a single-node PoC with its active data and translog on local SSD/NVMe—not
on the SMB share. Put snapshots in a separately protected repository/volume so
a data-volume failure does not destroy the recovery copy. Start with one
primary shard and zero replicas to measure the lower-bound footprint; add
replicas only after capacity and recovery measurements. Bulk in bounded
batches, monitor JVM heap, disk watermarks, merge time, refresh latency, and
rejected requests. If forced to use spinning media for index data, set Lucene
merge concurrency to one and accept materially lower indexing throughput.
Reserve substantial free space for segment merges, translog, and reindexing;
do not size the disk to the final index bytes alone.

An initial sizing model is:

```text
source bytes = S
eligible bytes = S × inclusion rate
index bytes ≈ extracted text bytes × measured index multiplier
working disk >= index + translog + merge headroom + snapshot + reindex allowance
```

Measure the multiplier on the representative sample; do not assume it is constant across scanned PDFs, Office files, duplicates, and archives. Extrapolate by file class and retain a confidence interval. The 4 TB target may require multiple data nodes or a larger dedicated SSD tier even if the source remains on the old server.

## ACL security trimming

At query time, resolve the caller’s authenticated user and group SIDs, then add an authorization filter that requires an allow ACE/principal match and honors explicit denies according to the Windows access model. Prefer a conservative deny-on-unknown policy. Apply the same check before returning snippets, page text, previews, or an open/download link; search ranking is not an authorization boundary. Cache group expansion briefly, but invalidate/retest when identity data changes. Test inherited ACLs, nested groups, explicit deny ACEs, renamed paths, inaccessible parents, and users who can read a directory but not a child file.

## Staged rollout

1. **Pilot (50 GB):** choose representative directories and file classes; establish extraction, ACL, and latency baselines.
2. **Stress slice (200 GB):** include worst-case archives, scanned material, duplicates, deep trees, and permission edge cases; run restart and disconnect tests.
3. **Scale rehearsal:** extrapolate to 4 TB, then process a larger slice only if source I/O, local disk, and queue lag remain within the agreed budgets.
4. **Production decision:** document hardware changes, backup/restore, monitoring, retention, and a rollback that disables search without modifying the share.

## Benchmark gates

Before the first customer file is read, the customer and senior engineer must
approve the corpus strata, judged questions, load profile, and thresholds.
These are the provisional go/no-go floors; changing one after results are seen
must be recorded as a test-plan revision:

The PoC is credible only if it demonstrates:

- **Coverage:** 100% of discovered files are accounted for and at least 98% reach an explicit terminal state; in the scale pipeline, at least 95% of eligible text-native files and bytes reach `indexed`; failures are classified and replayable.
- **Extraction:** report per-extension text yield, native-vs-OCR yield, and timeout/limit rates. No extension is called supported if fewer than 90% of its eligible sample produces useful text and provenance.
- **Retrieval quality:** on at least 50 customer-judged questions, overall recall@10 is at least 0.85, no critical stratum is below 0.75, and correct-page rate is at least 0.80 for formats with reliable page boundaries.
- **Freshness:** under an intact watcher, create/modify/rename/delete changes converge at p95 within 15 minutes; an administrative share disablement or agent revocation makes results ineligible at p95 within 60 seconds. Reconnect and missed-event reconciliation produce no silent stale rows.
- **Search:** after warm-up, p95 is at most 2 seconds and p99 at most 5 seconds at five concurrent users for the agreed query mix. Exact identifiers and citations must not depend on semantic retrieval.
- **Security:** zero unauthorized result, snippet, preview, or download across the ACL matrix; unknown ACLs fail closed.
- **Resilience:** worker/API restart, OpenSearch restart, source disconnect, duplicate delivery, and partial bulk failure recover without lost or duplicated live documents; queues resume within 10 minutes after service recovery.
- **Capacity and source impact:** keep at least 30% local index-disk free, keep incremental queue lag under 15 minutes after the initial crawl, and project text-native 4 TB completion within the customer-approved window. Throttle when added source-server read latency exceeds 20% or indexing consumes more than 25% of measured spare SMB throughput. OCR receives a separate completion forecast and may not block text-native availability.

### Reproducible evaluator

The agent package includes a local-only query runner and engine-neutral
evaluator. The control runner opens an existing index read-only. Keep query
text, share paths, judgments, and result records on the customer-controlled
benchmark host:

```powershell
python -m clarity_agent.poc_query_runner `
  D:\LawHandIndex\search-index.db queries.jsonl --output results.jsonl
```

```powershell
python -m clarity_agent.poc_benchmark judgments.jsonl results.jsonl `
  --coverage coverage.jsonl --output report.json
```

`queries.jsonl` uses an opaque `q`-number, UUID, or 64-character hexadecimal
ID. It contains sensitive query and scope material and must never be exported:

```json
{"query_id":"q001","query":"negligent spoliation sanctions","share_id":"firm","share_path":"\\\\server\\Firm","folder":"Closed Matters","limit":20}
```

`judgments.jsonl` identifies relevant documents/pages without requiring query
text in the evaluator:

```json
{"query_id":"q001","stratum":"born_digital_pdf","relevant":[{"doc_id":"<runner SHA-256 ID>","page":12}]}
```

`results.jsonl` records ranked document/page identifiers and end-to-end
latency. `coverage.jsonl` records one document identifier and terminal status
per discovered file. The report contains aggregate and per-stratum recall,
precision, nDCG, MRR, correct-page rate, latency percentiles, coverage rates,
and uncovered relevant-document counts. Coverage is optional; when it is not
supplied, uncovered-document counts are reported as unknown rather than zero.
The runner intentionally emits no query text, path, filename, excerpt, or
document content. Its deterministic SHA-256 document identifiers are
pseudonyms, not anonymity, so result files stay inside the same boundary.

## Explicit limitations and risks

An old HDD file server will constrain the initial crawl and OCR input; adding CPU or RAM to the index host does not remove that bottleneck. A single OpenSearch node is not highly available. ACL snapshots can become stale between crawl and query, so high-risk deployments need a policy for rechecking access at open time. Tika/OCR coverage is necessarily imperfect; encrypted files, malformed containers, handwritten text, tables, and unusual legacy formats need visible failure states. Content hashes and OCR derivatives may themselves be sensitive data and require encryption, access controls, retention, and backup policy. SQLite FTS5 remains useful for a small control index, but its application-managed consistency and single-process operating model do not establish readiness for a 4 TB multi-user service.

## Official references

- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [Apache Tika documentation](https://tika.apache.org/docs/4.0.x/index.html), [Pipes timeout model](https://tika.apache.org/docs/4.0.x/pipes/timeouts.html), and [security model](https://tika.apache.org/security-model.html)
- [OCRmyPDF advanced operation](https://ocrmypdf.readthedocs.io/en/latest/advanced.html)
- [OpenSearch system settings](https://docs.opensearch.org/latest/install-and-configure/configuring-opensearch/configuration-system/), [index settings](https://docs.opensearch.org/latest/install-and-configure/configuring-opensearch/index-settings/), and [cluster disk watermarks](https://docs.opensearch.org/latest/install-and-configure/configuring-opensearch/cluster-settings/)
- [Microsoft SMB directory change notification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fasod/271a36e8-c94b-4527-8735-e884f5504cd9)
- [Microsoft file security and access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights) and [DACL/ACE behavior](https://learn.microsoft.com/en-us/windows/win32/secauthz/dacls-and-aces)
