# Clarity Legal architecture and trust boundaries

This document describes the release architecture as implemented. It separates
customer-facing readiness from capabilities that exist in code but are still
release-gated.

## 1. Production topology

Clarity is a containerized single-host application for the first-customer
release.

```mermaid
flowchart TB
    internet["Public internet"] -->|"TCP 80/443"| nginx["Nginx\nTLS, headers, limits, routing"]
    nginx --> frontend["React/Vite static application"]
    nginx --> backend["FastAPI API\nRUN_SCHEDULER=false"]

    migrator["One-shot Alembic migrator\nlegalapp owner role"] --> postgres[("PostgreSQL 16 + pgvector")]
    backend -->|"clarity_app / FORCE RLS"| postgres
    backend --> redis[("Redis 7\nauthenticated")]
    backend --> litellm["LiteLLM gateway"]
    litellm --> litellmdb[("LiteLLM PostgreSQL")]
    litellm --> models["Configured model providers"]

    scheduler["Dedicated single scheduler\nRUN_SCHEDULER=true"] --> postgres
    scheduler --> redis

    backend --> integrations["Microsoft, Google, Zoom, QBO, Stripe, SMTP"]
    backend -. "X-Clarity-Internal-Key" .-> courtlistener["Private CourtListener service"]
    courtlistener --> courtlistenerdb[("Private CourtListener pgvector DB")]
```

The production Compose invariants are:

- Only nginx publishes host ports 80 and 443.
- PostgreSQL, Redis, backend, frontend, LiteLLM, scheduler, and optional
  CourtListener services have no public listener.
- `migrator` must finish successfully before API or scheduler startup.
- `litellm-migrator` and the exact-diff `litellm-schema-migrator` must finish
  before LiteLLM during deployment. A read-only schema guard runs again on
  every proxy process start, including Docker/host restart recovery; LiteLLM
  runtime schema mutation remains disabled.
- API processes set `RUN_SCHEDULER=false`; one scheduler process sets it to
  `true`. PostgreSQL advisory locks remain a duplicate-run backstop.
- Backend and scheduler use `APP_DATABASE_URL`, which must identify the
  `clarity_app` role. Alembic uses `MIGRATOR_DATABASE_URL`, which identifies the
  owner role.
- `/health/readiness` reports only component states for disk, database, Redis,
  scheduler heartbeat, and durable queue. It does not expose tenant IDs,
  credentials, queue payloads, or infrastructure addresses.

`docker-compose.hypervisor.yml` is the existing Skynet topology. A conventional
Linux VPS, including AWS Lightsail, uses `docker-compose.yml` plus
`docker-compose.prod.yml`. Both paths use digest-pinned foundational images and
the same nginx-only public boundary.

The `deploy.resources.limits` entries in `docker-compose.prod.yml` are runtime
ceilings, not reservations. Their configured totals are 17.5 GiB of memory and
9 vCPU, but services without limits, transient builds/migrations, Docker, and
the host OS sit outside those sums. CPU may be time-shared, so the supported
single-customer host has 8 vCPU; memory cannot safely be overcommitted onto a
16 GB host. `prod_env_preflight.sh` runs `check_host_capacity.sh` before a
deployment can reach the data guard or build. It requires 8 online CPUs and
24 GiB guest-visible RAM. Every distinct filesystem backing `UPLOADS_HOST_DIR`,
the checkout/release backups, Docker's root, the application and LiteLLM
database binds, or any other bind in the exact resolved Compose model must have
160 GiB total and keeps a 25 GiB free profile floor. On every checked
filesystem, the gate additionally preserves 5 GiB for transient build/recovery
artifacts and computes the free-space reserve needed for `df` to remain
strictly below the configured `DISK_MAX_PERCENT` after that headroom is used;
the larger requirement wins. The VPS gate also requires its reviewed database
sources, `/data/legalapp/postgres` and `/data/legalapp/litellm-postgres`, to
remain present; an unreviewed relocation fails closed. Named volumes remain
covered by Docker's root filesystem. The Skynet hypervisor profile keeps the
same CPU/memory floor and its separately monitored 80 GiB total / 15 GiB free
profile floor on each of those filesystems, with the same threshold-derived
reserve and 5 GiB build headroom layered above it. It is selected only by the repository's exact
`docker-compose.hypervisor.yml` path. The supported
AWS Lightsail bundle is the general-purpose 2Xlarge-32GB Linux plan
(8 vCPU, 32 GB memory, 640 GB SSD); smaller bundles are not production targets.
This is a capacity floor, not a load-test result, and scaling still follows
observed CPU, memory, I/O, database, and queue headroom.

