# On-prem Search Node operations

## Boundary and rollout state

The Search Node is a customer-infrastructure service. Crawling, extraction
output, chunk text, OpenSearch indexes, lexical ranking, highlighting, ACL
tokens, and query execution remain on the customer network. LawHand SaaS may
authenticate users, authorize matter/share scopes, and relay a bounded query
through the agent's existing outbound channel; it must not receive the corpus.

`search_node_enabled` defaults to `false`. The existing SQLite FTS feature is a
PoC and cannot serve at the same time as the production Search Node. SQLite is
used only for manifest and job/lease control state. This PR defines crawler,
extractor, and ACL-filter interfaces but does not add Tika/OCR, USN crawling,
complete Windows ACL trimming, workstation opening, or vector search.

## Topology and authentication

Run OpenSearch on the same host as the agent or through a host-local transport.
Its HTTP and transport listeners must bind to loopback only. Do not publish
ports 9200 or 9600 in a container, firewall, reverse proxy, or load balancer.
The sample `agent/packaging/search-node/opensearch.yml` keeps both OpenSearch
listeners on `127.0.0.1`, enables OpenSearch security, and requires a
non-default account. Performance Analyzer is installed by default in OpenSearch
2.x and later, has a separate port 9600 service, and has no request
authentication. Uninstall/disable it unless required; if retained, install the
packaged `performance-analyzer.properties`, which binds it to `127.0.0.1`.

The agent exposes a second loopback-only gateway (default `127.0.0.1:8765`).
It requires a random secret of at least 32 bytes in every request:

```text
Authorization: Bearer <LAWHAND_SEARCH_GATEWAY_TOKEN>
```

Generate independent OpenSearch and gateway secrets with the operating-system
credential tooling. Configure them through protected service environment
variables or save them through the agent configuration path, which encrypts
them using the existing machine-local key. Never put secrets in command logs.

## Installation on Windows and Linux

The MSI and Linux tarball include versioned samples and this runbook in their
`search-node` directory. They are reference files under the install tree and
never overwrite operator-owned service or OpenSearch configuration on upgrade.

1. Install a supported single-node OpenSearch distribution as its own Windows
   service or systemd unit. Run it as a dedicated account, not LocalSystem or
   root. Copy `lawhand.options` into the distribution's `jvm.options.d`
   directory; never replace the vendor `jvm.options`. Set `OPENSEARCH_TMPDIR`
   and the data/log/temp paths to dedicated local SSD/NVMe storage. Install the
   packaged OpenSearch and Performance Analyzer settings as described above.
2. Issue a node/server certificate trusted by the agent host and a dedicated
   least-privilege `lawhand_agent` OpenSearch user. Keep security enabled.
   If the certificate is not trusted by the operating system, set
   `opensearch_ca_path` to an absolute local Windows or Linux CA bundle path.
3. Confirm OpenSearch is reachable only through loopback and that no firewall
   rule exposes it. Confirm disk watermarks and snapshot repository access.
4. Copy the settings from `config.example.toml`, set the three secrets, leave
   `search_node_enabled=false`, and restart the agent to verify the existing
   file-share functions are unchanged.
5. Run the preflight health and snapshot checks, then opt in and restart. The
   agent fails startup rather than silently falling back to SQLite full text,
   and logs every reason the preflight rejected the node. The most common one
   is a node that never received the packaged `opensearch.yml` and still has
   OpenSearch's stock 85% low watermark, which reads as
   `disk watermarks do not match the packaged opensearch.yml: low='85%'
   (expected '80%')`. Because this failure stops the whole agent — including
   scanning and content fetch — verify health before opting in.

Agent upgrades preserve the config, control database, OpenSearch data path,
and index aliases. The existing MSI permanent data component and Linux
`/etc/lawhand-agent` directory remain the configuration owners; OpenSearch data
must live outside the agent binary/install directory.

## Capacity and resource limits

- Begin heap sizing at half of dedicated RAM, capped below 32 GB, with equal
  initial and maximum heap. Validate against the representative corpus.
- Reserve at least 20% disk headroom. Default watermarks are low 80%, high 90%,
  and flood-stage 95%. Flood stage may make indexes read-only; add capacity and
  clear the block only after disk returns below the safe threshold.
