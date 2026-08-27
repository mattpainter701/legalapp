# WellPled

WellPled is a multi-tenant, AI-assisted legal operations platform. It
combines caller intake, tasks, matters, document automation, billing, cloud
integrations, and source-aware legal research in one web application.

The current commercial launch is intentionally narrow: **Call Intake + Tasks +
Zoom Phone**. The wider platform remains available for controlled onboarding.
Public MCP access is release-gated and must remain disabled.

## Release state

| Surface | Current state |
|---|---|
| Call Intake, Tasks, Zoom Phone | First-customer release candidate. Ship only after every gate in the [first-customer production runbook](docs/FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md) passes on the deployed revision. |
| PDF templates | Adobe-style Prepare Form review, existing AcroForm discovery, OCR-assisted scan/handwriting overlays, manual field placement, flattening, integrity checks, and matter-file output are implemented. See [PDF template operations](docs/PDF_TEMPLATE_OPERATIONS.md) for supported inputs and customer recovery steps. |
| Full legal platform | Available for controlled tenants; modules are enforced in both navigation and API middleware. |
| Public MCP product | **Disabled.** `MCP_PRODUCT_ENABLED=false` is a launch invariant. Do not market, issue, or accept customer MCP keys yet. |
| Marketing / SEO | Public landing, original artwork, social card, canonical metadata, structured data (organization, software, capability list, breadcrumbs, FAQ, site navigation), sitemap, and private-route `noindex` controls are included. Google Search Console, Analytics 4, and Business Profile setup is in [docs/GOOGLE_SEARCH_AND_BUSINESS_SETUP.md](docs/GOOGLE_SEARCH_AND_BUSINESS_SETUP.md). Marketing claims and prices still require commercial-owner approval before publication. |

## Task and customer-data lifecycle

![Task routing and customer-owned document storage](frontend/public/guide-assets/customer-data-task-lifecycle.svg)

LawHand Tasks is the authoritative work record. Outlook and Google calendar
events are projections, not a second task database. A reviewed email can create
a task only when its subject begins with `[TASK]` or `[DEADLINE]`; untagged
body text, replies, forwards, and model-only date guesses do not create work.

For a cloud-bound tenant, durable matter-file bytes live in the tenant-selected
OneDrive, SharePoint, or Google Drive. Auto binds an active Microsoft 365 tenant
to OneDrive unless the administrator overrides the provider. Portal originals
are stored under `{matter}/client_uploads`; reviewed derivatives become new
documents in the appropriate matter folder. Provider failure is fail-closed and
does not silently create a durable local copy.

The SaaS still stores its control plane—matters, clients, tasks, assignments,
cloud object IDs, hashes, indexing metadata, and audit history—so the accurate
boundary is customer-owned **document content**, not zero customer data. See
[Task routing and customer-owned document storage](docs/task-and-customer-data-lifecycle.md)
for the backend contract, recovery checks, and remaining legacy-local migration.

## What is in the product

| Area | Implemented behavior |
|---|---|
| Caller intake | Manual and Zoom Phone intake, signed tenant webhook, call-history sync, caller/contact matching, notes, lead/task handoff, partner assignment log, and CSV exports. |
| Tasks | Tenant-scoped CRUD, assignee and matter/contact links, priorities, deadlines, reminders, assignment notes, viewed/contacted signals, reassignment, and close reasons. |
| Document templates | DOCX/TXT sample analysis and variable substitution; PDF/image intake with local PDF.js review, AcroForm and OCR-assisted field discovery, manual correction, retained sources, flattened output, and matter storage. |
| Matters and CRM | Matters, contacts, parties, notes, assignments, budgets, files, timelines, communications, intake, and reports. |
| Billing | Time, expenses, invoices, payments, retainers, LEDES export, and optional Stripe payment flows. |
| Research and drafting | Tenant document RAG, public CourtListener context, practice-area workflows, source labels, and attorney-review guardrails. |
| Cloud integrations | Microsoft and Google OAuth, cloud search/storage paths, Zoom, QuickBooks, Teams, SMTP, and optional Slack/webhook notifications. Each provider still requires its own production consent and ingress proof. |
| Authentication | Email/password, Microsoft and Google login, short-lived access cookies, rotating Redis-backed refresh tokens, tenant/module authorization, and scoped platform-operator sessions. |

