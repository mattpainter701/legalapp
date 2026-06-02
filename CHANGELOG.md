# Changelog

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
