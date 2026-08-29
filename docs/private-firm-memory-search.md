# Private Firm-Memory Search: Security and Operations

This document defines the security boundary for searching historic case files
through the LawHand file-share agent. It is the canonical companion to
`docs/smb-agent-setup.md`.

## Scope and shipped behavior

The shipped SMB path indexes tenant-bound metadata and a bounded opening
snippet in PostgreSQL. It already fetches bounded full text from the
on-premises agent only after an authorized result is selected. The new
default-off local full-text control index is derived data; it is not the
authoritative file store and adds no local-index search-result or excerpt
relay to the SaaS.

The bounded control implementation uses SQLite FTS5/BM25 to validate local
extraction, scope enforcement, ranking, and evaluation tooling. The planned
customer PoC scale target uses OpenSearch on local SSD/NVMe with a durable
queue, process-isolated extraction, and asynchronous OCR while the source
share may remain on HDD. That scale pipeline is a design requirement, not a
currently installable component. Embeddings are
optional future work. Neither implementation may claim comprehensive coverage,
semantic understanding, OCR coverage, native Windows ACL preservation, or
replacement of Westlaw or other licensed research tools without measured
evidence for that capability.

## Trust boundaries

```text
SMB file server -> outbound-only agent -> authenticated HTTPS -> LawHand API
       source files       local derived index       separately authorized results
```

The agent may read only configured share roots with the configured SMB
credential. The SaaS cannot open arbitrary sockets into the customer network.
The local index, ledger, retry queue, and OCR derivatives are sensitive
derived data and must live in the protected agent data directory on local
storage. Index initialization must fail closed if that directory or its index
file cannot be ACL-restricted; UNC/network index paths are not accepted.
Building that index does not itself authorize filenames, paths, pages,
excerpts, or other result content to leave the customer environment.

## Authorization invariants

These invariants apply to any future SaaS result relay. The current local
control-index evaluator is an operator-only tool inside the customer boundary;
it is not an end-user authorization surface and must not be exposed as one.

- Every row and every query is bound to one tenant.
- A matter search requires a tenant-valid matter, an authorized user, and a
  matching share/folder binding.
- A result and content task bind tenant, matter, share, file, canonical path,
  and agent. Identifier substitution is rejected.
- Canonical paths are normalized and constrained to the configured UNC root;
  traversal, reparse-point escapes, alternate data streams, and sibling-prefix
  matches are rejected.
- Share disablement, agent revocation, file deletion, and matter unbinding
  invalidate search eligibility and pending fetches.
- Database RLS is defense in depth, not a substitute for application checks.

### Native Windows ACL limitation

The configured SMB service account, not the LawHand user, normally determines
what the agent can read. Native per-user Windows ACLs are not mirrored by the
first slice. Firms must use separate least-privilege shares/accounts for
different security boundaries or explicitly accept the broader visibility.
Do not describe this mode as native ACL preservation.

## Untrusted content and parser safety

Historic documents are treated as hostile input. Text such as “ignore prior
instructions” is evidence quoted from a document, never an instruction to the
assistant. Delimit retrieved passages, escape UI output, and require a
separate user-confirmed action for every mutation or external tool call.

Parsers and OCR workers require source-byte, page, pixel, nesting, timeout,
memory, concurrency, and queue limits. Macros, scripts, embedded objects,
attachments, and active PDF actions are not executed. Unsupported or failed
files receive explicit status and cannot be presented as proof that no match
exists.

The SQLite control manifest currently emits `pending`, `running`, `ready`,
`unsupported`, and `error`, where `error` is its terminal failure class. The
evaluator also normalizes richer future pipeline states such as `partial`,
`timed_out`, and planned `failed_terminal`. The existing scanner's content value
is only a SHA-256 fingerprint of the first 4 KiB, not a full-file integrity
hash. The scale PoC must record its fingerprint/hash method and pipeline
version explicitly.

The opt-in native Python extractor is limited to controlled validation data;
its byte cap and worker bound are not a process sandbox. Real customer PoC
files require the forked Tika Pipes extraction boundary described in
`docs/firm-memory-poc-architecture.md`.

## Content rights and proprietary material

Firm files may contain client-confidential material and licensed third-party
content. Keep customer-owned files, licensed secondary sources, and public
authority in separate source classes with provenance and retention metadata.
Do not scrape or bulk-ingest Westlaw/Thomson Reuters, Lexis, Wright & Miller,
or similar material without a written license/API agreement. Do not place
restricted text in a cross-tenant corpus or model-training dataset. Responses
must disclose which source classes were searched and whether licensed
secondary sources were unavailable.

## Retention, audit, and operations

The index is rebuildable derived data. Protect its directory and backups with
the customer's encryption and retention policy. Deletion and legal hold must
cover index rows, OCR derivatives, excerpts, caches, retry payloads, and
backups. Audit actor, tenant, matter, share, file identity, result count, and
outcome without logging credentials or unrestricted text.

Operators should monitor discovered/indexed/failed/deleted counts, queue depth,
index size, last successful scan, parser errors, P50/P95 query latency,
recall@10, correct-page rate, and revocation-to-ineligibility time.

## Rollback and rebuild

Pause the worker and switch to the shipped metadata-first search path. Preserve
audit records. Rebuild by validating the configured root, creating a new
protected index, completing a scan, checking counts and canary queries, and
atomically switching the active index. Rollback or rebuild never deletes the
source case files.