No AI output is a substitute for professional review. A displayed citation or
source label is a review aid, not a guarantee that authority remains good law.

### Chat evidence and citation contract

Legal-chat answers use three review states:

- `cited` means the claim points to an exact retrieved source and the cited
  passage directly supports the claim. Faithful paraphrases are allowed.
- `verify` means the proposition, application, or inference still requires
  attorney confirmation. A source may be linked for context without upgrading
  the proposition to `cited`.
- `model` means general model knowledge rather than retrieved firm material or
  public authority.

Retrieval and evidence review are separate stages. Tenant material is protected
by a lexical/semantic relevance gate. Public-authority search uses broad recall,
then independently reranks candidates using semantic similarity, meaningful
issue-term coverage, and phrase coverage. Generic matches such as only the word
"court," "law," or a jurisdiction name are excluded. FTS rank is never reported
as semantic similarity.

The API returns `citation_annotations` for every tagged claim. Each annotation
contains stable character offsets, the normalized review state, exact source
IDs, source-marker spans, and the review-tag span. The browser uses this
structured contract to bind claims, tags, and sources; bracket parsing remains
only as a compatibility fallback for an in-flight or older client. Because the
annotations are deterministically rebuilt from persisted Markdown, existing
conversations receive the structured contract without a database migration.

Only sources actually bound to answer claims appear in the visible citation
ledger. Retrieval counts remain in the reference audit, so unused search results
are observable without being presented as support. Inline citation numbers open
the original authority or authenticated firm document, while a linked `cited`
or `verify` badge jumps to the supporting excerpt in the response ledger.

Source metadata may include an independent match score, authority tier, official
status, jurisdiction, effective date, and locator when the upstream corpus
provides them. These fields assist review; they do not perform citator treatment
checks or guarantee that an authority remains current, controlling, or unmodified.

## Runtime architecture

```mermaid
flowchart TB
    user["Browser / Zoom webhook"] --> edge["Nginx\nonly public ports: 80/443"]
    edge --> web["React/Vite frontend"]
    edge --> api["FastAPI API\nRUN_SCHEDULER=false"]

    migrate["One-shot Alembic migrator\nowner database role"] --> db[("PostgreSQL 16 + pgvector")]
    api -->|"clarity_app / FORCE RLS"| db
    api --> redis[("Authenticated Redis\nrefresh state, limits, caches")]
    api --> llm["Private LiteLLM gateway"]
    llm --> providers["Configured model providers"]

    scheduler["Dedicated scheduler\nRUN_SCHEDULER=true"] -->|"tenant-scoped jobs"| db
    scheduler --> redis
    scheduler --> outbox["Durable job outbox\nretryable external delivery"]

    api --> cloud["Microsoft / Google / Zoom / QBO / Stripe"]
    api -. "dedicated internal key" .-> mcp["Private CourtListener sidecar"]
```

Production separates schema ownership from runtime access:

- `MIGRATOR_DATABASE_URL` uses the database owner and is available only to the
  one-shot migrator.
- `APP_DATABASE_URL` uses `clarity_app`, which is `NOSUPERUSER NOBYPASSRLS`.
- API workers never start APScheduler. Exactly one scheduler container owns
  scheduled and durable jobs.
- PostgreSQL, Redis, backend, frontend, LiteLLM, and the optional CourtListener
  sidecar are private. Nginx is the only public container.
- Stored OAuth, provider, and tenant app credentials are application-encrypted
  with a staged Fernet keyring.

The detailed trust boundaries and data flows are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Can this run on AWS Lightsail or another VPS?

