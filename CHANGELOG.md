# Changelog

## [0.8.0] — 2026-06-03

### Sprint 7 — Calendar, Communications & Matter Operations

### Added

#### Task 801 — Deadline Calendar
- `GET /api/calendar/events` endpoint aggregating task due_dates, matter key_dates, and renewal dates with `?start=&end=` range filter
- CalendarPage.jsx — month/week calendar view with color-coded events by type; click to navigate to matter/task detail

#### Task 802 — Communications Router
- Full CRUD router for `communication_logs` at `/api/communications` with filters by matter_id, contact_id, channel, date range
- CommunicationsPage.jsx — log list with filters and quick-log form (channel, subject, body, matter link)

#### Task 803 — Lead-to-Matter Conversion
- `POST /api/intake/leads/{id}/convert` — creates a Matter from a qualified Lead; sets `client_contact_id` from lead's contact; marks lead `status = matter_opened`; returns `{matter_id, matter_name, lead_id, status}`
- `LeadConvertRequest` schema (matter_name, matter_type, role, jurisdiction, counterparty)
- IntakePage: "Convert to Matter" button on engaged leads; modal with all required Matter fields; navigates to new matter on success
- `convertLead(id, data)` API helper in `frontend/src/api.js`

### Changed

### Fixed

### Tests

---

## [0.7.0] — 2026-06-03

### Sprint 6 — Matters, Document Management & Firm Reporting

### Added

#### MatterParty — Multi-Party Matter Support (701)
- `MatterParty` model — M:N link between matters and contacts with role (client/opposing_party/counsel/witness/expert/other), is_primary flag, notes
- Migration 021: `matter_parties` table with RLS tenant isolation
- `GET/POST /api/matters/{id}/parties` — list and add parties to a matter
- `PATCH/DELETE /api/matters/{id}/parties/{party_id}` — update role/notes, remove party
- Frontend: Parties tab in MatterDetailPage with role badges, add/remove form, contact dropdown

#### MatterDocument — Case File Attachments (702)
- `MatterDocument` model — file attachments linked to matters (separate from RAG document store)
- Migration 022: `matter_documents` table with RLS tenant isolation
- `POST /api/matters/{id}/documents/upload` — multipart file upload (50MB limit) with path traversal protection
- `GET/PATCH/DELETE /api/matters/{id}/documents/{doc_id}` — list, update metadata, delete
- `GET /api/matters/{id}/documents/{doc_id}/download` — FileResponse download
- Frontend: MatterDocumentsTab component with upload form, category badges (pleading/contract/evidence/correspondence/other), inline edit, download

#### Conflict Check Service (703)
- `backend/app/services/conflict_check.py` — shared conflict check service extracted from contacts router
- Auto-runs on matter create: sets `conflicts_status` ("not-run"/"clear"/"conflict-found") automatically
- `POST /api/plugins/litigation/matters/{id}/conflict-check` — manual re-run endpoint
- Frontend: conflicts_status badge + Re-run Check button in MatterDetailPage with match list display

#### Task Email Reminders (704)
- `send_task_reminder()` method in email service with HTML + plaintext body
- `_check_task_reminders` hourly APScheduler job — queries tasks due within 24h, sends per-assignee reminders
- Migration 023: `reminder_sent_at` column on tasks prevents duplicate hourly sends (23h cooldown)
- `POST /api/tasks/{task_id}/remind` — manual reminder trigger (202 Accepted)
- Frontend: Bell icon remind button per task row with inline "Sent!" confirmation

#### Firm Reporting (705)
- `GET /api/reports/matters` — matter counts by status, matter_type, risk_level
- `GET /api/reports/intake` — lead counts by status + conversion rate (matter_opened / total)
- `GET /api/reports/overdue-tasks` — overdue tasks with matter context
- `GET /api/reports/bundle` — all three reports in one request
- Frontend: `/reports` route, Sidebar nav link, ReportsPage with 3 summary cards

### Changed
- `contacts.py` conflict_check endpoint now delegates to shared `conflict_check` service (behavior unchanged)
- MatterDetailPage extended with Parties tab, Documents tab, conflict status badge

