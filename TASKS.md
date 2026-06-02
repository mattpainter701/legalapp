# TASKS.md

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
- [x] Fix passlibΓåÆbcrypt in requirements.txt
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
- [x] PRIMARY_LLMΓåÆdeepseek-chat

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