Yes, as a **single-host first-customer deployment** on an x86-64 Linux VPS with
Docker Engine, Compose v2, Python 3, persistent SSD storage, a static public
address, DNS, and inbound TCP 80/443 only. The configured Compose memory **limits** sum to
17.5 GiB and the configured CPU limits sum to 9 vCPU. These are per-container
ceilings, not reservations or a promise that the stack fits that sum; nginx,
builds, migration helpers, Docker, and the host OS also need capacity. The
supported Lightsail starting point is AWS's general-purpose
[2Xlarge-32GB Linux bundle](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html):
8 vCPU, 32 GB memory, and 640 GB SSD. The 16 GB bundle is not supported.

Production preflight enforces a conservative hard floor of 8 online CPUs and
24 GiB guest-visible RAM. Every distinct filesystem used by uploads,
application/LiteLLM database binds, release backups/source, Docker, or any other
bind in the exact resolved production Compose model must provide at least 160
GiB total and retain the 25 GiB profile floor. Preflight also reserves 5 GiB
for transient build/recovery artifacts and requires enough additional free
space for `df` to remain strictly below `DISK_MAX_PERCENT` after that reserve
is consumed. At the default 85% threshold, a 160 GiB usable filesystem needs
about 30.6 GiB free before deployment: 16% of `df`'s used-plus-available
capacity, plus the 5 GiB headroom. The threshold-derived requirement wins when
it is higher than the profile floor. The reviewed VPS database paths are
`/data/legalapp/postgres` and `/data/legalapp/litellm-postgres`; changing or
removing either fails preflight until the topology and gate are reviewed together.
The 24 GiB gate accounts for provider/guest reporting while still requiring the
32 GB Lightsail bundle. Use the base plus production topology:

```bash
COMPOSE_FILES="docker-compose.yml docker-compose.prod.yml" \
  bash scripts/deploy_prod.sh --build
```

The current topology is not highly available: the VPS, local PostgreSQL, local
Redis, and attached storage are single failure domains. Production acceptance
therefore requires encrypted off-host backup, a clean-host restore rehearsal,
public monitoring, and a documented rollback. A provider snapshot alone is not
an application-consistent backup.

For the existing Skynet host, use `docker-compose.hypervisor.yml` and the
repository's production-deployment skill/runbook. Do not run both production
topologies against the same data volumes.

See [first-customer production runbook](docs/FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md)
for provisioning, fresh-host proof, deployment, backup/restore, Zoom ingress,
alerts, and go/no-go evidence.

## API and credential boundaries

| Credential | Accepted at | Important constraints |
|---|---|---|
| Application session | Normal `/api/*` routes | Short-lived JWT access cookie plus rotating, revocable refresh state; tenant and module authorization still apply. |
| Platform bootstrap key | `POST /api/platform/auth/token` only | Stored as an identity/scope/expiry-bound SHA-256 hash. It is exchanged for a short-lived scoped bearer token and is rate limited/audited. |
| Platform bearer token | `/api/platform/*` | Defaults to a 15-minute lifetime and cannot exceed its bootstrap grant. |
| Research MCP OAuth/API token | `https://research.getlawhand.com/api/mcp` (shorthand host supported) | OAuth 2.1 is used by hosted ChatGPT/Claude clients; header-capable clients use a LawHand Research API token. Research scope, active tenant, entitlement, billing, quota, and Redis burst limits fail closed. Workspace tools are excluded, and the product remains release-gated. |
| MCP upstream key | Backend to private CourtListener sidecar only | Dedicated 32+ character server credential. Browser JWTs and customer keys are never forwarded upstream. |
| Tenant BYOK | Approved model provider only | Provider URL is allowlisted/validated; arbitrary administrator-controlled hosts are rejected. |

Operational details are in
[credential security operations](docs/credential_security_operations.md) and
[MCP product gateway](docs/mcp_product_gateway.md).

## Marketing and SEO posture

The production frontend build uses `VITE_PUBLIC_SITE_URL` to emit absolute
canonical/social URLs, `robots.txt`, `sitemap.xml`, and JSON-LD. `/`, `/privacy`,
and `/terms` are indexable. Login, signup, portals, and every authenticated
workspace route are marked `noindex, nofollow` and omitted from the sitemap.
Production preflight requires this value and verifies that, after removing one
optional trailing slash, it exactly equals `https://$DOMAIN`; both supported
Compose topologies pass that explicit value into the frontend build.