- Default request bounds are 100 results, 10,000 offset, 1,000 query
  characters, and a 10-second engine request timeout. The gateway body cap is
  64 KiB.
- Batch and per-document bounds are separate, because one document is one atomic
  envelope and cannot be split across requests. `search_max_bulk_documents` (500)
  and `search_max_bulk_mb` (8) bound one bulk request; a batch over either is
  split into more requests, never rejected. `search_max_document_chunks` (5,000)
  and `search_max_document_mb` (20) bound a single document, and a document over
  either fails on its own without affecting the rest of its batch. Keep
  `search_max_document_mb` at or above the extraction pool's
  `SEARCH_NODE_MAX_OUTPUT_MIB`, or the index will refuse documents the extractor
  is allowed to produce.
- Run the acceptance queries with the packaged runner. The installers place it
  and its fixtures in the install tree's `search-node/benchmarks` directory:

  ```text
  export LAWHAND_BENCHMARK_OPENSEARCH_PASSWORD=...
  python run_benchmark.py --url https://127.0.0.1:9200 --username lawhand_agent --ca-path <local CA bundle>
  ```

  It loads the synthetic corpus into its own disposable index generation, runs
  the phrase, Boolean/field-filter, ACL-deny, and ACL-allow checks, deletes that
  generation, and exits non-zero if any check fails. It never reads or writes
  the aliases the agent serves from, so it is safe to run against the live node.
  The password comes from the environment so it never reaches a shell history.
  These fixtures prove the engine, mapping, analyzer, and ACL filter — not that
  the customer corpus is intact. Add a representative customer-owned corpus for
  throughput and p95 latency without copying that corpus off-site.

## Index versions, rebuilds, and upgrades

Physical indexes include the schema version and a unique generation. Clients
read and write through version-specific aliases. The current schema version is
2; it added `deny_acl_tokens`. An index created at version 1 fails the startup
mapping check and must be rebuilt, which costs nothing today because no crawler
populates the index yet. Startup validates the active
mapping `_meta.lawhand_schema_version`; a newer incompatible schema fails
closed. A rebuild creates a fresh generation, bulk loads and refreshes it, then
atomically swaps read/write aliases. If the swap response is lost, the agent
re-reads both aliases before cleanup and never deletes a possibly active
generation. Each source document and all of its nested chunks are one atomic
OpenSearch document. Incremental replacements first write an even-version
tombstone, then the complete envelope at the next odd version, so ACL revocation
fails closed without exposing a partial chunk set. Strict external versions make
a delayed older process unable to delete or overwrite newer content. Rebuilds and
incremental mutations also use one exclusive local barrier for ordinary cutover.
Every control-queue claim, including retry or expired-lease reclamation,
increments a durable mutation generation. Workers must copy the claimed job's
generation into every `DocumentChunk` for an upsert or into `DocumentMutation`
for a delete.
Rebuild input must be ordered lexicographically by `document_id` so version and
generation consistency can be validated with bounded memory, and a single
document is rejected as soon as it exceeds the configured chunk bound. Before
cutover, the engine uses the OpenSearch write-block API to drain and block the
old physical index under an owner-token/OCC lease in a dedicated coordination
index. It revalidates the alias target, refreshes and verifies every source
shard, then reindexes that stable source into the candidate with preserved
external versions. The replay runs as a bounded asynchronous OpenSearch task
while the write block remains held. The OCC lease durably records the source,
candidate, phase, source-block state, replay task ID, and last verified alias.
The same distributed lease serializes first-generation index/alias creation;
a rebuild first establishes that canonical generation and then replays any
mutations accepted there, rather than treating an initially empty alias lookup
as a source-free cutover. The recorded phase is always the last successfully
verified transition and is never replaced merely to label an error; lease
presence itself is the quarantine signal.
A definite failure clears the block through the index-settings API and verifies
it; an uncertain or timed-out task retains the block, candidate, and lease as an
explicit operator quarantine. Search fails closed whenever either the lease is
present or the active index is write-blocked, and health is degraded for either
condition. Mutations that arrived during
the rebuild therefore win over stale rebuild input; later writes retry after the
atomic alias swap. A required CI contract exercises the alias,
nested-chunk query, and generation wire behavior over authenticated TLS against
a pinned real OpenSearch node.

Before an agent/OpenSearch upgrade:

1. Record health, active physical index, agent and OpenSearch versions.
2. Create and verify a named filesystem/S3-compatible snapshot repository that
   remains inside customer-controlled infrastructure.
3. Take a snapshot and wait for success; retain the prior installer/package.
4. Upgrade OpenSearch only across its supported version path, restart it, then
   upgrade the agent with `search_node_enabled=false` if a schema rebuild is
   required.
5. Rebuild, validate benchmark queries and ACL denial, atomically cut aliases,
   then enable the gateway. Keep the old index until the observation window
   ends.

## Health, snapshot, and recovery hooks

Authenticated `GET /v1/health` reports engine state, active index, schema,
capabilities, result limits, and configured disk watermarks. It never returns
paths, queries, snippets, ACL tokens, or document counts partitioned by client.

The engine exposes asynchronous snapshot create and restore hooks. Restore
validates exact successful snapshot metadata, excludes aliases/global state,
starts the scoped restore asynchronously, and holds the mutation barrier while
polling every expected shard to `DONE`; a bounded operation timeout fails closed.
Recovery completion must match the requested repository and snapshot plus the
exact primary-shard identity/count from successful snapshot metadata; an older
completed recovery cannot satisfy the poll.
Repository registration and credentials are operator actions because storage
topology is customer-specific. After restore, validate schema and corpus counts,
run every benchmark query (especially ACL-deny), and perform an alias
rebuild/cutover.
If OpenSearch is unavailable, the gateway reports unavailable and the SaaS
relay must return a bounded degraded result; it must not upload documents or
route the query to a SaaS search engine.

### Rebuild quarantine recovery

There is no time-to-live or automatic force-release for the rebuild lease.
Recover only while the query gateway and crawler are stopped, and retain a
snapshot of both physical indexes until the outcome is proved.

1. Read `rebuild-lock` from the schema-versioned coordination index and record
   its owner, source index, candidate index, phase, source-block state, replay
   task ID, and last verified alias. An empty source with phase `initializing`
   identifies first-generation creation, not a source-free rebuild. Do not edit
   or delete the lease yet.
2. For first-generation creation (empty source and phase `initializing`,
   `cutover`, or `complete`), no reindex task is expected. Read the combined
   aliases and validate the candidate mapping. If the candidate is the exact
   active read/write index, delete the lease with OCC and retain the candidate.
   If both aliases are absent and the candidate is inactive, delete the
   candidate and then the lease. Any split/foreign alias state remains
   quarantined for escalation.
3. For a rebuild, if a task ID is present, read that exact task and require a
   positively observed terminal response. If the start outcome was uncertain
   and no ID was recorded, list reindex tasks and match both the recorded source
   and candidate in each task description. Cancel a matching live task if
   necessary, then
   positively verify that every matching task is terminal. A missing task or an
   unavailable task API is not proof of completion.
4. Read the read and write aliases in one combined request. Require them to
   identify one exact, prefix-owned physical index with the write alias marked
   as its write index. Read the source and candidate write-block settings as
   well; do not infer alias state from the lease phase alone.
5. If the candidate is active and the recorded phase and terminal task result
   prove validation and cutover completed, retain the blocked inactive source
   for the observation window and delete the lease with its current sequence
   number and primary term.
6. If the source is still active, first prove that no matching replay task can
   write to the candidate. Revalidate the source mapping and benchmark ACL-deny
   queries, clear its write block through the index-settings API, and read the
   setting back as false. Delete the candidate only after the combined alias
   read proves it inactive, then delete the lease with its current sequence
   number and primary term.
7. If task, alias, block, or validation evidence is incomplete, leave the lease,
   block, and candidate intact and escalate with the recorded evidence. Never
   clear quarantine merely because a lease is old.

## Security checks

- Verify listener addresses after every OpenSearch or packaging upgrade.
- Give the agent index/alias/search/bulk/snapshot-operation and cluster-monitor
  permissions only for its physical and coordination-index prefixes and approved
  repository. Rebuild also requires index block/settings, reindex, refresh, and
  task-monitor permissions; do not use the admin certificate.
- Rotate the gateway and OpenSearch credentials independently.
- Keep OpenSearch audit and slow-query logs local with customer retention.
  Do not log request bodies or highlighted fragments.
- Treat index snapshots as a full copy of the corpus: encrypt, restrict, test,
  and destroy them under the customer's retention policy.
