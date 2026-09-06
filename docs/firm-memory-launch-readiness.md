# Firm Memory launch boundary

The OpenSearch portal path is an opt-in pilot, not a measured capacity claim.
The normal scanner feeds the durable local extraction manifest; isolated parser
children produce text; OpenSearch is the only full-text sink for this path.
SQLite retains status, paths, ACL evidence and durable mutation counters, with
an empty FTS table. The separate experimental CrawlPipeline/OCR queue is not
the daemon's scheduler. No automatic whole-archive onboarding occurs.

## Provision before enabling

- Install matching `clarity-agent` and `lawhand-search-node` distributions on the
  agent host. Set `SEARCH_NODE_ENABLED=true` and configure dedicated absolute
  local `SEARCH_NODE_STAGING_ROOT` and `SEARCH_NODE_TEMP_ROOT` directories.
- For a packaged/frozen agent, provision a reviewed Python 3.11+ environment,
  install the matching `lawhand-search-node` wheel in it, and set the absolute
  `SEARCH_NODE_PYTHON_EXECUTABLE` path. The agent executable is not a Python
  interpreter. The MSI does not itself provision that external environment.
- Apply the search-node sandbox runbook and set `SEARCH_NODE_SANDBOX_VERIFIED`
  only after verification. Preflight also checks runtime imports and actual
  process containment; failed Windows Job Object assignment aborts parsing.
- Configure TLS/authenticated loopback OpenSearch and its packaged disk
  watermarks, native authorization, identity verification key and gateway token.
  Set `LAWHAND_SEARCH_NODE_ENABLED=true`, `CLARITY_NATIVE_AUTHZ_ENABLED=true`,
  and keep `CLARITY_LOCAL_INDEX_ENABLED=false`. Run `lawhand-agent search-preflight` before activation. Both SaaS and agent native
  identity configuration must agree. Defaults remain disabled.
- Retain the local manifest and its adjacent `.generations.db` together with
  OpenSearch backups. A reset/restore must use a fresh OpenSearch index prefix
  and rebuild from source; never restore an older generation database against
  a newer engine index.

## Authorization, currentness and data flow

Native authorization failures, absent Redis, missing identity/signing setup and
offline agents return no cloud filename/snippet fallback. Non-native policies
retain explicitly partial metadata search. Portal queries carry signed identity
tickets. OpenSearch applies allow, explicit deny and authorized folder filters;
the agent then checks manifest version/readiness and re-reads each result's DACL
before returning bounded snippets. Preview/open uses the same live DACL check.
Deleted/pending/failed/stale-version entries cannot be released even if old text
remains in OpenSearch after an engine outage. Engine deletion failure is reported
as an incomplete scan. A durable deletion outbox retries at startup and on
subsequent successful scans, retaining generation fencing against newer text.

The path identity includes share and canonical Windows path. Moves are a
delete/new identity in this scanner, not a promise of stable file IDs across
moves. Durable engine generation counters do not depend on wall-clock order.
Full reconciliation and ACL refresh still follow the configured scan interval;
query-time ACL checks prevent cached grants from surviving a detected live DENY.
Returned text reflects the last completed scan, not a transactional file-server
snapshot. A file edit between scans may require the next scan to refresh text.

Source bytes are staged transiently on the agent host and removed after parsing.
The local engine retains full text; SaaS receives existing scan metadata and
bounded authorized result snippets. Optional assistant retrieval can separately
transfer selected text according to its configured policy. This is not a claim
that no data leaves the office. Only authorized matter-bound shares participate;
unbound legacy archives need a reviewed collection/access model first.

## Acceptance still required

Use synthetic files first, then a customer-approved representative sample. Test
native allow/deny/inherited group changes, missing identity, offline storage,
restart during extraction, edits/moves/deletes and restore. Record first-index
time, source CPU/I/O, query latency/concurrency, failed/pending files and disk
growth. Do not extrapolate from engine unit fixtures to a multi-terabyte archive.

The scanner's existing format and input-size bounds remain in effect. Files
requiring OCR report `ocr_pending` coverage and are not silently described as
fully indexed. Available native text remains searchable with partial coverage;
image-only documents remain unavailable until OCR support is configured. This adapter does not run the separate OCR pool or promise Tika
availability. Review those failures before claiming corpus completeness. Mobile
search does not supply a phone document viewer; Windows opening requires the
existing file opener and network/VPN access.

No production activation or customer indexing is part of this change.
