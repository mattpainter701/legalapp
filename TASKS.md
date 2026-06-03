# TASKS.md

## Sprint 7 — Calendar, Communications & Matter Operations (v0.8.0)

**Goal:** Make the practice management layer truly operational — deadline calendar, full communications router, lead-to-matter conversion, matter budget tracking, and document templates.

### 801. Deadline Calendar (P0, MEDIUM)

- [ ] `GET /api/calendar/events` — aggregates task due_dates + matter key_dates JSON + renewal dates; returns `{date, title, type, matter_id, task_id}` list; supports `?start=&end=` date range
- [ ] CalendarPage.jsx — month/week calendar view; color-coded by event type; click → matter/task detail
- [ ] Sidebar nav link to `/calendar`

Files: `backend/app/routers/calendar.py`, `backend/app/schemas/calendar.py`, `frontend/src/pages/CalendarPage.jsx`

### 802. Communications Router (P0, MEDIUM)

- [ ] Full CRUD router for `communication_logs` table at `/api/communications`
- [ ] `GET /api/communications` — list with filter by matter_id, contact_id, channel, date range
- [ ] `POST /api/communications` — log a call, email, meeting, or note against a matter/contact
- [ ] `GET/PATCH/DELETE /api/communications/{id}` — detail, update, delete
- [ ] Frontend: CommunicationsPage.jsx — log list with filters; quick-log form (channel, subject, body, matter link)

Files: `backend/app/routers/communications.py` (extend or create), `backend/app/schemas/communication_log.py` (extend), `frontend/src/pages/CommunicationsPage.jsx`

### 803. Lead-to-Matter Conversion (P1, SMALL)

- [ ] `POST /api/intake/leads/{id}/convert` — creates a Matter from a Lead; links Lead.contact_id as Matter.client_contact_id; sets lead status = "matter_opened"; returns new MatterResponse
- [ ] Frontend: IntakePage "Convert to Matter" button on qualified/conflict-checked leads → opens confirm modal with matter_type select

Files: `backend/app/routers/intake.py` (add endpoint), `frontend/src/pages/IntakePage.jsx`

### 804. Matter Budget Tracking (P1, MEDIUM)

- [ ] Migration 024: add `budget_amount` (Numeric 12,2), `budget_currency` (String 3, default "USD") to matters table
- [ ] `GET /api/reports/matters/{id}/budget` — billable hours × default_rate vs budget_amount; % utilization
- [ ] Frontend: budget utilization badge in MatterDetailPage header; budget amount editable inline

Files: `backend/migrations/versions/024_add_matter_budget.py`, `backend/app/routers/reports.py` (add endpoint), `frontend/src/pages/MatterDetailPage.jsx`

### 805. Document Templates (P2, MEDIUM)

- [ ] `DocumentTemplate` model — title, body (Text, `{{variable}}` placeholders), category (engagement_letter/retainer/NDA/motion/other), is_active
- [ ] Migration 025: document_templates table + RLS
- [ ] `GET/POST /api/templates` — list/create templates
- [ ] `GET/PATCH/DELETE /api/templates/{id}` — detail, update, delete
- [ ] `POST /api/templates/{id}/render` — body with `{variables: {key: value}}` POST; returns rendered text; optionally creates a MatterDocument
- [ ] Frontend: TemplatesPage.jsx — template library list + create modal; "Generate" button with variable fill-in form

Files: `backend/app/models/document_template.py`, `backend/app/schemas/document_template.py`, `backend/app/routers/document_templates.py`, `backend/migrations/versions/025_create_document_templates.py`, `frontend/src/pages/TemplatesPage.jsx`

---

## Sprint 6 — Matters, Document Management & Firm Reporting (v0.7.0)

**Goal:** Deepen case management with multi-party matters, document storage linked to contacts/matters, automated conflict checking on matter create, task email reminders, and a reporting layer for matter status, intake funnel, and overdue tasks.

### 701. MatterParty — Multi-Party Matter Support (P0, MEDIUM) — COMPLETED
- [x] `MatterParty` SQLAlchemy model (matter_id, contact_id, role, is_primary, notes)
- [x] Migration 021: matter_parties table + RLS + indexes
- [x] Pydantic schemas: MatterPartyCreate/Update/Response/ListResponse
- [x] Router `/api/matters/{id}/parties`: list, add, update, remove — all tenant-scoped
- [x] Frontend: MatterDetailPage → Parties tab with role badges, add/remove form