## 2. Persistence and failure domains

| Data | Persistence | Backup requirement |
|---|---|---|
| Application PostgreSQL | Named volume on the hypervisor; `/data/legalapp/postgres` bind mount in base+prod | Custom-format `pg_dump`, checksum, count manifest, encrypted off-host Restic snapshot, isolated restore proof |
| Uploaded and generated files | Absolute `UPLOADS_HOST_DIR` bind-mounted at `/app/uploads` in either topology; directory root owned by UID/GID 10001 | Immutable tar artifact plus sorted path/size/SHA-256 manifest, encrypted off-host snapshot, and safe extraction/hash verification |
| Redis | Persistent Compose volume with AOF enabled in production | Operational state only; PostgreSQL remains the durable business record |
| LiteLLM PostgreSQL | Dedicated volume/bind mount | Custom-format dump, checksum/count manifest, encrypted off-host snapshot, permanent-salt escrow, separately escrowed Restic credentials, and isolated restore proof |
| CourtListener corpus | Separate Compose volumes | Rebuildable corpus, but operational backup policy depends on ingest cost and local modifications |

This is not a highly available design. One host, one application database, one
Redis instance, and local file storage are single failure domains. The first
customer may run on this topology only with monitoring, encrypted off-host
backup, and a tested clean-host restore. Multi-host orchestration, database
failover, Redis failover, and point-in-time recovery are future scaling work.

## 3. Identity and authorization boundaries

### Application users

Email/password and Microsoft/Google sign-in issue a short-lived access token in
an HTTP-only cookie. Refresh tokens rotate and are tracked in Redis. Successful
rotation atomically consumes the presented token and retains only a family-id
tombstone for the remainder of that token's original lifetime. A replay during
that window atomically revokes the live family; after the original expiry, the
submission is rejected as expired without claiming family attribution. A signed
plan claim drives navigation, while
`ModuleGuardMiddleware` independently blocks API prefixes outside the tenant's
licensed modules.

Tenant identity is never accepted from request JSON. Authenticated user and
tenant identity come from the verified session. Public provider callbacks and
webhooks use provider-bound state, tenant-specific URLs, signatures, or shared
secrets as appropriate.

Legacy mediation and matter-portal invitation links carry an opaque secret but
no tenant identifier. Their public, source-IP-rate-limited exchange resolves the
hash by entering one ordinary active-tenant RLS context at a time. The matching
invite and tenant rows are locked through acceptance; no cross-tenant bypass is
used, suspended tenants are excluded, and all unavailable-token states return
the same generic response.

### Platform operators

A raw platform bootstrap secret is accepted only at
`POST /api/platform/auth/token`. Production configuration stores its SHA-256
hash with a fixed operator identity, maximum scopes, and expiry. Exchange
returns a scoped bearer token with a default 15-minute lifetime. Platform
requests are rate limited and written to the operator audit log.

The legacy static `PLATFORM_SECRET_KEY` is a time-boxed migration bridge only.
New deployments leave `PLATFORM_LEGACY_BOOTSTRAP_ENABLED=false` and the legacy
secret unset.

### Stored provider credentials

OAuth tokens, tenant-owned OAuth app secrets, QBO tokens, tenant BYOK keys, and
platform model-provider keys are encrypted at the application layer. The
newest key in `TOKEN_ENCRYPTION_KEYS` encrypts writes; remaining keys decrypt
old ciphertext during a staged rotation. This keyring does not replace host or
volume encryption and is currently injected through the host's protected
`.env` file.

Tenant BYOK does not accept an arbitrary administrator-controlled base URL.
Provider selection and URL validation restrict outbound destinations so a
tenant configuration cannot turn the backend into an SSRF or prompt-exfiltration
proxy.

## 4. Tenant isolation

Tenant-scoped request handlers call `set_tenant_context()` before data access.
PostgreSQL tables use Row Level Security with `FORCE ROW LEVEL SECURITY`, and
the runtime role is `NOSUPERUSER NOBYPASSRLS`. Application predicates remain in
queries as defense in depth and to keep test intent visible.

Cross-tenant operations use narrow, explicit system paths. They must set a
transaction-local bypass only where a corresponding RLS bypass policy exists,
enumerate active tenants, and then restore tenant context. The dedicated
scheduler processes each active tenant separately. Scheduler logs are
tenant-scoped at migration `088`; legacy null-tenant rows are not returned by
tenant APIs.

