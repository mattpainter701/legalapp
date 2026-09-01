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

Release 0.15.3 adds a bounded `local_search` relay for the control index. It
is matter-scoped at the API, uses the agent's existing outbound polling
connection, and is exposed through the authenticated REST/portal surface,
Chat structured sources, and user-bound Workspace MCP
`search_firm_memory`. Query text is short-lived relay material: it is not
application-logged, persisted, or included in search audit records. Only
correlation IDs, counts, latency, index state, and partial/degraded status are
operationally retained. Search remains limited to the indexed corpus and
does not imply that unindexed or unsupported files were searched.

Portal results use an opaque same-origin file deep link. Opening the result
rechecks the user's matter entitlement and then offers **Copy UNC** for the
canonical `\\server\\share\\...` path. The browser is never given a raw
`file://` or `smb://` hyperlink, and the portal does not proxy arbitrary SMB
bytes. The user's workstation must already have access to the share.

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

### Native identity and Windows ACL release gate

Native trimming is now implemented behind two independent, default-off gates:
`FIRM_MEMORY_NATIVE_AUTHZ_ENABLED` and
`FIRM_MEMORY_ACL_COVERAGE_HEALTHY`. Enabling only one does not authorize a
release. A tenant administrator must first establish a healthy immutable
AD/Entra mapping for every active user; each mapping records the directory
tenant/object identity, primary SID, complete nested effective group SID set,
resolution version, expiry, and an explicit `pending`, `healthy`, `stale`,
`error`, or `revoked` state. Partial group expansion is an error, because an
omitted deny group is unsafe.

For each search the SaaS resolves—not the browser—the allowed source ids and
optional filters, then signs an Ed25519 ticket bound to tenant, user, customer
node, sources, identity version, nonce, issue time, and an expiry of at most
five minutes. The agent rejects forged, expired, replayed, cross-tenant,
cross-node, and cross-source tickets. Tickets and principal SIDs are relay-only
authorization material and are not application logged.

The customer node stores normalized read-relevant allow/deny SIDs, inheritance
markers, capture time, and a descriptor version beside the local derived index.
Old index rows begin `unknown`; unchanged files whose ACL snapshot ages out are
queued for refresh. Unknown, pending, unavailable, error, and stale ACLs are
deny-all. A matching explicit deny wins over every allow, including inherited
allows. Filenames and snippets are emitted only after that local decision, and
deep-link/detail and content tasks request a new one-use ticket and a new local
ACL decision before release.

Operators use `GET /api/v1/smb/native-authz/status`, the privacy-safe identity
diagnostic list, and `lawhand-agent status` (`Native ACLs`) to establish
coverage. The private signing key belongs only in the SaaS secret store; the
agent receives only the public key in
`CLARITY_SEARCH_IDENTITY_PUBLIC_KEY`. Revoking an identity blocks new tickets;
already minted tickets expire within the configured short TTL, while consumed
nonces remain in the agent's negative replay cache through expiry.

The local OpenSearch SearchNode lifecycle and its mandatory bounded
`acl_tokens` query filter are delivered by FM-03. After FM-03 lands, the
verified SID set from this ticket is the input to those local tokens; an empty
token set remains deny-all. Neither native descriptors, ACL tokens, nor corpus
content are sent to the SaaS.

Cloud-source provider/matter/ethical-wall trimming is supplied by FM-01's
`NativeSourceAuthorizer` and remains a prerequisite for enabling this gate.
FM-06 deliberately does not create a competing provider policy path; after
FM-01 lands, every cloud adapter must invoke that authorizer before fetching or
returning content, metadata, matter names, or association counts.

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

For a Tailscale-connected validation host, collect the local rotating
`agent.log`, `lawhand-agent status`, read-only SQLite index statistics, portal
search/status responses, Workspace MCP audit records, and the local JSONL
evaluator artifacts. Tailscale is optional admin reachability only; it is not
the telemetry layer, and the agent still initiates outbound HTTPS polling.

## Rollback and rebuild

Pause the worker and switch to the shipped metadata-first search path. Preserve
audit records. Rebuild by validating the configured root, creating a new
protected index, completing a scan, checking counts and canary queries, and
atomically switching the active index. Rollback or rebuild never deletes the
source case files.