Files: `backend/app/models/matter_party.py`, `backend/app/schemas/matter_party.py`, `backend/app/routers/matter_parties.py`, `backend/migrations/versions/021_create_matter_parties.py`

### 702. Document Management (P0, LARGE) — COMPLETED
- [x] `MatterDocument` SQLAlchemy model (matter_documents table — separate from RAG documents)
- [x] Migration 022: matter_documents table + RLS
- [x] File storage: local filesystem with path traversal protection (os.path.basename)
- [x] Router `/api/matters/{id}/documents`: list, upload, patch, delete, download (FileResponse)
- [x] Frontend: MatterDocumentsTab component + Documents tab in MatterDetailPage

Files: `backend/app/models/matter_document.py`, `backend/app/schemas/matter_document.py`, `backend/app/routers/matter_documents.py`, `backend/migrations/versions/022_create_matter_documents.py`

### 703. Conflict Check Auto-Run on Matter Create (P1, SMALL) — COMPLETED
- [x] Extracted conflict logic into `backend/app/services/conflict_check.py`
- [x] Hook `create_matter` in plugins.py to auto-run check; sets conflicts_status = "clear"/"conflict-found"
- [x] Manual re-check endpoint: `POST /api/plugins/litigation/matters/{id}/conflict-check`
- [x] Frontend: conflicts_status badge + Re-run Check button in MatterDetailPage

Files: `backend/app/services/conflict_check.py`, `backend/app/routers/plugins.py`, `backend/app/routers/contacts.py`

### 704. Task Email Reminders (P1, MEDIUM) — COMPLETED
- [x] `send_task_reminder` method added to email service
- [x] `_check_task_reminders` hourly APScheduler job — queries tasks due in 24h, sends per-assignee emails
- [x] `reminder_sent_at` column on tasks (migration 023) prevents duplicate hourly sends
- [x] `POST /api/tasks/{id}/remind` — manual immediate reminder trigger
- [x] Frontend: Bell icon remind button per task row in TasksPage

Files: `backend/app/services/scheduler.py`, `backend/app/services/email.py`, `backend/app/routers/tasks.py`, `backend/app/models/task.py`, `backend/migrations/versions/023_add_task_reminder_sent_at.py`

### 705. Reporting Endpoints (P1, MEDIUM) — COMPLETED
- [x] `GET /api/reports/matters` — count by status, matter_type, risk_level
- [x] `GET /api/reports/intake` — leads by status, conversion rate
- [x] `GET /api/reports/overdue-tasks` — overdue task list with matter names
- [x] `GET /api/reports/bundle` — all three reports combined
- [x] Frontend: ReportsPage with 3 summary cards; /reports route + Sidebar nav link

Files: `backend/app/routers/reports.py`, `backend/app/schemas/reports.py`, `frontend/src/pages/ReportsPage.jsx`

---

## Sprint 5 — CRM, Contacts, Tasks & Client Communication (v0.6.0) — COMPLETED

**Goal:** Build the practice management layer: Contact/Client data model, Task & Deadline tracking, Communication Log, Intake pipeline, and conflict check — closing the gap with Clio/Tabs3 on core CRM functionality.

### 601. Contact/Client Data Model (P0, LARGE) — COMPLETED
- [x] `Contact` SQLAlchemy model (person/org, contact_type, address JSON, tags)
- [x] `Lead` SQLAlchemy model (intake pipeline: new→contacted→qualified→conflict_checked→engaged→matter_opened|declined)
- [x] Migration 018: contacts table + RLS; add nullable `client_contact_id` FK to matters
- [x] Pydantic schemas: ContactCreate/Update/Response, ContactListResponse, ConflictCheckRequest/Result, LeadCreate/Update/Response, LeadConvertRequest
- [x] Router `/api/contacts`: list (search/filter), create, detail, update, soft-delete, get_matters, get_communications
- [x] `POST /api/contacts/conflict-check` — fuzzy name/email match against contacts + matter counterparty strings
- [x] QBO sync updated to use Contact.display_name when client_contact_id is set (fallback to counterparty string)

Files: `backend/app/models/contact.py`, `backend/app/schemas/contact.py`, `backend/app/routers/contacts.py`, `backend/migrations/versions/018_create_contacts.py`