## 5. First-customer workflow: Zoom call to assigned task

```mermaid
sequenceDiagram
    participant Z as Zoom Phone
    participant N as Nginx
    participant A as FastAPI
    participant D as PostgreSQL/RLS
    participant W as Durable Zoom worker
    participant U as Intake user
    participant T as Assignee

    Z->>N: signed event / tenant webhook URL
    N->>A: POST /api/integrations/zoom-phone/webhook/{tenant_id}
    A->>A: validate CRC or tenant webhook signature
    A->>D: commit one idempotent call job under tenant RLS
    A-->>Z: 2xx only after durable commit
    W->>D: claim tenant call job
    W->>Z: fetch authoritative exact call detail
    W->>D: bind first opaque account ID if needed + import call atomically
    U->>A: review caller, contact/history matches, notes
    A->>D: create/update contact or lead and assignment task
    A-->>T: in-app task; optional configured notification
    T->>A: view, reassign, log customer contact, complete/close
    A->>D: task lifecycle + customer communication history
```

The tenant must own the Zoom account-level OAuth app configured through
Administration > Zoom; shared platform/S2S Phone credentials are not accepted.
The administrator does not enter Zoom's numeric Account Number. OAuth state is
bound to the tenant and tenant-owned client, and a successful account call-
history probe makes the refreshable grant usable for tests and manual sync. If
Zoom supplies its opaque `account_id` in an OAuth response, it is bound
automatically. Otherwise, the first correctly signed completion event becomes
the candidate only after the worker proves the same grant can fetch that exact
call. The binding and call import commit atomically; later event mismatches are
rejected. Webhook delivery readiness is tracked separately from Phone API
readiness, avoiding a broader Zoom user-profile scope without blocking history
imports.
The shipped intake integration requests call-history/call-detail scopes; it
does not fetch Zoom recording or transcript content. A dedicated queue lane
retries transient failures and hourly reconciliation covers missed delivery.
Production acceptance requires all of the following through the real public
hostname:

1. OAuth connect/reconnect saves a refreshable grant and its account call-
   history probe passes.
2. Zoom URL-validation CRC response succeeds through nginx/TLS.
3. A correctly signed supported event for a real inbound call reaches the
   tenant-specific endpoint.
4. The grant fetches that exact call, learns or confirms the opaque account
   binding atomically with import, and the call appears once in Call Intake.
5. The API connection test passes.
6. Intake creates a specifically assigned task.
7. The assignee can view and update that task without cross-tenant leakage.

Unit and E2E tests support this evidence but do not replace the live provider
proof.

## 6. PDF template data flow

```mermaid
flowchart LR
    upload["Admin uploads PDF"] --> inspect["Validate PDF\nreject password, active content, XFA"]
    inspect --> fields["Discover AcroForm widgets\nmax 250 pages / 200 fields"]
    fields --> retain["Retain immutable source\npath + SHA-256 metadata"]
    retain --> review["Review field mapping and preview"]
    review --> activate["Admin activates template"]
    activate --> fill["Matter/user values + reviewed edits"]
    fill --> render["Fill and flatten by default"]
    render --> store["Matter document + timeline provenance"]
```

PDF layout is preserved because the renderer fills actual AcroForm widget
positions in the retained source. Static or scanned PDFs without AcroForm
fields can be analyzed but cannot become generation templates. Password
protection, JavaScript/actions, embedded files, XFA, unsupported widget
appearances, unsafe scripts/glyphs, and values that cannot fit safely fail with
an actionable error instead of emitting a silently corrupted document.

Preview does not store a matter document and does not enforce required fields.
Saving an active template to a matter enforces required fields, creates a
unique output name, stores the binary through the matter-file store, and adds a
matter event with source/output hashes and renderer metadata. Signature fields
remain blank for a later signing workflow.

See [PDF template operations](PDF_TEMPLATE_OPERATIONS.md).

## 7. Retrieval and cloud data

Clarity has three distinct context paths:

1. **Session attachments:** stored for download, extracted on demand, and
   bounded before prompt injection. They are not automatically embedded.
2. **Indexed documents:** text is chunked, embedded, and stored in pgvector for
   tenant RAG. Public CourtListener chunks use a separate public corpus path.
3. **Live cloud search:** Microsoft/Google provider search returns candidates;
   bounded content from selected hits is used for the request. The local cloud
   metadata index stores routing metadata rather than mirroring an entire drive
   or mailbox.

Matter-file output chooses the configured cloud provider when available and can
fall back to local storage. A fallback is reported to the caller; operators
must monitor storage errors rather than assume every generated file reached a
cloud provider.