### Fixed
- Missing `matter_parties_router`, `matter_documents_router`, `reports_router` imports in `main.py`
- RLS policy in migration 021 corrected to use `app.current_tenant_id` (matching the app's `set_tenant_context`)
- Path traversal vulnerability in document upload fixed with `os.path.basename(filename)`
- `conflicts_status` value standardized to "conflict-found" (was "flagged" in initial implementation)

### Tests
- Integration: all new endpoints verified with tenant isolation checks via spec/quality review cycle

---

## [0.6.0] — 2026-06-03

### Added — CRM, Contacts, Tasks & Client Communication

#### Contact/Client Data Model
- `Contact` model — person or organization with entity_type, contact_type (client/opposing_party/witness/expert/vendor/referral/other), email, phone, address (JSON), tags, soft-delete
- `Lead` model — intake pipeline with status lifecycle (new→contacted→qualified→conflict_checked→engaged→matter_opened|declined), source, conflict_check_status, estimated_value
- Migration 018: `contacts` table with RLS; nullable `client_contact_id` FK added to `matters`
- `GET /api/contacts` — list with search (`q=`), contact_type/entity_type filters
- `POST /api/contacts` — create person or organization
- `GET/PATCH /api/contacts/{id}` — detail + inline edit
- `DELETE /api/contacts/{id}` — soft-delete (sets is_active=False)
- `GET /api/contacts/{id}/matters` — linked matters via client_contact_id
- `GET /api/contacts/{id}/communications` — communication history for contact
- `POST /api/contacts/conflict-check` — fuzzy name/email match against contacts + matter counterparty strings; returns clear/matches with matter linkage
- QBO sync: uses `Contact.display_name` when matter has `client_contact_id` set (fallback to `counterparty` string)

#### Task & Deadline Management
- `Task` model — task_type (deadline/hearing/filing/deposition/call/follow_up/review/general), status (pending/in_progress/completed/cancelled), priority (low/medium/high/urgent), due_date, matter_id, contact_id, assigned_to, source (manual/email_agent/calendar_sync)
- Migration 019: `tasks` table with RLS + performance indexes
- `GET /api/tasks` — list with filters: matter_id, contact_id, assigned_to, status, priority, task_type, due_before/after
- `POST /api/tasks` — create task
- `PATCH /api/tasks/{id}` — update; auto-sets `completed_at` on status→completed
- `GET /api/tasks/overdue` — tasks past due date, not completed/cancelled
- `GET /api/tasks/upcoming?days=7` — tasks due in next N days

#### Communication Log
- `CommunicationLog` model — direction (inbound/outbound), channel (email/call/letter/meeting/portal/sms/other), subject, summary, matter_id, contact_id, occurred_at, external_ref
- Migration 020: `communication_logs` + `leads` tables with RLS
- `GET /api/communications` — list with filters: matter_id, contact_id, channel, direction, occurred_after
- `POST /api/communications` — log entry
- `PATCH /api/communications/{id}` — update

#### Intake Pipeline
- `GET /api/intake` — list leads (filter by status, assigned_to, practice_area)
- `POST /api/intake` — create lead with inline Contact creation if needed
- `PATCH /api/intake/{id}` — update status/notes
- `POST /api/intake/{id}/convert` — convert to Matter (creates Matter with client_contact_id, marks lead as matter_opened)

#### Email Agent Integration
- Auto-create `CommunicationLog` (inbound/email/received) for each classified email
- Auto-create `Task` (type=deadline, source=email_agent) when classification returns `deadline_mentioned`
- Date parsing via `python-dateutil` with fuzzy parsing

#### Frontend
- `ContactsPage` (`/contacts`) — list/search contacts with type/entity filters, quick-create modal
- `ContactDetailPage` (`/contacts/:id`) — tabs: Profile | Matters | Communications | Tasks; inline edit
- `ContactPicker` component — search-as-you-type autocomplete for linking contacts in forms
- `TasksPage` (`/tasks`) — grouped sections: Overdue / Due Today / Upcoming / No Due Date / Completed; create modal with ContactPicker; filter by status/priority/type
- `IntakePage` (`/intake`) — pipeline view with stage counters; advance/convert actions; convert-to-matter modal
- Sidebar: added Contacts, Tasks, Intake nav links

### Changed
- `backend/app/models/plugin.py` — added nullable `client_contact_id` FK to `Matter`
- `backend/app/services/qbo_sync.py` — prefer Contact name over counterparty string when available
- `backend/app/services/email_agent.py` — auto-log communications and tasks on email classification
- `backend/requirements.txt` — added `python-dateutil==2.9.0`
- `frontend/src/api.js` — added 20 new API functions for contacts, tasks, communications, intake

## [0.5.2] — 2026-06-02

### Fixed — Security & Bug Fixes

#### Critical Bug Fixes
- `app/services/qbo_sync.py` — SQL injection in QBO query strings: escape single quotes in display_name, item_name, and customer_name via `_safe_qbo_string()` helper
- `app/routers/billing_extended.py` — Added `set_tenant_context()` to all 4 list endpoints (time entries, expenses, invoices, payments) for RLS correctness
- `app/routers/billing_extended.py` — `delete_time_entry` now hard-deletes unbilled entries (was incorrectly soft-deleting with `status=written_off` while returning 204)
- `app/routers/qbo.py` — QBO OAuth fallback state dicts now evict expired entries on each write to prevent unbounded memory growth
- `app/services/cache.py` — Fixed `invalidate_user_cache` key-pattern to match actual key format (`{type}:{tenant_id}|{user_id}|{suffix}`)
- `app/services/pii_detection.py` — Tightened `driver_license` regex (requires 9+ digits after letters) and `bank_account` regex (lookahead/behind to reduce false positives on phone numbers)

#### Sprint 2 Audit Fixes
- `app/routers/billing_extended.py` — Added missing `import asyncio` and `async_session_maker` (QBO sync fire-and-forget was broken at runtime)
- `app/services/rag.py` — Fixed SQL injection in pgvector queries: embedding vectors now passed as bind parameters instead of f-string interpolation
- `app/routers/billing_extended.py` — Added `logger.warning()` to silent `except Exception: pass` blocks in QBO sync tasks
- `app/routers/admin.py` — Added missing error schema imports (`ErrorLogResponse`, `SystemErrorLogsResponse`, `ErrorResolveRequest`, etc.)
- `app/routers/chat.py` — Wrapped `_trigger_auto_memory_generation` in try/except to prevent memory failures from breaking chat responses

## [0.5.1] — 2026-06-02

### Added — Trust Accounting + PDF Export

#### Trust Accounting CRUD
- `TrustAccount` CRUD endpoints (`POST/GET/PATCH /api/trust/accounts`, `POST /api/trust/accounts/{id}/close`)
- `TrustTransaction` endpoints (`POST/GET /api/trust/transactions`) with balance tracking
- Three-way IOLTA reconciliation (`POST /api/trust/accounts/{id}/reconcile`)
  - Bank balance vs trust liability vs unallocated funds
  - Auto-marks transactions as reconciled when balanced
  - Outstanding deposits/disbursements tracking
  - Reconciliation status endpoint (`GET /api/trust/accounts/{id}/reconciliation`)
- `TrustAccountCreate/Update/Response`, `TrustTransactionCreate/Response` Pydantic schemas
- `ReconciliationRequest/Response` with reconciling items detail
- `backend/app/routers/trust_accounting.py` — 8 endpoints
- `backend/app/schemas/trust_accounting.py` — 11 schemas

#### PDF Invoice Export
- `InvoicePDFService` — professional legal invoice PDF generation via ReportLab
- Clean letterhead layout: firm name, invoice details grid, line items table with totals, payments section, balance due
- `POST /api/billing/invoices/{id}/export` format=pdf returns `application/pdf`

### Changed
- `app/routers/__init__.py` — added trust_accounting_router
- `app/services/__init__.py` — added generate_invoice_pdf
- `app/main.py` — wired trust_accounting_router
- `requirements.txt` — added reportlab==4.2.5

## [0.5.0] — 2026-06-01

### Added — Billing & QBO Integration Foundation

#### Core Billing Models
- `TimeEntry` — billable time with matter link, UTBMS task/activity codes, status lifecycle (draft→billed→written_off)
- `Expense` — disbursements with category tracking (filing fees, court reporter, expert witness, etc.)
- `Invoice` — auto-numbered (INV-YYYY-XXXXXX), Stripe payment link, QBO sync status, LEDES export tracking
- `InvoiceLineItem` — polymorphic source tracking (time_entry/expense/flat_fee/adjustment/discount)
- `Payment` — multi-method (stripe/check/wire/trust_account/cash/other) with QBO sync
- 23 Pydantic v2 schemas in `schemas/billing.py`
- Migration 015: billing tables with RLS policies

#### QBO Integration
- `QBOIntegration` model — per-tenant QBO OAuth2 tokens (Fernet AES-256-GCM encryption, same pattern as TenantCredential)
- Full OAuth2 flow: `GET /api/integrations/qbo/connect` → callback → token exchange + encrypted storage
- Token refresh with refresh_token grant, sandbox/production toggle
- State-based CSRF protection with Redis fallback
- `QBOSyncService` — Matter→QBO Customer, TimeEntry→TimeActivity, Invoice→Invoice, Payment→Payment sync
- Migration 016: qbo_integrations table with RLS

#### Time Tracking & Billing CRUD
- TimeEntry CRUD: create, list (by matter/status/unbilled), detail, edit, soft-delete
- Expense CRUD: create, list (by matter/category/unbilled), detail, edit, delete
- Invoice generation: gather unbilled time+expenses → compute line items → auto-number → link sources
- Invoice CRUD: list, detail (with line items + payments), status transitions
- Payment recording with auto invoice status update (paid/partially_paid)
- Stripe Payment Link generation on invoice

#### Legal Billing Compliance
- LEDES 1998B pipe-delimited export (24-field format, full UTBMS task/activity code maps)
- Litigation (L100-L220), Counseling (C100-C800), Project (P100-P500), Bankruptcy (B100-B190) codes
- CSV invoice export

#### Trust Accounting Foundations
- `TrustAccount` model — per-matter IOLTA accounts with auto-replenish support
- `TrustTransaction` model — deposit/disbursement/transfer/replenishment/fee/adjustment types
- Migration 017: trust_accounts + trust_transactions tables with RLS

### Changed
- `app/config.py` — added QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REDIRECT_URI, QBO_ENVIRONMENT, QBO_WEBHOOK_VERIFIER
- `app/models/__init__.py` — registered 8 new models
- `app/schemas/__init__.py` — registered 28 new schemas
- `app/routers/__init__.py` — registered qbo_router, billing_extended_router
- `app/services/__init__.py` — registered QBOSyncService, export_ledes_1998b
- `app/main.py` — wired qbo_router, billing_extended_router

## [0.4.0] — 2026-06-02

### Added - Enhanced User Model & Context Management

#### User Preferences & Expertise Tracking
- `User.practice_areas` — JSON array of legal specializations (commercial, litigation, privacy, employment, product, IP, AI governance, regulatory, trust & estate, mediation)
- `User.expertise_level` — Proficiency classification: "junior", "mid", "senior" (drives cache TTLs and response complexity)
- `User.default_skill` — Preferred plugin/skill for routing (stored on user profile)
- `User.privacy_mode` — Strict PII handling flag (affects context injection and scrubbing)
- `User.memory_summary` — Auto-generated summary of user interactions and preferences
- `User.last_memory_update` — Timestamp for memory freshness tracking
- Migration 010: Add columns to `users` table with sensible defaults; index on practice_areas

#### Per-User Memory & Interaction Context
- `UserMemory` model with type-based storage:
  - `memory_type`: "preference" (user-set), "expertise" (observed), "matter_context" (case-specific), "interaction_pattern" (learned behavior)
  - `key` / `value` — Flexible key-value store (e.g., `preferred_rag_source_type`, `client_X_context`)
  - `confidence` — Relevance score 0–1 (how certain we are about this memory)
  - Timestamps and tenant/user isolation
- Migration 011: Create `user_memory` table with RLS
- `MemoryService` — CRUD ops + auto-summarization via LLM
- Auto-memory trigger: every 10 messages → `summarize_conversation()` → extract key facts/decisions → store as interaction_pattern
- Update `User.memory_summary` after each summary

#### PII Detection & Scrubbing
- 8 PII pattern types: SSN, credit card, phone, email, IP address, passport, driver's license, bank account
- Input scanning: detect PII in user messages before RAG query
- Output scrubbing: mask PII in assistant responses while preserving intent (e.g., "[MASKED_SSN]" instead of actual SSN)
- `PII Detection Service` (`services/pii_detection.py`):
  - `detect_pii(text: str)` — Returns list of {type, location, confidence}
  - `scrub_pii(text: str)` — Replaces with placeholders
  - `assess_pii_risk(text: str)` — Returns "low" | "medium" | "high"
- Guardrails integration: `apply_guardrails()` now returns `(cleaned_text, needs_retry, pii_findings)`
- Conversation flagging: Message.pii_flags stores detected PII metadata for audit
- User opt-in: privacy_mode=true enables stricter scrubbing

#### Explicit Context Usage Tracking
- Extended `Message` model:
  - `context_used` — JSON array of source IDs (document chunks, precedents, regulations) used in response
  - `context_relevance_scores` — Dict mapping source_id → relevance score (0–1)
  - `skill_applied` — Which plugin/skill was active for this message
  - `pii_flags` — Array of detected PII with type and confidence
- Chat response footer: **"### Sources & Context"** section shows:
  - Relevance scores for top 3 sources
  - Source type (Case law, Regulation, Firm material)
  - Hit rate summary (used X of Y retrieved)
- Migration 012: Add columns to `messages` table

#### Skill-Based Chat Routing
- Extended `MessageCreate` schema:
  - Optional `skill` field: route to specific plugin (e.g., "commercial-legal", "litigation-matter-intake")
  - Optional `matter_id` field: inject case context into conversation
- Chat endpoint enhancements:
  - If skill provided: prepend skill context to RAG prompt
  - If matter provided: load matter details, scrub PII if privacy_mode=true, inject into conversation history
  - Track applied skill in Message model + UsageRecord
- Skill-aware response templates (already in plugin system, now injected into RAG)

#### Tenant Settings & Feature Flags
- `TenantSettings` model (one per tenant, unique constraint):
  - Cache controls: `cache_enabled`, `cache_ttl_multiplier` (0.5–2.0)
  - User defaults: `default_expertise_level`, `default_practice_areas` (array), `default_privacy_mode`
  - Feature flags: `enable_auto_memory`, `enable_pii_detection`, `enable_skill_routing`, `enable_matter_context`
  - Rate limiting: `max_requests_per_minute`, `max_daily_tokens`
  - Custom config: JSON blob for tenant-specific overrides
  - Notes: Admin annotations
- Migration 014: Create `tenant_settings` table with RLS + indexes
- System defaults applied at tenant signup; admins override per-tenant
- New admin endpoints:
  - `GET /admin/settings` — Retrieve tenant settings
  - `PUT /admin/settings` — Update (admin only)

#### Expertise-Aware Caching
- `ExpertiseCacheManager` service — Three-tier caching by expertise level:
  - **Junior** (paralegal): RAG 1h, LLM 30m, matter 2h (40% hit target)
  - **Mid** (associate): RAG 30m, LLM 15m, matter 1h (25% hit target)
  - **Senior** (partner): RAG 15m, LLM 5m, matter 30m (10% hit target)
- Skill-based TTL multipliers:
  - Commercial 1.5x (higher complexity, longer cache OK)
  - Employment 1.3x
  - Litigation 0.7x (time-sensitive, shorter cache)
  - Renewal 2.0x (static data)
- Methods:
  - `get_cached_rag_results()`, `set_cached_rag_results()`
  - `get_cached_llm_response()`, `set_cached_llm_response()`
  - `get_cached_matter_context()`, `set_cached_matter_context()`
  - `invalidate_user_cache()` — Clear on privilege change
  - `get_cache_config()` — Retrieve active config for user
- Extended `UsageRecord` with cache tracking:
  - `cache_hit_rag` — Boolean, did RAG query hit cache?
  - `cache_hit_llm` — Boolean, did LLM response hit cache?
  - `cache_hit_matter` — Boolean, did matter context hit cache?
- Cache analytics endpoint: `GET /admin/cache-analytics`

#### Enhanced Admin Console
- New admin endpoints:
  - `GET /admin/tenant/detailed` — Full tenant profile with analytics:
    - User counts (total, active)
    - Message volume, total cost
    - Cache hit rate, avg response time
  - `GET /admin/users/{user_id}` — User detail with:
    - Practice areas, expertise, privacy mode, memory summary
    - Last activity, created/updated timestamps
  - `GET /admin/cache-analytics` — Cache performance metrics:
    - Total requests, cache hits, hit rate (%)
    - Per-tier hit rates (RAG, LLM, matter)
    - Estimated cost savings
- New schemas in `schemas/admin.py`:
  - `UserDetailResponse` — Full user profile
  - `TenantSettingsResponse`, `TenantSettingsUpdate`
  - `TenantDetailResponse` — Analytics-rich tenant view
  - `CacheAnalytics` — Performance metrics

#### Error Logging & Support Management
- `ErrorLog` model — Global error tracking:
  - Per-user and system-level logging (user_id nullable for system errors)
  - Error classification: api_error, rag_query_error, llm_error, cache_error, database_error, authentication_error, validation_error, timeout_error, rate_limit_error, permission_error
  - Severity levels: critical, error, warning, info
  - Request context: endpoint, method, status_code, IP address, user agent
  - Error details: message, stack trace, request ID
  - Conversation context: conversation_id, query_text for debugging
  - Resolution tracking: is_resolved, resolved_at, resolution_notes
  - Composite indexes for efficient 72-hour rolling per-user queries and system-level recent errors
- Migration 015: Create `error_logs` table with RLS
- Admin endpoints (pending implementation):
  - `GET /admin/errors/user/{user_id}?days=3` — Per-user 72-hour rolling error logs
  - `GET /admin/errors/system?days=3` — System-level errors
  - `GET /admin/errors/summary` — Error metrics and top issues

### Changed
- Chat endpoint: integrated cache manager, matter context loading with PII scrubbing, PII detection in user input
- Guardrails: extended to include PII detection alongside prohibited phrase checking
- Message model: now tracks context usage, skill applied, and PII flags for full audit trail
- Admin dashboard: enhanced tenant view with detailed analytics and user drill-down
- User model: expertise-driven system behavior (cache TTLs, response length, confidence thresholds)
- Auth schemas: use validated emails and password length constraints

#### Auth Hardening
- Existing tenant domains now require admin invitation/account pre-provisioning instead of automatic self-registration joins
- OAuth login callbacks now use short-lived frontend exchange codes instead of bearer JWTs in redirect URLs
- Integration OAuth connects now require authenticated initiating users and bind callback state to user, tenant, intent, and role
- Google OAuth login now rejects unverified Google email claims
- Backend-side auth rate limits now cover login, registration, forgot-password, and reset-password endpoints
- OAuth token storage now fails closed when `TOKEN_ENCRYPTION_KEY` is missing or invalid
- OAuth token expiry writes now use timezone-aware datetimes matching the database schema
- Per-user OAuth token lookup now includes explicit tenant filtering in addition to RLS
- Tenant RLS context is now set with a bound `set_config` parameter and UUID validation

### Migration Summary
- 010: Enhance user model (practice_areas, expertise_level, default_skill, privacy_mode, memory_summary, last_memory_update)
- 011: Create user_memory table
- 012: Extend message context tracking (skill_applied, context_used, context_relevance_scores, pii_flags)
- 013: Add cache tracking to usage_records (cache_hit_rag, cache_hit_llm, cache_hit_matter)
- 014: Create tenant_settings table (per-tenant feature flags and cache config)
- 015: Create error_logs table (per-user and system error tracking)

### Tests
- Lint: all new files pass ruff validation
- Auth: targeted ruff, Python compile, schema probe, frontend build, and regression grep checks for hardened auth modules
- Models: SQLAlchemy validation for RLS policies
- Schemas: Pydantic model_config set to "from_attributes=True" for ORM binding

## [0.3.0] — 2026-06-01

### Added
- CourtListener public RAG pipeline
  - `scripts/ingest_courtlistener.py` now extracts/chunks only and inserts `public_chunks` rows pending Jetson embeddings
  - `scripts/jetson_embed_worker.py` remains the BGE-small embedding writer for `public_chunks.embedding`
  - `scripts/create_public_chunks_index.sql` builds the IVFFlat index after embedding
  - `scripts/courtlistener_jetson_pipeline.md` documents the single-Jetson same-network workflow
  - RAG now searches `public_chunks` with optional BGE query embeddings alongside tenant document chunks
- Phase 1: OAuth token persistence — encrypted token vault with Fernet (AES-256-GCM)
  - `TenantCredential` and `UserOAuthToken` SQLAlchemy models with RLS
  - `TokenVault` service with auto-refresh for MS Graph + Google APIs
  - `GET /api/integrations/microsoft/connect|callback` — admin/user OAuth flows
  - `GET /api/integrations/google/connect|callback` — admin/user OAuth flows
  - `GET /api/integrations/status` — admin-only integration health
  - `POST /api/integrations/{provider}/disconnect` — revoke tokens
- Phase 2: Email agentic pipeline + Calendar sync
  - `MicrosoftMailService` — per-user/per-tenant inbox read via Graph API
  - `GoogleMailService` — Gmail API inbox read with label-aware filtering
  - `EmailAgent` — LLM classification (legal_query/court_filing/client_comm/etc) + draft response generation
  - `CalendarSyncService` — read/write M365 + Google Calendar; bidirectional deadline sync
  - `POST /api/email/scan` — scan + classify + draft responses
  - `POST /api/email/calendar` — list events + optional deadline sync
- Phase 3: Document sync for RAG
  - `DocumentSyncService` — sync from OneDrive, SharePoint, Google Drive
  - `GET /api/sync/documents/stats` — cross-drive document counts
  - `POST /api/sync/documents/list` — list legal documents by provider
  - `POST /api/sync/documents/sync-and-ingest` — background download + RAG pipeline ingestion
- Phase 4: Gemini + Azure OpenAI LLM providers
  - `LLMService._complete_gemini()` — Google Gemini 2.0 Flash via REST API
  - `LLMService._complete_azure()` — Azure OpenAI (GPT-4o) via SDK
  - Provider routing via `provider=` param on `LLMService.complete()`
- Phase 5: Admin user sync dashboard
  - `UserSyncService` — M365 Graph `/users` + Google Directory API sync
  - `POST /api/sync/users/microsoft` — sync M365 users to Clarity
  - `POST /api/sync/users/google` — sync Google Workspace users
  - `POST /api/sync/users/all` — sync both providers
- Config: `TOKEN_ENCRYPTION_KEY`, `AZURE_OPENAI_*`, `GEMINI_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_*`
- Migration 009: `tenant_credentials` + `user_oauth_tokens` tables with RLS
- New deps: `cryptography`, `google-auth-oauthlib`, `google-api-python-client`, `google-genai`

### Changed
- CourtListener sync tooling now targets `public_chunks` instead of tenant-scoped sentinel rows in `chunks`
- Jetson launcher defaults to one `JETSON_HOST`, with optional multi-host `JETSON_HOSTS`
- Auth OAuth flows: added `offline_access` scope to MS and Google login
- LLMService: added optional `provider` parameter for Gemini/Azure routing

### Tests
- Lint: all new files pass ruff (28 pre-existing issues in other files remain)

## [0.2.0] — 2026-05-31

### Added
- Email/password registration (`POST /auth/register`) with company details form
- Email/password login (`POST /auth/login`)
- Password reset flow (`POST /auth/forgot-password`, `POST /auth/reset-password`)
- SignupPage, ForgotPasswordPage, ResetPasswordPage (React)
- `password_hash` column to User model (005 migration)
- `company_name`, `staff_size`, `address`, `phone` columns to Tenant model (005 migration)
- JWT `iat` (issued-at) and `jti` (JWT ID) claims
- Token blacklist on logout via Redis (fallback to in-process dict)
- Healthchecks for postgres, redis, backend, frontend in docker-compose
- Production frontend Dockerfile (multi-stage Vite build + serve)
- `/health`, `/docs`, `/openapi.json`, `/redoc` proxying through nginx

### Changed
- Registration reuses existing domain tenant; first user gets admin
- Login queries scoped by created_at desc + limit(1)
- Logout now blacklists JWT tokens
- Backend Dockerfile: added wget for healthcheck
- Frontend Dockerfile: multi-stage build serving via `serve` instead of `vite dev`

### Fixed
- Sidebar: `documents.map` and `conversations.length` crashes (Array.isArray guards)
- Registration: missing `db.commit()` after user creation
- Login: `is_active` check added
- `passlib[bcrypt]` → `bcrypt>=4.0,<5.0` in requirements.txt (incompatibility)
- Reset tokens hidden when `DEV_MODE=false`
- Fallback dict TTL garbage collection
- CORS: added `https://172.16.16.202`

### Security
- `SECRET_KEY` regenerated
- `DEV_MODE=false` on hypervisor
- Credentials removed from `.env`
- `PRIMARY_LLM` reverted to `deepseek-chat`

## [0.1.0] — Initial

### Added
- Multi-tenant architecture with domain-based tenant isolation
- Row-Level Security (RLS) on all tables
- OAuth authentication (Microsoft, Google)
- Chat with DeepSeek + Claude Opus (RAG via pgvector)
- Document upload with vector embedding
- Plugin system: Litigation Matters, Commercial Renewals
- Admin dashboard (tenant users, usage stats)