### 602. Task & Deadline Management (P0, LARGE) — COMPLETED
- [x] `Task` SQLAlchemy model (task_type, status, priority, due_date, matter_id, contact_id, assigned_to_user_id, source)
- [x] Migration 019: tasks table + RLS + indexes
- [x] Pydantic schemas: TaskCreate/Update/Response, TaskListResponse
- [x] Router `/api/tasks`: list (filters: matter_id, status, priority, task_type, due range), create, detail, update (auto-sets completed_at), delete
- [x] `GET /api/tasks/overdue` — tasks past due date, not completed
- [x] `GET /api/tasks/upcoming?days=7` — tasks due in next N days

Files: `backend/app/models/task.py`, `backend/app/schemas/task.py`, `backend/app/routers/tasks.py`, `backend/migrations/versions/019_create_tasks.py`

### 603. Communication Log (P1, MEDIUM) — COMPLETED
- [x] `CommunicationLog` SQLAlchemy model (direction, channel, status, matter_id, contact_id, occurred_at, external_ref)
- [x] Migration 020 (combined with leads): communication_logs + leads tables + RLS
- [x] Pydantic schemas: CommunicationLogCreate/Update/Response/ListResponse
- [x] Router `/api/communications`: list (filter by matter/contact/channel/direction), create, detail, update
- [x] EmailAgent hook: auto-create CommunicationLog + Task (if deadline_mentioned) on each classified email

Files: `backend/app/models/communication_log.py`, `backend/app/schemas/communication_log.py`, `backend/app/routers/communications.py`, `backend/migrations/versions/020_create_communications_leads.py`, `backend/app/services/email_agent.py`

### 604. Intake Pipeline (P1, MEDIUM) — COMPLETED
- [x] Lead model included in contact.py (contact_id FK, status, source, conflict_check_status, matter_id conversion)
- [x] Router `/api/intake`: list (filter by status), create (+ inline Contact create), detail, update status, convert to Matter
- [x] `POST /api/intake/{id}/convert` — creates Matter with client_contact_id set, marks lead as matter_opened

Files: `backend/app/routers/intake.py`

### 605. Frontend: Contacts & CRM (P0, LARGE) — COMPLETED
- [x] `ContactsPage` — list/search with type/entity filters, inline create modal
- [x] `ContactDetailPage` — profile tabs: Profile | Matters | Communications | Tasks, inline edit
- [x] `ContactPicker` reusable autocomplete component
- [x] Routes: `/contacts`, `/contacts/:id`
- [x] Sidebar nav links: Contacts, Tasks, Intake

### 606. Frontend: Tasks & Intake (P1, MEDIUM) — COMPLETED
- [x] `TasksPage` — grouped list: Overdue / Today / Upcoming / No Due Date / Completed; create modal with ContactPicker
- [x] `IntakePage` — pipeline kanban with stage counters, advance/convert actions
- [x] Routes: `/tasks`, `/intake`
- [x] api.js: all contact, task, communication, intake API functions

### Backlog (from Sprint 4)
- [ ] P3-2: Clio marketplace listing + API integration
- [ ] P3-3: Clio data migration tool
- [ ] P3-4: Tabs3 data migration tool
- [ ] P3-5: LEDES XML 2.1 export
- [ ] P3-6: QBD via unified API partner (Unified.to / Apideck)

---

## Sprint 1 — Billing & QBO Integration Foundation (v0.5.0) — COMPLETED

**Goal:** Build core billing models (time tracking, expenses, invoices, payments), QBO OAuth2 integration, trust accounting foundations, Stripe payments, LEDES export.

### 501. Billing Models (P0, LARGE) — COMPLETED
- [x] TimeEntry, Expense, Invoice, InvoiceLineItem, Payment SQLAlchemy models
- [x] 23 billing Pydantic schemas (Create/Update/Response + list/exports)
- [x] Migration 015 for billing tables with RLS policies
- [x] Migration 016 for qbo_integrations table
- [x] Migration 017 for trust_accounts + trust_transactions tables
- [x] Wire models, schemas, QBO config into __init__.py and config.py

