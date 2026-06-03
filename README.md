# Clarity Legal

AI-powered legal platform for in-house and boutique legal teams. Multi-tenant SaaS with practice-area intelligence, document RAG, matter management, MCP integration, and an operator console.

---

## What it does

| Capability | Description |
|-|-|
| **Legal Research Chat** | Grounded in uploaded documents + CourtListener public case law via pgvector RAG; confidence-tagged citations |
| **Practice Area Plugins** | 11 workspaces with cold-start profiles, structured skill prompts, dual LLM tiers, and compliance gates |
| **Matter Management** | Firm-wide matter CRUD with assignments (lead/associate/paralegal roles), internal & client-facing notes, key dates, budgets, and append-only event timelines |
| **Contract Renewal Tracker** | Urgency-rated dashboard with automated weekly email alerts |
| **MCP (Model Context Protocol)** | Connect external AI tools (Claude, Cursor, custom agents) to your legal knowledge base |
| **Platform / Operator Console** | Multi-tenant admin with usage dashboards, tenant CRUD, and platform-key auth |
| **OAuth + Email Auth** | Microsoft 365, Google, or email/password sign-in; unified signup collects firm profile |
| **Multi-Model AI** | DeepSeek V4 Flash (primary), DeepSeek V4 Pro (premium) via OpenCode.ai; Azure OpenAI + Gemini support |
| **Audit & Usage Logging** | Every LLM call logged with tokens, cost, query text, RAG sources, IP, user agent; error logs with resolution tracking |
| **User Expertise Tracking** | Per-user practice areas, expertise level, memory summary, privacy preferences |
| **Context Usage Transparency** | Explicit source attribution in chat responses; relevance scores for each context source |
| **PII Protection** | Automatic detection and scrubbing of 8 PII types (SSN, credit card, phone, email, IP, passport, driver's license, bank account) |
| **Skill-Based Chat Routing** | Route messages to specific legal plugins; inject matter context with privacy controls |
| **Expertise-Aware Caching** | Cache TTLs based on user expertise level (junior paralegal ≠ senior partner); skill-based multipliers |
| **Auto-Memory Generation** | Per-user conversation summaries; learned preferences and interaction patterns stored as UserMemory |
| **Platform Billing** | Stripe flat-seat and PAYG metered tiers for platform subscription |
| **Legal Billing** | Time tracking with UTBMS codes, expense disbursements, invoice generation with line items, payment recording, Stripe payment links, LEDES 1998B export |
| **Recurring Billing** | Scheduler-driven auto-invoice generation for matters on monthly/quarterly billing cycles |
| **Retainer Management** | Upfront retainer deposits with balance tracking, drawdown against invoices, full transaction audit trail |
| **Invoicing** | Auto-numbered invoices (INV-YYYY-XXXXXX), line-item breakdowns, status workflow (draft/sent/paid/overdue/void), PDF export |
| **Time Tracking** | Billable time entries with UTBMS task/activity codes, hourly rates, matter linking, status lifecycle (draft→billed→written_off) |
| **Matter Assignments** | M:N user-to-matter assignment with roles (lead, associate, paralegal) and primary flag |
| **Matter Notes** | Internal and client-facing structured notes on matters, optionally billable with hours |
| **Prompt Overrides** | Per-tenant, per-skill prompt customization with test-before-save endpoint |
| **Matter File Store** | Routes document uploads to customer's connected cloud (OneDrive/Google Drive) with local-disk fallback |
| **Contacts & CRM** | Person/organization contacts with search, soft-delete, matter linking, intake pipeline |
| **Task Management** | Task CRUD with matter/contact linking, deadlines, priorities, hourly email reminders |
| **Deadline Calendar** | Aggregated calendar view of task deadlines, matter key dates, and contract renewals |
| **Communications Log** | Full communication log CRUD with filters by matter, contact, channel, date range |
| **Lead-to-Matter** | One-click lead conversion to matter with client contact auto-linking |
| **Matter Budget** | Budget tracking with billable hours vs budget utilization progress bar |
| **Document Templates** | Reusable templates with `{{variable}}` substitution, render to MatterDocument |
| **Firm Reports** | Matter status, intake funnel/conversion rate, overdue tasks dashboards |

---

## Tech stack

### Backend
| Layer | Technology |
|-|-|
| API | FastAPI + uvicorn, async throughout |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Database | PostgreSQL 16 + pgvector |
| Cache / Rate limiting | Redis 7 |
| Auth | JWT + Microsoft Entra OAuth2 + Google OAuth2 + email/password |
| Primary LLM | DeepSeek V4 Flash via OpenCode.ai (OpenAI-compatible) |
| Premium LLM | DeepSeek V4 Pro via OpenCode.ai |
| Enterprise AI | Azure OpenAI GPT-4o + Google Gemini 2.0 Flash (optional) |
| Embeddings | OpenAI text-embedding-3-small for tenant docs; BGE-small 384-dim for CourtListener public chunks |
| Task scheduler | APScheduler AsyncIOScheduler |
| Migrations | Alembic (27 migrations) |
| Billing | Stripe Python SDK |
| Multi-tenancy | PostgreSQL Row Level Security enforced at DB layer |
| Services | PII detection (8 types), Memory service (auto-summarization), Matter context (with scrubbing), Expertise-aware cache manager (3-tier TTLs), Matter file store (OneDrive/Google Drive routing), Recurring billing (auto-invoice scheduler), Prompt resolver (per-tenant skill overrides) |

### Frontend
| Layer | Technology |
|-|-|
| Framework | React 18 + Vite 6 |
| Styling | Tailwind CSS (custom brand palette: sage green / warm gold / navy / cream) |
| Fonts | Inter (body) + Source Serif 4 (headings) |
| Icons | Lucide React |
| Routing | React Router v6 |
| HTTP | Axios with Bearer token interceptor |
| Markdown | react-markdown with citation colour-coding and confidence tags |

### Infrastructure
| Component | Technology |
|-|-|
| Containers | Docker Compose (base + override + hypervisor + prod profiles) |
| Reverse proxy | Nginx (HTTP dev config; TLS 1.2/1.3 prod config) |
| SSL | Let's Encrypt via Certbot |
| CI/CD | GitHub Actions — lint, test, build, SSH deploy |

---

## Practice area plugins (11)

| Plugin | Skills |
|-|-|
| **Commercial Legal** | vendor-agreement-review, nda-review, saas-msa-review, renewal-tracker |
| **Privacy Legal** | dpa-review, dsar-response, pia-generation, reg-gap-analysis |
| **Litigation Legal** | matter-intake, portfolio-status, demand-draft, claim-chart, legal-hold |
| **Corporate Legal** | diligence-review, closing-checklist |
| **Employment Legal** | hire-review, termination-review, classification-analysis |
| **Product Legal** | launch-review, marketing-claims-check |
| **IP Legal** | trademark-clearance, fto-analysis, cnd-triage |
| **AI Governance** | use-case-triage, impact-assessment, vendor-ai-review |
| **Regulatory Legal** | reg-gap-analysis, policy-diff, nprm-comment |
| **Trust & Estate** | estate-portfolio, estate-detail, will-trust-review, probate-tracking |
| **Mediation** | case-portfolio, case-detail, intake-brief, settlement-draft |

All skill outputs include confidence tags (`[settled]` / `[verify]` / `[model knowledge]`) and an attorney-review banner.

---

## Routes

| Path | Auth | Page |
|-|-|-|
| `/` | — | Marketing homepage (unauthenticated); Chat (authenticated) |
| `/login` | — | OAuth + email login |
| `/signup` | — | Unified signup (company info + OAuth or email) |
| `/chat` | ✓ | Main research chat |
| `/plugins` | ✓ | Plugin gallery |
| `/plugins/:name` | ✓ | Plugin detail + skills |
| `/plugins/litigation/matters` | ✓ | Litigation portfolio |
| `/plugins/litigation/matters/:id` | ✓ | Matter detail + timeline |
| `/plugins/commercial/renewals` | ✓ | Renewal tracker |
| `/plugins/trust-estate/estates` | ✓ | Trust & Estate portfolio |
| `/plugins/trust-estate/estates/:id` | ✓ | Estate detail + activity log |
| `/plugins/mediation/cases` | ✓ | Mediation case list |
| `/plugins/mediation/cases/:id` | ✓ | Case detail + session log |
| `/admin` | ✓ admin | Tenant admin (users, usage, model settings) |
| `/billing` | ✓ | Stripe billing management |
| `/mcp` | ✓ admin | MCP API keys + tool docs + connection guide |
| `/contacts` | ✓ | CRM contacts directory |
| `/tasks` | ✓ | Task board with filters |
| `/calendar` | ✓ | Deadline calendar (tasks, key dates, renewals) |
| `/communications` | ✓ | Communication log with filters |
| `/reports` | ✓ | Firm analytics (matter status, intake funnel, overdue tasks) |
| `/templates` | ✓ | Document template library |
| `/matters` | ✓ | Firm-wide matter portfolio with assignments, notes, budgets, key dates |
| `/matters/:id` | ✓ | Matter detail — tabs: Overview, Timeline, Parties, Documents, Notes, Budget |
| `/time-tracking` | ✓ | Time entry log — create, filter by matter/status, billable tracking |
| `/invoices` | ✓ | Invoice list — generate from unbilled time/expenses, filter, status workflow |
| `/invoices/:id` | ✓ | Invoice detail — line items, payments, PDF export, Stripe payment link |
| `/profile` | ✓ | User profile — assigned matters, time summary, billing stats |
| `/intake` | ✓ | Lead intake pipeline — stage counters, advance/convert actions |
| `/platform` | platform key | Operator console (multi-tenant admin) |

---

## Getting started (dev)

### Prerequisites
- Docker + Docker Compose
- Git

### Setup

```bash
git clone https://github.com/mattpainter701/legalapp
cd legalapp
```

Copy and edit `.env`:

```env
DEEPSEEK_BASE_URL=https://opencode.ai/zen/go/v1   # or https://api.deepseek.com/v1
DEEPSEEK_API_KEY=sk-...                            # OpenCode.ai or direct DeepSeek key
PRIMARY_LLM=deepseek-v4-flash
PREMIUM_LLM=deepseek-v4-pro
OPENAI_API_KEY=sk-...                              # Optional — embeddings (falls back to deepseek key)
SECRET_KEY=<random-64-char>
```

```bash
docker compose up -d
```

| Service | URL |
|-|-|
| App (via nginx) | http://localhost |
| API docs | http://localhost:8000/docs |

### OAuth setup

Register redirect URIs in your OAuth providers (use the URL your browser accesses — `localhost:8080` via SSH tunnel, or public DNS):

| Provider | Redirect URI |
|-|-|
| Google | `<FRONTEND_URL>/api/auth/google/callback` |
| Microsoft | `<FRONTEND_URL>/api/auth/microsoft/callback` |

Add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID` to `.env`.

Note: Google OAuth does not accept private IPs (172.16.x.x). For remote hypervisors, use an SSH tunnel:
```bash
ssh -L 8080:localhost:80 user@hypervisor-ip -N
# Then access http://localhost:8080 and set FRONTEND_URL/BACKEND_URL accordingly
```

### Auth hardening notes

- Existing tenant domains require an admin invite or pre-provisioned user record; public self-registration only creates a tenant for new domains.
- OAuth login callbacks return short-lived exchange codes instead of bearer JWTs in redirect URLs.
- Microsoft/Google integration OAuth state is bound to the initiating user, tenant, intent, and role.
- `TOKEN_ENCRYPTION_KEY` must be a stable Fernet key before storing OAuth tokens.
- Backend-side limits protect login, registration, forgot-password, and reset-password endpoints even when nginx is bypassed.

---

## Database migrations

| Migration | Description |
|-|-|
| `001_initial_schema` | Core tables, pgvector index, RLS |
| `002_plugins` | Practice profiles, matters, matter events, renewals |
| `003_scheduler` | Scheduler logs |
| `004_audit_log` | Audit columns on usage_records |
| `005`–`007` | Platform admin, MCP keys, model settings |
| `008_estate_mediation` | Trust & Estate + Mediation tables and routes |
| `009_oauth_tokens` | Encrypted tenant/user OAuth token persistence |
| `010_enhance_user_model` | User preferences (practice_areas, expertise_level, default_skill, privacy_mode, memory_summary) |
| `011_create_user_memory_table` | Per-user memory storage (preferences, expertise, matter context, interaction patterns) |
| `012_extend_message_context_tracking` | Message enhancements (skill_applied, context_used, context_relevance_scores, pii_flags) |
| `013_add_cache_tracking` | UsageRecord cache hit flags (RAG, LLM, matter) |
| `014_create_tenant_settings` | Tenant feature flags, cache config, rate limiting, defaults |
| `015_create_billing_tables` | Billing core: TimeEntry, Expense, Invoice, InvoiceLineItem, Payment tables with RLS |
| `016_create_qbo_integration` | QuickBooks Online integration tables |
| `017_create_trust_accounting` | Trust accounting (IOLTA) tables |
| `018_create_contacts` | Contacts + leads tables with RLS; client_contact_id FK on matters |
| `019_create_tasks` | Task management with matter/contact linking, priorities, deadlines |
| `020_create_communications_leads` | Communication logs + lead intake pipeline tables |
| `021_create_matter_parties` | Multi-party matter support (M:N matter↔contact with roles) |
| `021_create_prompt_overrides` | Per-tenant prompt customization (plugin_name + skill_name + prompt_content) |
| `022_create_matter_documents` | Case file attachments (separate from RAG document store) |
| `023_add_task_reminder_sent_at` | Dedup column for hourly task reminder emails |
| `024_add_matter_budget` | Budget tracking (budget_amount, budget_currency on matters) |
| `025_create_document_templates` | Reusable document templates with variable substitution |
| `026_matter_revamp` | Matter assignments, notes, retainers; expands matters with practice_area, billing_cycle, billing_method, hourly_rate, contingency_percentage, tax_rate, court, judge, case_number; adds default_billing_rate to users; data migration from internal_owners JSON to matter_assignments rows |

### CourtListener public RAG

CourtListener data is staged in `public_chunks` and embedded on one local NVIDIA Jetson connected to PostgreSQL over the same network.

```bash
python scripts/ingest_courtlistener.py --file /data/courtlistener/opinions.json.gz --batch-size 1000
python scripts/jetson_embed_worker.py --worker-id 0 --total-workers 1 --batch-size 64 --db-url "$DATABASE_URL" --loop
psql "$DATABASE_URL" -f scripts/create_public_chunks_index.sql
```

Full operator notes are in `scripts/courtlistener_jetson_pipeline.md`.

---

## Project structure

```
legalapp/
├── backend/
│   ├── app/
│   │   ├── models/          # SQLAlchemy models (22 files): User, UserMemory, Message, Tenant, TenantSettings, ErrorLog, Contact, Lead, Task, CommunicationLog, Matter (+ events, documents, parties, assignments, notes), Retainer, Billing (TimeEntry, Expense, Invoice, InvoiceLineItem, Payment), PromptOverride, DocumentTemplate, QBO, TrustAccounting, OAuth tokens
│   │   ├── routers/         # FastAPI routers (28 files): auth, chat, documents, plugins, admin, billing, billing_extended, mcp, platform, contacts, tasks, communications, intake, reports, calendar, templates, matters, matter_documents, matter_parties, prompt_admin, trust_accounting, qbo, integrations, document_sync, email_agent, user_sync, scheduler, dev
│   │   ├── services/        # LLM, RAG, embeddings, billing, scheduler
│   │   │   ├── plugins/     # 11 practice area prompts + executor
│   │   │   ├── pii_detection.py       # PII pattern matching (8 types) + scrubbing
│   │   │   ├── memory_service.py      # UserMemory CRUD + auto-summarization
│   │   │   ├── matter_context.py      # Matter loading with PII scrubbing
│   │   │   ├── matter_file_store.py   # Cloud storage routing (OneDrive, Google Drive, local fallback)
│   │   │   ├── recurring_billing.py   # Auto-invoice generation on billing cycles
│   │   │   ├── cache.py               # ExpertiseCacheManager (3-tier TTLs, skill multipliers)
│   │   │   └── conflict_check.py      # Cross-matter conflict detection
│   │   ├── schemas/         # Pydantic models (22 files): auth, admin, billing, matter, contacts, tasks, reports, calendar, document_templates, etc.
│   │   ├── middleware/      # Tenant context + rate limiter
│   │   └── main.py
│   ├── migrations/          # Alembic (001–026, 27 migrations)
│   └── scripts/
│       └── verify_matter_migration.py  # Post-migration integrity checker
├── frontend/
│   ├── src/
│   │   ├── pages/           # 33 page components
│   │   ├── components/      # Sidebar, ChatMessage, FileUpload, SkillOutput, ColdStartInterview, ContactPicker, legalMarkdown
│   │   └── assets/          # Homepage images
│   └── public/
├── nginx/
├── scripts/
├── docker-compose.yml
├── docker-compose.override.yml
├── docker-compose.hypervisor.yml
└── docker-compose.prod.yml
```

---

## License

Private — all rights reserved.
