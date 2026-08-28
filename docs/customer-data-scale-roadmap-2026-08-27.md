# Customer data and scale roadmap

Date: 2026-08-27

## Executive decision

LawHand does not need a general-purpose object-store cache merely because files
transit the API. Streaming or short-lived local handling is acceptable at the
current single-host stage if size limits, tenant isolation, cleanup, backup,
monitoring, and failure behavior are enforced. It does need an object-store
abstraction before adding a second application host or intentionally retaining
new categories of server-hosted bytes.

Authoritative matter originals should continue to live in the customer's
Google Workspace or Microsoft 365 unless a documented product requirement and
customer contract say otherwise. That architecture reduces custody; it does
not eliminate LawHand's processor obligations for control-plane records,
messages, indexes, temporary uploads, generated files, inbound email, logs,
agreement evidence, or backups.

## Current production facts and invariants

- Production is one host, not high availability: Nginx behind Cloudflare
  Tunnel, four Uvicorn workers, one scheduler, one PostgreSQL, one Redis, and a
  bind-mounted `/app/uploads` volume.
- `/app/uploads` is covered by immutable tar/hash manifests and off-host
  snapshots, but remains in the same live failure domain as the application.
- Matter storage prefers customer Google/Microsoft providers; legacy/local
  fallback still exists for some paths.
- One Redis currently mixes security/control state and disposable caches. AOF
  plus `noeviction` protects refresh-replay tombstones and revoked JWT IDs, but
  lets cache pressure threaten the same instance.
- Cloudflare R2-compatible endpoint and credential names exist in configuration,
  but there is no approved operational bucket or application storage consumer.

These remain non-negotiable: tenant isolation, no public customer-data bucket,
no silent fallback from bound customer cloud to local matter storage, checksum
verification, legal-hold enforcement, and auditable deletion.

## Workstream 3 — operational object storage

Target: start before a second app node or any new feature retains server-hosted
customer bytes. Indicative effort: 2–4 engineer-weeks after legal/location
decisions.

### Decisions before bucket creation

1. Counsel and security approve the data classes, controller/processor terms,
   DPA/subprocessor disclosure, customer return/deletion behavior, backup
   exceptions, legal holds, and breach responsibilities.
2. Select US or EU jurisdiction based on customer commitments. R2 jurisdiction
   is immutable after bucket creation, so do not “pick later.”
3. Set RTO/RPO, maximum transient lifetime, evidence retention, and who may
   authorize exports or holds.

### Storage layout and controls

Use separate private buckets per environment and risk class, not one shared
bucket:

- `operational-transient`: quarantine, upload staging, and generated exports;
  short lifecycle, no matter originals by default.
- `agreement-evidence`: optional signed evidence packages; retention lock where
  counsel requires non-rewritability.
- `backup`: isolated credentials and restore-only production access.
- `public-corpus`: public legal corpus only, never customer content.

Implement an S3-compatible `BlobStore` interface first. Keys use opaque tenant
and object UUIDs, never client names, matter names, filenames, emails, or other
PII. PostgreSQL remains the control plane for bucket/key, SHA-256, size, media
type, storage state, retention deadline, legal-hold state, and deletion result.
Use per-service least-privilege tokens, TLS, provider encryption at rest,
version-aware conditional writes, bounded multipart uploads, malware/content
validation, and structured audit events without object contents or signed URLs.

Bucket lifecycle rules are a backstop, not the authoritative deletion ledger.
Provider expiry is asynchronous, so reconcile deletion results and alert on
objects still present after the expected window. Bucket locks override lifecycle
deletion; model and surface that conflict before enabling either feature.

### Migration sequence

1. Add the abstraction and metadata state machine while local storage remains
   authoritative.
2. Dual-write new eligible transient objects; compare hashes and sizes.
3. Backfill by tenant with bounded concurrency and resumable checkpoints.
4. Shadow-read R2 and reconcile every object against the database manifest.
5. Cut reads over by data class, retaining a measured rollback window.
6. Delete local bytes only after checksum, read, restore, lifecycle, and legal-
   hold tests pass and the reconciliation count is zero.