### 502. QBO OAuth2 Connect + Time Tracking CRUD (P0, LARGE) — COMPLETED
- [x] QBO OAuth2 connect/callback/disconnect/status endpoints
- [x] QBOSyncService — Customer sync, TimeActivity sync, Invoice sync, Payment sync
- [x] QBO sync with token refresh, sandbox/production toggle, sync_all()
- [x] TimeEntry CRUD (create, list by matter/status/unbilled, edit, soft-delete)
- [x] Expense CRUD (create, list by matter/category/unbilled, edit, delete)
- [x] Invoice generation from unbilled time+expenses with auto-numbering
- [x] Invoice CRUD (list, detail with line items/payments, status transitions)
- [x] Payment endpoints (record payment, list by invoice, auto status update)
- [x] Stripe Payment Link generation on invoice
- [x] Invoice export (CSV + LEDES 1998B formats)
- [x] LEDES 1998B export service with UTBMS task/activity code maps

### 503. Invoice Generation + Stripe Payments (P0, LARGE) — COMPLETED
- [x] Invoice generation from unbilled time+expenses
- [x] Invoice CRUD endpoints
- [x] Stripe Payment Link generation on invoice
- [x] Payment endpoints
- [x] CSV invoice export (P1)
- [x] PDF invoice export (P1)

### 504. Legal Billing Compliance (P1, MEDIUM) — COMPLETED
- [x] LEDES 1998B export service
- [x] UTBMS task/activity code mapping
- [x] Trust accounting CRUD + three-way reconciliation endpoint

## Sprint 2 — Webhooks, QBO Push Sync & Error Tracking (v0.5.2) — COMPLETED

**Goal:** Close the billing loop — Stripe webhook for auto-reconciliation, QBO push sync on invoice/ payment events, error logging admin endpoints + capture middleware.

### 507. Stripe Webhook Handler (P0, SMALL) — COMPLETED
- [x] `POST /api/billing/webhooks/stripe` — verify Stripe signature, handle `payment_intent.succeeded` → auto-create Payment + update invoice status
- [x] Handle `payment_intent.payment_failed` → log, optionally mark invoice for follow-up
- [x] Handle `checkout.session.completed` → reconcile Payment Link checkout against invoice
- [x] Idempotency: skip duplicate events via `stripe_payment_intent_id` lookup on Payment table

Files: `backend/app/routers/billing_extended.py` (+webhook endpoint)

### 508. QBO Auto-Push Sync (P0, MEDIUM) — COMPLETED
- [x] Trigger `QBOSyncService.sync_invoice()` on invoice status change (draft→sent, sent→paid)
- [x] Trigger `QBOSyncService.sync_payment()` on payment create
- [x] Background sync queue — fire-and-forget via `asyncio.create_task()`, log failures to ErrorLog
- [x] Sync retry on failure — exponential backoff, max 3 attempts
- [x] Invoice qbo_sync_status lifecycle: pending→syncing→synced | failed

Files: `backend/app/routers/billing_extended.py` (hook into invoice update + payment create), `backend/app/services/qbo_sync.py` (+retry logic)

### 509. Error Log Admin Endpoints (P1, MEDIUM) — COMPLETED
- [x] `GET /admin/errors/user/{user_id}?days=3&severity=error` — Per-user 72h rolling error logs
- [x] `GET /admin/errors/system?days=7&severity=error` — System-level errors with optional filters
- [x] `GET /admin/errors/summary?days=30` — Error counts by severity/type, trend data (daily buckets)
- [x] `PATCH /admin/errors/{error_id}/resolve` — Mark error resolved with notes
- [x] All endpoints tenant-scoped + admin-only

Files: `backend/app/routers/admin.py` (+error endpoints), `backend/app/schemas/admin.py` (+error response schemas)

### 510. Error Capture Middleware (P1, MEDIUM) — COMPLETED
- [x] ErrorLog capture in `generic_exception_handler` (500s already caught — just persist)
- [x] ErrorLog capture in `http_exception_handler` (400, 401, 403, 404 — record with severity mapping)
- [x] ErrorLog capture in chat endpoint (RAG failures, LLM timeouts, cache errors)
- [x] Request context capture: endpoint, method, status_code, user_id, tenant_id, IP, user_agent
- [x] 72h rolling window — ErrorLog model already has composite indexes for this

Files: `backend/app/main.py` (exception handlers), `backend/app/routers/chat.py` (error capture), `backend/app/services/error_tracker.py` (NEW — helper)

## Sprint 3 — Trust Accounting + PDF Export (v0.5.1) — COMPLETED

**Goal:** Trust accounting CRUD, three-way reconciliation, PDF invoice export.

