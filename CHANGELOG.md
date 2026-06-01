# Changelog

## [0.3.0] — 2026-06-01

### Added
- Local no-bind Docker Compose mode for Windows workspaces that Docker cannot bind mount
  - `docker-compose.local.yml` exposes backend, frontend, Postgres, Redis, and vectordb without live mounts
  - Frontend Dockerfile accepts `VITE_API_URL` at build time for direct local frontend-to-backend testing
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
- Containers: local no-bind compose rebuild/start verification
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