AI/provider logging and retention depend on the configured gateway and model
provider. LiteLLM raw message logging is disabled by default, but infrastructure
configuration and vendor contracts still determine the full data-handling
posture.

## 8. MCP boundary and release gate

LawHand publishes two isolated MCP hostnames with separate identities and tool
catalogs:

- `mcp.getlawhand.com` is the OAuth-backed workspace/platform MCP. It exposes
  bounded tenant-scoped reads and review proposals; it has no model-facing
  approval, filing, sending, or delivery tools.
- `research.getlawhand.com/api/mcp` is the research-only legal-research/RAG
  MCP; the shorthand host is also supported. Hosted ChatGPT and Claude clients
  use OAuth 2.1, while header-capable clients use a LawHand Research API
  token. It exposes no firm matter or workspace capabilities and remains
  disabled pending the product release gates below.

Nginx rejects unrelated paths on both hosts, while the legacy apex MCP paths
remain bounded compatibility aliases. The workspace OAuth issuer remains the
main application origin even though its canonical protected-resource URI is on
the workspace hostname.

Public research MCP is not part of the first-customer product.

- `MCP_PRODUCT_ENABLED=false` makes the public manifest and transport
  unavailable.
- Legacy unscoped `X-API-Key` issuance is retired and migration `087`
  invalidates existing legacy tenant API keys.
- The implemented `/api/mcp` endpoint uses the official Python SDK Streamable
  HTTP lifecycle and protocol negotiation.
- A Research API token is accepted only for an active tenant with explicit MCP
  entitlement, active billing, Stripe customer/meter configuration, tool scope,
  remaining monthly quota, and an available Redis burst limiter.
- A successful product call writes its usage event and durable Stripe meter job
  in one database transaction. Delivery retries use a stable identifier.
- Backend-to-sidecar traffic uses a dedicated `MCP_UPSTREAM_API_KEY`; application
  JWTs and customer keys are never forwarded.
- Pure retrieval calls do not invoke LiteLLM because they perform no model
  inference. A future model-backed synthesis tool must use internal LiteLLM
  with tenant, user, and opaque research-credential identifiers (never the raw
  credential) and reconciled spend.

These controls make the implementation fail closed; they do not constitute
commercial approval. Do not enable or promote MCP until the product-specific
deployment, billing reconciliation, monitoring, restore, and support gates in
[MCP product gateway](mcp_product_gateway.md) pass.

## 9. Marketing and SEO boundary

The public SPA emits absolute canonical/Open Graph/Twitter metadata, JSON-LD,
`robots.txt`, and a sitemap when built with `VITE_PUBLIC_SITE_URL`. Only `/`,
`/privacy`, and `/terms` are indexable. The build emits dedicated initial HTML
shells for `/privacy` and `/terms`, so title, description, canonical, social
metadata, and legal-summary content are route-correct before JavaScript loads;
React then takes over normally. Private routes are runtime-labeled `noindex,
nofollow` and omitted from the sitemap.
Production preflight binds this build-time origin to `https://$DOMAIN` in both
supported Compose topologies so a copied deployment cannot retain canonicals
from a different host.

This is still a client-rendered SPA, not SSR. Route-specific public shells
improve discovery, but crawl rendering and Core Web Vitals must be
measured after deployment. Public claims must stay within implemented and
operationally verified behavior; certification, uptime, trial, price, customer,
or provider claims require separate evidence and owner approval.

## 10. Deployment and rollback boundary

`scripts/deploy_prod.sh` is the on-host deployment gate. It performs preflight,
captures a pre-deploy dump/count manifest, starts PostgreSQL, builds the selected
topology, gates startup on migrations, waits for API/scheduler/nginx, validates
nginx and frontend image contents, refuses a post-deploy count decrease before
external provider checks, and then runs strict production checks. A fresh empty
host may use `BOOTSTRAP_MODE=true` once so an administrator can reach the UI and
configure its tenant-owned Zoom app; that mode is explicitly **NOT GO-LIVE** and
must be followed by the default strict production check.

Deployment is not the backup strategy. Before changing production, create and
copy an encrypted backup off-host. A rollback decision must preserve the new
database state: do not downgrade migrations or restore an old dump merely to
roll back containers without first assessing writes made after deployment.
Prefer rolling application code forward. If rollback is required, record the
deployed commit, failed gate, data-change window, selected image/commit, and
post-rollback production check.

The complete executable procedure and go/no-go checklist are in
[FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md](FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md).