### 505. Trust Accounting Endpoints (P1, MEDIUM) — COMPLETED
- [x] TrustAccount CRUD (create, get, list by matter, update, close)
- [x] TrustTransaction endpoints (create deposit/disbursement/transfer, list by account)
- [x] Three-way reconciliation endpoint (bank balance vs trust liability vs unallocated)
- [x] Reconciliation report endpoint

Files: `backend/app/schemas/trust_accounting.py`, `backend/app/routers/trust_accounting.py`

### 506. PDF Invoice Export (P1, SMALL) — COMPLETED
- [x] Invoice PDF generation service (professional legal invoice layout via ReportLab)
- [x] PDF support in export endpoint

Files: `backend/app/services/invoice_pdf.py`

### Backlog (P3)
- [ ] P3-1: QBD migration path (CSV import for firms moving to QBO)

## Sprint 4 — Security & Bug Fixes (v0.5.2) — COMPLETED

### 511. Critical Bug Fixes (P0, MEDIUM) — COMPLETED
- [x] Fix SQL injection in QBO sync queries (escape single quotes in display_name, item_name, customer_name)
- [x] Add `set_tenant_context` to all billing list endpoints (time entries, expenses, invoices, payments) for RLS correctness
- [x] Fix delete_time_entry to hard-delete unbilled entries (was soft-deleting with wrong 204 status)
- [x] Fix unbounded QBO OAuth fallback state dicts (add TTL-based eviction on write)
- [x] Fix cache invalidation key-pattern mismatch (`invalidate_user_cache` pattern now matches actual key format)
- [x] Tighten PII detection regexes (driver_license: require 9+ digits; bank_account: use lookahead to exclude phone-like sequences)

### 512. Sprint 2 Code Audit Fixes (P0, MEDIUM) — COMPLETED
- [x] Add missing `import asyncio` and `async_session_maker` to billing_extended.py (QBO sync was broken)
- [x] Fix SQL injection in rag.py — parameterized embedding vector in pgvector queries
- [x] Add logging to silent `except Exception: pass` in QBO sync fire-and-forget tasks
- [x] Add missing error schema imports in admin.py (ErrorLogResponse, SystemErrorLogsResponse, etc.)
- [x] Add try/except error handling to `_trigger_auto_memory_generation` in chat.py
- [ ] P3-2: Clio marketplace listing + API integration
- [ ] P3-3: Clio data migration tool
- [ ] P3-4: Tabs3 data migration tool
- [ ] P3-5: LEDES XML 2.1 export
- [ ] P3-6: QBD via unified API partner (Unified.to / Apideck)

## Completed