This is a client-rendered SPA. The home page has useful static metadata and a
`noscript` summary, but it is not server-rendered or prerendered. If organic
search becomes a primary acquisition channel, prerendering and real search
performance telemetry are the next architecture step. Do not publish security
certifications, uptime promises, customer logos, trial enforcement, or prices
that have not been independently approved and operationally supported.

## Local development

Prerequisites: Git, Docker Engine, and Docker Compose v2.

```bash
git clone https://github.com/mattpainter701/legalapp
cd legalapp
```

Create an untracked `.env` with at least:

```dotenv
DATABASE_URL=postgresql+asyncpg://legalapp:legalapp@postgres:5432/legalapp
REDIS_URL=redis://redis:6379/0
SECRET_KEY=replace-with-a-local-random-secret-at-least-32-characters
TOKEN_ENCRYPTION_KEY=replace-with-a-valid-fernet-key
BACKEND_URL=http://localhost
FRONTEND_URL=http://localhost
DEV_MODE=true
MCP_PRODUCT_ENABLED=false
```

Generate the Fernet key without writing it to shell history:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then start the development stack. Docker Compose automatically applies
`docker-compose.override.yml`:

```bash
docker compose up -d --build
docker compose ps
curl --fail http://localhost/health
```

The web application is at `http://localhost`. The development override does not
publish PostgreSQL, Redis, or backend defaults on their standard ports; review
the override before connecting local tools.

## Validation

```bash
# Backend (inside the running development image)
docker compose exec -T backend pytest tests -q

# Frontend
cd frontend
npm ci
npm run check
```

Release validation additionally includes migration/RLS integration tests,
official MCP protocol tests, CourtListener sidecar tests, Compose resolution,
shell syntax checks, a disposable fresh-host rehearsal, production checks, and
the off-host restore proof described in the runbook.

## Release notes workflow

Customer-facing changes are written as short, plain-language bullets in
`backend/app/release_notes.json`. That catalog drives the sign-in announcement
and the Profile/Admin release panels. Run
`python scripts/generate_release_notes.py` after editing the catalog to refresh
[customer release notes](RELEASE_NOTES.md), then record implementation,
security, migration, and operator detail in the [technical changelog](CHANGELOG.md).

Pull requests must state whether they include a customer-facing change. CI runs
`python scripts/generate_release_notes.py --check` on every commit and requires
the catalog, generated notes, and technical changelog to move together when a
customer release-note update is declared.

## Documentation map

- [Customer release notes](RELEASE_NOTES.md)
- [Technical changelog](CHANGELOG.md)
- [MCP documentation index and future wiki handoff](docs/mcp/README.md)
- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [First-customer production runbook](docs/FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md)
- [Forward email to a matter — user guide](docs/inbound_email_user_guide.md)
- [Inbound matter email — admin and operator runbook](docs/inbound_email_setup.md)
- [Standalone Call Intake plan and API enforcement](docs/call-intake-standalone.md)
- [Zoom and cloud integration setup](docs/integrations-setup.md)
- [Zoom Phone per-customer app setup](docs/ZOOM_PHONE_TENANT_APP_SETUP.md)
- [PDF template operations](docs/PDF_TEMPLATE_OPERATIONS.md)
- [Credential rotation and platform access](docs/credential_security_operations.md)
- [MCP product gateway release gates](docs/mcp_product_gateway.md)
- [Matter automation and workspace MCP architecture](docs/matter_automation_workspace_mcp.md)
- [Production hardening](docs/PROD_HARDENING.md)
- [SBOM tracking inventory](docs/SBOM_TRACKING_INVENTORY.md)

The current Alembic head for this release is
`094_admin_conf_call_content`; migrations `086`-`094` cover retained PDF template
sources, fail-closed MCP product security, tenant-isolated scheduler logs,
durable Zoom Phone call import, automatic provider-proven Zoom webhook binding,
value-bound single-consumption PDF activation/generation preview evidence with
fail-closed storage reconciliation state, and confidential-call-content
capability backfills for the default internal User and Administrator roles.
