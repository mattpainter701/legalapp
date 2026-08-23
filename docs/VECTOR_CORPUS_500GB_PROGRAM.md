# LawHand 500 GB Legal Retrieval Corpus Program

**Status:** proposed project kickoff
**Objective:** operate a trustworthy public-law retrieval corpus with **at least
500 GB of used searchable vector-database storage**, while using the existing
Jetson fleet only for embedding work. The production chat path must stay
available throughout bulk loading.

## 1. Outcome and non-goals

The delivery is a legal-research corpus that supports citation-backed retrieval
across CourtListener and approved primary/official authority sources. A record
is searchable only after it has a stable source identity, provenance, temporal
metadata, normalized/chunked text, a 1024-dimension embedding, and a source
link for the answer UI.

This program does not mean indiscriminately scraping the web. Every source
must be represented in `legal_sources.json`, have an approved access/license
status, a documented rate limit, an owner, and a rebuild path. Tenant or matter
documents remain in the RLS-protected application database and never enter this
public corpus.

The 500 GB goal is a **physical searchable-store measurement**: vector payload,
vector index, filtered-search payload, and database overhead after compaction.
Raw archive staging, WAL, backups, and temporary index-build space do not count
toward the 500 GB target and must be budgeted separately.

## 2. Current baseline

The project already has a valuable foundation:

- `courtlistener-db` is isolated from the main LawHand database.
- CourtListener bulk loading is resumable and the corpus model retains court,
  opinion, citation, date, and chunk provenance.
- Official-authority adapters and `legal_sources` provide an explicit source
  policy and checkpoint model.
- Jetson workers partition pending chunks with `FOR UPDATE SKIP LOCKED`, embed
  both `opinion_chunks` and `legal_document_chunks`, and write normalized
  `mixedbread-ai/mxbai-embed-large-v1` 1024-dimension vectors.
- Existing production documentation records only about 5,024 embedded opinion
  chunks and a roughly 164 MB logical database. That is a pilot, not a sizing
  baseline for national coverage.

### Production storage checkpoint — 2026-08-23

The Skynet hypervisor was inspected read-only. Its Docker logical volume is
1.3 TB total, with approximately 1.2 TB free; the root filesystem has 68 GB
free. The active CourtListener PostgreSQL volume is 2.3 GB (2.17 GB logical
database) and the retained CourtListener bulk cache is 58 GB. Most database
space is presently `legal_document_chunks` (about 1.81 GB), while the original
5,024 opinion chunks occupy about 92 MB including their relation data.

This confirms that the current host can support the benchmark/pilot stage but
is **not** a safe home for the 500 GB searchable target: it cannot also provide
the required build/compaction headroom, raw-store capacity, and independent
backups. Do not run a destructive Docker volume prune: Docker may label the
retained bulk snapshot reclaimable merely because its one-shot loader is not
running, but it is a useful replay cache.

### Current-host execution target

Until dedicated storage is added, the operational target is **350 GB of used
searchable storage**, with a hard stop at 400 GB. Reserve the remainder of the
current ~1 TB free Docker volume for the retained bulk snapshot, WAL, index
build/compaction, exports, and a recovery margin. The 500 GB milestone remains
the target for the later dedicated-storage tier.

The existing loader must not be launched with `--load-staged` on production:
that path imports the whole staged CourtListener snapshot without a jurisdiction
or storage budget. Before a large production run, implement and test all of:

1. an explicit court allowlist (or named court group) for a tranche;
2. a maximum source-row/opinion/chunk budget;
3. a maximum database/search-store size guard checked between batches;
4. durable progress/checkpoint reporting; and
5. an automatic stop with an operator-visible reason at the budget or error
   threshold.

The first expansion tranche should be a 25 GB searchable-storage budget. It
must pass the retrieval suite and Jetson throughput check before the next
tranche is released.

## 3. Target architecture

Keep PostgreSQL as the authoritative catalog and transactional ingest ledger.
Move high-volume approximate-nearest-neighbor search to a dedicated vector
engine before the corpus grows past the first large pilot. The recommended
production split is:

```text
Approved sources -> immutable raw object storage -> normalize/chunk -> Postgres catalog
                                                                |                |
                                                                |                +-> FTS, sources, versions, citations
                                                                v
                                                        Jetson embedding queue
                                                                |
                                                                v
                                               dedicated vector cluster (dense vectors + filters)
                                                                |
                                      hybrid retrieval/reranker <- LawHand MCP/private chat
```

Use a self-hosted, sharded vector engine such as Qdrant for the 500 GB tier;
retain the current pgvector sidecar for the existing product and migration
validation. This avoids placing a very large HNSW build, large WAL bursts, and
application relational workload on one PostgreSQL volume. The final engine
choice is gated by the Phase 1 benchmark, but the interface must be provider
neutral so pgvector remains a rollback/search shadow.

### Capacity envelope

`mxbai-embed-large-v1` produces 1024 float32 values: about 4 KiB per vector
before identifiers, filter payload, or ANN index. Consequently, 500 GB of raw
vectors alone is approximately 122 million chunks. Depending on index settings,
metadata, replicas, and compaction, 500 GB of *used searchable storage* is
likely to represent roughly 60–100 million chunks. The program must use actual
measured bytes/chunk from a representative pilot rather than this estimate.

Provision separately, before the national ingestion phase:

| Resource | Minimum planning allocation | Why |
| --- | ---: | --- |
| Primary searchable vector storage | 2 TB NVMe | 500 GB target, growth headroom, rebuild/compaction |
| Relational catalog + FTS | 1 TB NVMe | source records, chunks, citations, ingest ledger |
| Raw immutable corpus | 2–4 TB object storage | replayable source packages and normalized artifacts |
| Build/WAL/temporary space | 1–2 TB fast local storage | bulk loads, index creation, compaction |
| Backups | 1.5x current data, independent location | point-in-time recovery and restore testing |

Sizing is for a single searchable copy. Replication multiplies it. Never size
the host from compressed CourtListener snapshots alone.

## 4. Delivery phases and gates

### Phase 0 — inventory and safety baseline (week 1)

1. Freeze a corpus manifest: source, license/terms decision, jurisdiction,
   authority tier, expected records/bytes, update cadence, owner, and replay
   location. Import nothing that lacks this entry.
2. Capture current production counts, database/index/table sizes, query p50/p95,
   Jetson CUDA/model details, embedding throughput, failed-batch rate, and disk
   growth. Store the report as an ingest-run artifact.
3. Separate the public corpus network, credentials, object storage bucket, and
   backup policy from tenant data. Verify restores before expansion.
4. Define a relevance evaluation set: 200+ jurisdiction-specific legal questions
   with expected authorities, citation verification, and no-result cases.

**Exit gate:** source register reviewed; a clean restore succeeds; no ingestion
job can write tenant tables; capacity dashboard is populated.

### Phase 1 — scale benchmark and engine decision (weeks 2–3)

1. Load a representative 1–5 million-chunk sample from CourtListener plus
   approved statutes/regulations. Preserve realistic long opinions, PDFs, and
   metadata filters.
2. Benchmark current pgvector HNSW and the selected dedicated engine with the
   same vectors, filters, and 200-query evaluation set. Measure recall@10,
   p95 retrieval latency, ingest rate, bytes/chunk, index-build time,
   compaction behavior, and recovery time.
3. Benchmark each Jetson independently, then together. Tune batch size only
   within thermal/memory limits; record sustained chunks/sec and restart
   behavior. Keep a CPU/Ollama fallback strictly for recovery, not bulk work.
4. Use the measured bytes/chunk to publish the final count, disk, replica, and
   calendar forecast for the 500 GB milestone.

**Exit gate:** chosen engine sustains the target filtered-query latency and
recall; Jetson capacity forecast is credible; 500 GB physical sizing is signed
off. If not, reduce vector precision or dimensions only after a documented
quality evaluation, never as an unmeasured storage shortcut.

### Phase 2 — production ingestion platform (weeks 4–6)

1. Add a provider-neutral vector repository with idempotent upsert/delete,
   collection aliases, source/version filters, and an outbox from the catalog.