### Enhanced User Model, Context Management & Error Logging (PR: v0.4.0)
- [x] Phase 1: Enhanced User Model — practice_areas, expertise_level, default_skill, privacy_mode, memory_summary, last_memory_update (migration 010)
- [x] Phase 2: UserMemory Model — type-based memory storage (preference/expertise/matter_context/interaction_pattern) with confidence scoring (migration 011)
- [x] Phase 3: PII Detection & Scrubbing — 8 PII types (SSN, credit card, phone, email, IP, passport, driver's license, bank account) with detection and scrubbing service
- [x] Phase 4: Context Usage Tracking — Message model extended with context_used, context_relevance_scores, skill_applied, pii_flags (migration 012)
- [x] Phase 5: Skill-Based Chat Routing — Chat endpoint with skill/matter routing and context consolidation
- [x] Phase 6: Auto-Memory Generation — MemoryService with LLM-based conversation summarization (every 10 messages)
- [x] Phase 7: PII-Safe Matter Context — MatterContextService with PII scrubbing and privacy mode support
- [x] Expertise-Aware Caching — ExpertiseCacheManager with 3-tier TTLs (junior/mid/senior) and skill-based multipliers
- [x] Tenant Settings & Feature Flags — TenantSettings model with per-tenant cache config, rate limiting, and feature flags (migration 014)
- [x] Enhanced Admin Console — Full tenant drill-down with analytics, user detail endpoint, cache analytics
- [x] Error Logging Foundation — ErrorLog model for per-user 72h rolling logs and system-level error tracking (migration 015)
- [x] Admin Error Log Schemas — ErrorLogResponse, UserErrorLogsResponse, SystemErrorLogsResponse, ErrorSummaryResponse

## Completed

### CourtListener Public RAG
- [x] Align CourtListener ingest, Jetson embedding, and RAG search around `public_chunks` BGE-384 vectors

### M365 + Google Workspace Integration
- [x] Phase 1: OAuth token persistence (tenant/user token tables, Fernet encryption, token vault, integration connect/disconnect/status API)
- [x] Phase 2: Email agentic pipeline + Calendar sync (M365/Google mail read, LLM classification + draft responses, calendar read/write + deadline sync)
- [x] Phase 3: Document sync for RAG (OneDrive, SharePoint, Google Drive listing + download + ingest into RAG pipeline)
- [x] Phase 4: Gemini + Azure OpenAI LLM providers (added to LLMService with provider routing)
- [x] Phase 5: Admin user sync dashboard (M365/Google Workspace user import via Directory API)

### Auth System
- [x] Add password_hash to User model (005 migration)
- [x] Add company fields to Tenant model (005 migration)
- [x] POST /auth/register endpoint (email/password + company details)
- [x] POST /auth/login endpoint (email/password with bcrypt)
- [x] POST /auth/forgot-password endpoint (reset token generation)
- [x] POST /auth/reset-password endpoint (token + new password)
- [x] Login: is_active check
- [x] Login: scope query by created_at desc + limit(1)
- [x] Registration: reuse existing domain tenant
- [x] JWT: add iat and jti claims
- [x] Logout: token blacklist via Redis (fallback in-process dict)

### Frontend
- [x] SignupPage with company details form
- [x] LoginPage with email/password + forgot password link
- [x] ForgotPasswordPage (token display in dev mode)
- [x] ResetPasswordPage (token + new password form)
- [x] App.jsx routes: /signup, /forgot-password, /reset-password
- [x] api.js: register, login, forgotPassword, resetPassword functions

### Infrastructure
- [x] Add no-bind local Docker Compose mode for engines that cannot mount Windows workspaces
- [x] Fix Sidebar: Array.isArray guards for documents/conversations
- [x] Fix passlib→bcrypt in requirements.txt
- [x] Fix reset token visibility (DEV_MODE check)
- [x] Fix TTL garbage collection for fallback dicts
- [x] Fix CORS origins for hypervisor IP
- [x] Add healthchecks to docker-compose (postgres, redis, backend, frontend)
- [x] Production frontend Dockerfile (multi-stage build + serve)
- [x] Nginx proxy for /health, /docs, /openapi.json, /redoc
- [x] Deploy to hypervisor (172.16.16.202)

### Security
- [x] Harden auth review findings: tenant join controls, OAuth callbacks, token vault, rate limits
- [x] Regenerate SECRET_KEY on hypervisor
- [x] Set DEV_MODE=false on hypervisor
- [x] Remove credentials from .env
- [x] Set FRONTEND_URL/BACKEND_URL correctly
- [x] PRIMARY_LLM→deepseek-chat

## Pending

### Error Logging Integration (follow-up to v0.4.0)
- [ ] Create admin endpoints for error log querying:
  - [ ] `GET /admin/errors/user/{user_id}?days=3` — Per-user 72-hour rolling error logs
  - [ ] `GET /admin/errors/system?days=3&severity=error` — System-level errors with optional filters
  - [ ] `GET /admin/errors/summary` — Error counts by severity/type, trend data
  - [ ] `PATCH /admin/errors/{error_id}/resolve` — Mark error resolved with notes
- [ ] Implement error capture middleware/service in main.py
- [ ] Wire ErrorLog into exception handlers (400, 404, 500, unhandled exceptions)
- [ ] Add error logging to chat endpoint for RAG/LLM/cache failures

### Enhancements
- [ ] Email verification on registration (requires SMTP)
- [x] Rate limiting on auth endpoints
- [ ] OAuth provider credential setup (Google, Microsoft)
- [ ] Email notifications for password reset (currently dev-mode only)
- [ ] User interface for setting expertise_level and practice_areas
- [ ] User interface for privacy_mode toggle
- [ ] User memory dashboard (view learned preferences + interaction patterns)
- [ ] Admin console: view/delete UserMemory entries per user

### Future
- [ ] Production static file serving (nginx directly serves Vite dist)
- [ ] Backup strategy for postgres
- [ ] Monitoring / observability (error log dashboards, alerting)
- [ ] CI/CD pipeline
- [ ] HTTPS certificate automation (Let's Encrypt)