Exit gate: zero unaccounted objects, successful cross-host restore drill,
operator inventory by data class, legal-hold test, expired-object reconciliation,
and no customer-data bucket with public access.

## Workstream 4 — Redis separation

Target: after the data inventory ships and before load tests can put meaningful
pressure on shared Redis. Indicative effort: 1–2 engineer-weeks.

Split by failure semantics:

- Security/control Redis: refresh rotation/replay state, revoked JWT IDs,
  distributed locks, and other fail-closed controls. Use AOF, `noeviction`,
  authenticated/TLS connections, tight memory alerts, backups where required,
  and explicit recovery tests.
- Cache Redis: RAG/materialized caches and other recomputable values. Use an
  eviction policy appropriate to measured access (initially allkeys-LRU/LFU),
  bounded TTLs, separate memory, and fail-open cache-miss behavior.

Introduce typed configuration (`SECURITY_REDIS_URL`, `CACHE_REDIS_URL`) and
typed clients; retain `REDIS_URL` only as a time-bounded compatibility bridge.
Inventory every key prefix and owner. Dual-write and verify security replay
state before cutover; cache can cold-start. Test security Redis unavailable,
cache Redis unavailable, OOM/noeviction, restart, stale lock, and restoration.
Add a separate queue/counter instance later only if measured contention or
availability semantics require it.

Exit gate: no security key can land in cache Redis, security operations fail
closed without taking down ordinary cache misses, dashboards show memory/AOF/
latency per instance, and rollback has been exercised.

## Workstream 5 — performance and high availability

Target: begin capacity baselining now; build multi-host HA after storage and
Redis state are externalized. Indicative effort: 3–6 engineer-weeks for one
engineer, excluding managed-database procurement and remediation found by tests.

1. Define traffic and SLOs: active tenants/users, upload sizes, sync volume,
   chat concurrency, scheduler load, p95/p99 latency, error budget, RTO, and RPO.
2. Build synthetic-data k6/Locust journeys for login/refresh, matters, chat,
   uploads, search, admin inventory, OAuth callbacks, and background sync. Never
   load-test with client documents.
3. Establish single-host saturation at 1×, 2× projected peak, burst, and soak.
   Record API CPU/memory/event-loop lag, Postgres pools/locks/IO, Redis memory/
   latency, upload bandwidth, provider throttling, queue lag, and error rates.
4. Remove bottlenecks against explicit gates; avoid scaling app replicas while
   writable files or singleton assumptions remain local.
5. Run at least two application hosts behind the ingress, with external object
   storage and one logically elected scheduler using database advisory locks.
6. Add PostgreSQL PITR, replica/failover, connection management, restore drills,
   and monitored replication lag. Add Redis HA appropriate to each instance.
7. Run redundant Cloudflare Tunnel connectors on separate hosts/failure domains;
   treat replicas as connector availability, not as an intelligent application
   load balancer.
8. Drill host loss, database failover, Redis loss, tunnel loss, provider outage,
   partial object write, restore, and region/jurisdiction constraints.

Exit gate: final release candidate passes fresh 2× peak and soak tests within
SLO, a host can fail without data loss beyond RPO, restore meets RTO, scheduler
jobs do not duplicate, and operations has a rehearsed incident runbook.

## Contract/privacy track (parallel, counsel-owned)

The public Terms/Privacy notice should remain understandable, but production
customers also need counsel-approved commercial documents covering service
scope and disclaimers, confidentiality/security duties, data-processing roles,
subprocessors and locations, retention/return/deletion (including backups and
legal holds), incident notice, support/SLA, liability/indemnity, suspension,
termination, and signature authority. Maintain a versioned subprocessor list
and ensure product behavior matches those commitments before enabling the gate.

The platform evidence ledger can prove which bytes/version a tenant admin
accepted and when. It cannot prove that the document terms are legally adequate;
that remains counsel's decision.