2. Add durable ingest states for fetch, normalize, chunk, embed, index, verify,
   and quarantine. Each record must be replayable from raw storage and safe to
   resume after interruption.
3. Write immutable raw data to object storage with checksum, source URL,
   retrieval timestamp, parser version, and retention policy before processing.
4. Add observability: source lag, queued/embedded/failed chunks, duplicates,
   bytes by source, vector/index bytes, Jetson throughput/GPU temperature,
   query quality, and backup freshness. Alert on queue stall, error budget,
   unexpected storage growth, and stale sources.
5. Run dual-write and shadow-read against pgvector for the initial corpus;
   compare result sets and citations before changing the chat retrieval path.

**Exit gate:** replay drill, node-loss/restart drill, and dual-read relevance
comparison pass; no source can silently become searchable without provenance.

### Phase 3 — ordered corpus expansion (weeks 7–14)

Expand in value and authority order, stopping after each tranche for quality,
cost, and storage review:

1. **Controlling primary law:** SCOTUS, federal appellate courts, selected
   federal districts, and the jurisdictions LawHand serves first.
2. **Official current law:** all U.S. Code titles, eCFR, federal rules, state
   statutes/rules, and state administrative codes where official reuse is
   approved. Keep effective/superseded versions distinct.
3. **Broad case law:** remaining published/precedential CourtListener courts,
   then carefully scoped unpublished material labelled as non-binding.
4. **Official agency and court materials:** agency guidance, forms, manuals,
   court rules, and local sources only when authorization and citation quality
   meet the source policy.
5. **Historical/secondary material:** only after primary-authority coverage and
   retrieval ranking prove sufficient; it must be clearly tagged and cannot
   outrank binding authority by default.

Run shards by source partition, never one unlimited global loader. A partition
is eligible for the next stage only when its source checkpoint, vector count,
hash/dedup rate, citation rate, and evaluation score are recorded.

**Exit gate per tranche:** >99% non-quarantined chunks embedded, no unbounded
backlog, source freshness recorded, and relevance/citation tests do not regress.

### Phase 4 — reach and operate the 500 GB tier (weeks 15+)

1. Grow in controlled 25–50 GB searchable increments. Pause automatically at
   70% capacity, a query SLO breach, or a material quality regression.
2. At 500 GB, compact/optimize the vector store, reconcile catalog vs. vector
   IDs, take a consistent backup, restore it in isolation, and re-run the
   relevance suite.
3. Publish a coverage dashboard by court, jurisdiction, effective date,
   authority tier, source freshness, and indexed bytes. State the remaining
   coverage gaps plainly in product UI and retrieval metadata.

**Definition of done:** the dashboard reports >=500 GB used searchable vector
storage; 100% of vector IDs reconcile to a provenance-backed catalog record;
backup restore and a source replay have passed; the legal evaluation suite meets
the pre-agreed quality/SLO thresholds; and updates continue through automated,
checkpointed jobs.

## 5. Jetson operating model

Jetsons are stateless compute workers, not databases or source-of-truth storage.
Continue using deterministic partitions, short database transactions, and
`SKIP LOCKED`. Give each worker a stable ID; do not run duplicate IDs.

- Pin the model, tokenizer, normalization method, embedding dimension, and
  embedding version. A model change creates a new collection/version and a
  measured migration; it never mixes vectors silently.
- Dispatch work in source partitions and record a batch manifest. Do not allow
  every Jetson to scan the whole backlog without a per-worker rate/health limit.
- Use batch size 32 only as a starting point. Tune from observed sustained
  throughput, GPU memory, power draw, temperatures, and error rate.
- Maintain model cache, worker code release, SSH keys, and logs on the Jetson
  SSD. They may be rebuilt without losing corpus state.
- Keep the existing reverse-tunnel option for Jetsons that cannot reach the DB
  subnet; restrict DB credentials to the vector-write role and private LAN.

## 6. Retrieval, quality, and legal safeguards

The ingestion program is successful only if it improves answers. Retrieval must
be hybrid (lexical + dense), filter by jurisdiction/date/authority tier, and
rerank so controlling primary authority wins over semantic similarity. Return
the canonical source link, court/publisher, authority tier, effective date,
retrieved time, and version with every candidate.

Run the evaluation suite before promotions and weekly afterward. It should score
retrieval recall, controlling-jurisdiction correctness, citation resolvability,
temporal correctness, source attribution, and refusal/coverage-gap behavior.
Automated checks must quarantine malformed text, duplicate hashes, unexpected
language/OCR quality, missing canonical URLs, and stale effective dates.

### Source-link contract

Every public result returned to chat must carry a resolvable HTTPS `source_url`.
For official authority, this is the document's `canonical_url` on the
publisher's host (for example, `irs.gov`, `ecfr.gov`, or `uscode.house.gov`),
not a local database URL. For CourtListener, retain the CourtListener opinion
page URL unless an authoritative opinion URL is independently known and
validated. The chat source ledger and rendered citation must use this URL as
the hyperlink target and display the publisher/court plus retrieval date.

A result without a valid source URL is eligible for internal diagnostics only;
it is not eligible to support a chat citation. URL validation must reject
non-HTTPS, local/private-network, redirected-to-unapproved-host, or missing
targets. A weekly link checker records the final status, redirect target,
checked time, and failure reason without overwriting the original canonical
URL.

### Coverage ledger: answering “do we have this?”

The existing `legal_sources` and `source_sync_states` tables remain the source
and per-partition operational truth. Add a corpus-coverage ledger that records
the *expected* universe for every enabled source partition before ingest starts:

| Field | Meaning |
| --- | --- |
| source key / partition key | stable source and unit such as a court, U.S. Code title, CFR title, agency collection, or source release |
| expected coverage | jurisdiction, authority type, date/version range, and expected item count when published |
| acquisition state | not started, staged, loading, indexed, complete, partial, blocked, or retired |
| actual evidence | documents/chunks/vectors, byte counts, checksum/manifest version, first/last document date |
| freshness | upstream release date, last attempted/successful sync, next due, and stale-after threshold |
| quality | parsed/embedded/quarantined counts, duplicate rate, link-health state, and last evaluation score |
| gap reason / owner | rights/terms hold, unavailable upstream data, parser gap, storage budget, or intentionally deferred scope |

Expose this ledger through a private `corpus_status` endpoint and a persisted
daily JSON/CSV snapshot. Two months from now, an operator must be able to ask
for a court, title, agency, jurisdiction, or date and receive `complete`,
`partial`, `missing`, `stale`, or `not-approved`, with evidence and the next
action—not merely a raw row count.

For CourtListener, the expected partitions are court IDs and bulk-snapshot
release date. For statutory/regulatory feeds, they are individual title and
current/historical version. No source is called “complete” simply because its
last sync succeeded; completeness requires reconciliation to the declared
expected partition and version.

## 7. First sprint backlog

1. Create the source inventory and 200-question legal retrieval evaluation set.
2. Add a `corpus_metrics` report that captures rows/chunks, raw/vector/index
   bytes, backlog, per-source freshness, and Jetson chunks/sec.
3. Build the 1–5 million-chunk representative benchmark harness with a strict
   cost/disk guardrail.
4. Stand up the candidate dedicated vector engine as a non-production shadow
   environment; implement catalog outbox and dual-write only for the pilot.
5. Run a Jetson burn-in/throughput test and publish the measured weekly capacity
   forecast to 500 GB.
6. Add backup/restore, reconciliation, and ingest-resume drills to CI/operations
   acceptance criteria.

## 8. Decisions needed before Phase 1 completion

- Which jurisdictions and practice areas are first-class for LawHand customers?
- Does "500 GB" mean used physical searchable storage (the definition used
  here), or 500 GB of raw vector payload before index/replicas?
- What hardware/budget is available for the 2 TB primary store, raw-object
  storage, and independent backups?
- What p95 retrieval latency and recall@10 threshold should gate promotion?
- Who owns legal source/terms approval and recurring source-freshness review?
