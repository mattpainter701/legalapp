# TASKS.md

## Sprint 11 — Legal MCP Database & CourtListener Ingest Pipeline (v0.13.0)

**Goal:** Build a production-grade legal knowledge base with structured case law metadata, a CourtListener ingest pipeline with nightly updates, Mixedbread 1024-dim embeddings, and an MCP server (REST + SSE) with 7 domain-scoped legal tools — sold as an API product and wired into the LegalApp chat as an MCP tool.

**Architecture:** Separate MCP/Vector database server. Vectordb holds `courts`, `opinions`, `opinion_citations`, `opinion_chunks`, `legal_topics`, `ingest_runs`. Main app postgres holds `mcp_usage_logs`, `mcp_rate_limits`. Ingest + embedding + MCP scheduler all run on the MCP server. Main app queries vectordb remotely via `VECTORDB_URL`. See `docs/legal_rag.md` for full design.

### 1101. Legal Knowledge Base Schema (P0, LARGE) — PENDING
- [ ] Create vectordb migration: `courts` table (court_id PK, full_name, short_name, jurisdiction_type, jurisdiction_level, jurisdiction_scope, circuit_or_state)
- [ ] Create vectordb migration: `opinions` table (id, opinion_id UNIQUE, case_name, court_id FK, decision_date, status, docket_number, source_url, practice_areas JSONB, full_text_hash, ingested_at, updated_at) with indexes on court_id, decision_date, practice_areas GIN, status
- [ ] Create vectordb migration: `opinion_citations` table (id, citing_opinion_id FK, cited_reporter, cited_volume, cited_page, cited_opinion_id FK nullable) with indexes on citing/cited opinion IDs and reporter triple
- [ ] Create vectordb migration: `opinion_chunks` table (id, opinion_id FK, court_id FK, content, chunk_index, embedding Vector(1024), practice_areas JSONB, legal_topics JSONB, embedding_version int default 0, created_at) with GIN indexes on practice_areas/legal_topics, IVFFlat placeholder on embedding
- [ ] Create vectordb migration: `legal_topics` table (id, name, slug UNIQUE, parent_id self-FK, path, description) with seed data for top-level taxonomy
- [ ] Create vectordb migration: `ingest_runs` table (id, source, started_at, completed_at, status, opinions_processed, chunks_created, embeddings_generated, errors JSONB)
- [ ] Seed `courts` table with ~200 CourtListener court entries (federal + state supreme + state appellate)
- [ ] Seed `legal_topics` table with top-level legal taxonomy (constitutional, contract, tort, criminal, family, trust-estate, etc.)
- [ ] Backfill: migrate existing `public_chunks` rows → `opinions` + `opinion_chunks`, infer court_id from court string, classify practice_areas, set embedding_version=0

Design: `docs/legal_rag.md` §2

### 1102. CourtListener Ingest Service (P0, LARGE) — PENDING
- [ ] Create `mcp-server/mcp_server/courtlistener_ingest.py` — `CourtListenerIngestService` class
- [ ] API incremental mode: CourtListener REST API `/api/rest/v3/opinions/?date_filed__gte={since}` with pagination and API key auth
- [ ] Bulk import mode: gzipped JSONL file ingestion (preserving existing `ingest_courtlistener.py` pattern)
- [ ] Full metadata extraction: citations list, court_id mapping, docket_number, precedential status, cluster data
- [ ] Idempotent upserts: `ON CONFLICT (opinion_id) DO UPDATE` for opinions, `ON CONFLICT DO NOTHING` for chunks
- [ ] Citation parsing: extract (reporter, volume, page) tuples per opinion, upsert into `opinion_citations`
- [ ] Practice area classification: rule-based from court_id jurisdiction + case name pattern matching (no LLM cost)
- [ ] `ingest_runs` tracking: create run on start, update counts/errors on completion
- [ ] Config: `COURTLISTENER_API_KEY`, `COURTLISTENER_API_BASE_URL`, `COURTLISTENER_ENABLED`
- [ ] CLI entry point: `python -m mcp_server.ingest_worker` for manual/bulk runs

Design: `docs/legal_rag.md` §3

### 1103. Embedding Pipeline — Mixedbread 1024-dim (P0, MEDIUM) — PENDING
- [ ] Create `mcp-server/mcp_server/embeddings.py` — `MCPEmbeddingService` with `local`/`api`/`jetson` backends
- [ ] `embed_public_batch()` method: batch embed `opinion_chunks WHERE embedding IS NULL OR embedding_version < current_version`, handles batching (128/call), retry, version tracking
- [ ] `embed_public_query()` method: single query embedding with mxbai query prefix, for runtime RAG queries
- [ ] Metadata-enriched embedding: prepend `[{jurisdiction_type}] [{practice_area}] ` to chunk text before embedding (config: `PUBLIC_EMBEDDING_ENRICH_METADATA=true`)
- [ ] Update main app `EmbeddingService.embed_public_query()` to use mxbai-1024 with backward compat fallback
- [ ] Update main app `search_public_chunks()` to query `opinion_chunks` + JOIN `opinions` + `opinion_citations` + `courts` with filter support
- [ ] `embedding_version` tracking: `PUBLIC_EMBEDDING_VERSION=1` config, rows re-embedded on version mismatch
- [ ] Re-embedding migration: backfill all chunk embeddings from BGE-384 to mxbai-1024, build new IVFFlat index
- [ ] CLI entry point: `python -m mcp_server.embed_worker` for manual/batch embedding
- [ ] Config: `PUBLIC_EMBEDDING_MODEL=mixedbread-ai/mxbai-embed-large-v1`, `PUBLIC_EMBEDDING_DIM=1024`, `PUBLIC_EMBEDDING_BACKEND=local`

Design: `docs/legal_rag.md` §4

### 1104. Nightly Ingest Scheduler (P0, MEDIUM) — PENDING
- [ ] Create `mcp-server/mcp_server/scheduler.py` — APScheduler for MCP server jobs
- [ ] `cl-ingest` job: nightly 3:00 AM ET — incremental CourtListener API pull → ingest → chunk → tag practice areas
- [ ] `cl-embed` job: nightly 3:30 AM ET — embed new/updated opinion_chunks (runs after ingest)
- [ ] `cl-stats` job: weekly Sunday 4:00 AM ET — opinion/chunk counts, court distribution, unembedded count summary
- [ ] Agent registry entries for manual trigger on main app: `POST /scheduler/agents/cl-ingest/run`, `POST /scheduler/agents/cl-embed/run` (proxy to MCP server)
- [ ] Admin endpoints: `GET /admin/cl/status`, `POST /admin/cl/ingest/trigger`, `POST /admin/cl/embed/trigger`, `GET /admin/cl/ingest/history` — proxy to MCP server REST API
- [ ] `ingest_runs` logging: each run creates/updates a row with counts, errors, timing

Design: `docs/legal_rag.md` §7

### 1105. MCP Tool Definitions by Legal Domain (P1, LARGE) — PENDING
- [ ] Create `mcp-server/mcp_server/mcp_tools.py` — tool registry with full JSON Schema definitions for 7 legal tools
- [ ] `search_caselaw`: general semantic search with optional jurisdiction/practice_area/date filters, vector search on `opinion_chunks` with JOINs
- [ ] `search_by_jurisdiction`: scoped to federal circuit, state, or specific court via `courts` table
- [ ] `search_by_practice_area`: scoped to practice area slug via `practice_areas @> :area::jsonb` filter
- [ ] `search_by_citation`: exact citation lookup via `opinion_citations` reporter/volume/page match
- [ ] `get_case_details`: full opinion metadata + all chunks + related citations by opinion_id or citation
- [ ] `get_court_info`: court profile with jurisdiction type, level, opinion count, date range
- [ ] `search_similar_cases`: find cases similar to a given opinion using its chunk embedding as query vector
- [ ] Filtered vector SQL: dynamic WHERE clauses for court_id, practice_areas, date ranges appended to cosine similarity query
- [ ] Update main app `hybrid_rag_query()` to support jurisdiction/practice_area filters on public search

Design: `docs/legal_rag.md` §5

### 1106. MCP Protocol Server — REST + SSE (P1, MEDIUM) — PENDING
- [ ] Create `mcp-server/` package with `pyproject.toml`, `Dockerfile`, `mcp_server/server.py`
- [ ] Install `mcp` Python SDK as dependency
- [ ] SSE transport: endpoint on `:8020` for AI tool consumers (Claude Desktop, Cursor) per MCP spec 2024-11-05
- [ ] REST transport: endpoint on `:8021` with `GET /api/mcp` manifest, `POST /api/mcp/tools/call` invocation, `GET /api/mcp/usage`
- [ ] Auth: API key validation on `initialize` params (SSE) and `X-API-Key` header (REST), tenant resolution
- [ ] Refactor main app `backend/app/routers/mcp.py` to thin proxy: forward `/api/mcp/tools/call` to MCP server REST endpoint, keep `/api/mcp/api-key` locally
- [ ] Docker: add `mcp`, `cl-ingest`, `embed-worker`, `cl-scheduler` services to `docker-compose.mcp.yml`
- [ ] Config: `MCP_SSE_PORT=8020`, `MCP_REST_PORT=8021`, `MCP_SSE_ENABLED=true`, `MCP_REST_ENABLED=true`

Design: `docs/legal_rag.md` §6

### 1107. MCP Usage Metering & Rate Limiting (P1, MEDIUM) — PENDING
- [ ] Create main app migration: `mcp_usage_logs` table (id, tenant_id FK, user_id FK nullable, tool_name, arguments_hash, input_tokens, output_tokens, latency_ms, status_code, created_at) with RLS, indexes on (tenant_id, created_at) and (tool_name, created_at)
- [ ] Create main app migration: `mcp_rate_limits` table (tenant_id PK FK, monthly_limit, used_this_month, reset_at, created_at, updated_at) with RLS
- [ ] Rate limiting middleware: per API key, `MCP_RATE_LIMIT_RPM=60` (Redis sliding window), `MCP_RATE_LIMIT_DAILY=5000` (configurable per tenant via `mcp_rate_limits`)
- [ ] Metering on every `tools/call`: write to `mcp_usage_logs`, increment `used_this_month` in `mcp_rate_limits`
- [ ] Admin endpoints: `GET /admin/mcp/usage` (summary with filters), `GET /admin/mcp/usage/export?format=csv`, `PUT /admin/mcp/rate-limits/{tenant_id}`
- [ ] Tenant self-service: `GET /mcp/usage` — tenant views own usage stats
- [ ] `mcp_rate_limits.used_this_month` auto-reset: scheduler job on 1st of each month resets counter and sets `reset_at`

Design: `docs/legal_rag.md` §8

### 1108. Frontend — MCP Admin Dashboard (P2, MEDIUM) — PENDING
- [ ] AdminPage "MCP" tab: API key management (generate/regenerate, masked display)
- [ ] MCP usage chart: calls by tool, daily/weekly/monthly toggle
- [ ] Rate limit display per tier (free/pro/enterprise estimate)
- [ ] CourtListener ingest status panel: last run time, total opinions, total chunks, unembedded count, court distribution
- [ ] Manual ingest/embed trigger buttons with status feedback
- [ ] MCP configuration section: SSE enable/disable toggle, transport ports display
- [ ] API functions in `frontend/src/api.js`: `getMcpStatus`, `triggerClIngest`, `triggerClEmbed`, `getClHistory`, `getMcpUsage`, `exportMcpUsage`, `setMcpRateLimits`

### 1109. Calendar Sync — Fix Multi-User Sync Failure (P0, MEDIUM) — COMPLETED

- [x] Diagnose: `get_fresh_user_token()` silently returned `None` on any failure; calendar service raised bare `RuntimeError` that crashed as 500 with no user feedback
- [x] Verify `set_tenant_context` is called before calendar DB queries — confirmed present in both `email_agent.py` and `calendar_sync.py`
- [x] Confirm OAuth tokens are stored per-user (`UserOAuthToken` keyed on `user_id + provider`) — no admin-sharing issue
- [x] Add per-user sync error logging in `token_vault.py` (logs user_id, provider, reason on every None return); change `RuntimeError` → `ValueError` in `calendar_sync.py`; return 401 with readable message in `email_agent.py`
- [x] Surface errors in UI: `CalendarPage` now shows sync button, spinner, and success/error banner after each attempt

### 1110. Mobile Responsive UI Overhaul (P1, LARGE) — COMPLETED

- [x] Audit all pages for mobile breakpoints — identified worst offenders (sidebar, tables, chat, matter detail)
- [x] Sidebar: hamburger button (md:hidden) in ChatHeader, overlay with backdrop, slide-in/out via sidebar-hidden/sidebar-visible CSS classes wired to sidebarOpen state in ChatPage
- [x] Matter detail tabs: tab bar made horizontally scrollable (overflow-x-auto, flex-shrink-0 on each tab), edit form grids changed to grid-cols-1 sm:grid-cols-2, billing stats grid to grid-cols-1 sm:grid-cols-3, team add form to flex-col sm:flex-row
- [x] Chat page: ChatInput px reduced to px-4 md:px-8, iOS safe-area bottom padding via env(safe-area-inset-bottom), model selector and public case law toggle hidden on small screens (sm:hidden/md:hidden)
- [x] ChatHeader: hamburger button visible md:hidden, gap reduced on mobile, controls hidden at small breakpoints
- [x] Admin page: topbar px-4 md:px-8, content px-4 md:px-8 py-8 md:py-12, tab nav overflow-x-auto with whitespace-nowrap tabs
- [x] MatterPortfolioPage: topbar and content padding made responsive (px-4 md:px-8)
- [x] MatterDetailPage: topbar px-4 md:px-8, content px-4 md:px-8, title truncation, action buttons gap responsive
- Viewport meta tag was already present in index.html — no change needed

Files changed: `frontend/src/index.css`, `frontend/src/components/Sidebar.jsx`, `frontend/src/components/ChatHeader.jsx`, `frontend/src/components/ChatInput.jsx`, `frontend/src/pages/ChatPage.jsx`, `frontend/src/pages/MatterDetailPage.jsx`, `frontend/src/pages/AdminPage.jsx`, `frontend/src/pages/MatterPortfolioPage.jsx`

---

## Sprint 10 — SMB File Share Relay Agent (v0.12.0)

**Goal:** Enable enterprise SMB file share search without full content indexing. A relay agent installed on-prem scans file metadata into the SaaS index (tsvector, no embeddings), and fetches file content on-demand when users ask questions — same pattern as session attachments (Tier 1 RAG).

### 1001. Database Schema + Models (P0, LARGE) — COMPLETED
- [x] Migration 036: create `smb_agents` table (id, tenant_id, agent_name, api_key_hash, status, agent_version, hostname, os_info, last_heartbeat, pairing_code, pairing_expires_at, timestamps)
- [x] Migration 036: create `smb_shares` table (id, agent_id, tenant_id, share_path, display_name, file_extensions, max_depth, scan_schedule, last_scan_at, last_scan_status, last_scan_file_count, timestamps)
- [x] Migration 036: create `smb_file_index` table (id, tenant_id, share_id, agent_id, path, filename, ext, mime_type, snippet, owner, size_bytes, modified_time, created_time, is_deleted, search_vector tsvector, last_seen_at, created_at) with GIN index
- [x] Migration 036: create `smb_access_log` table (id, tenant_id, user_id, agent_id, file_path, conversation_id, access_reason, bytes_sent, accessed_at)
- [x] Migration 036: create `matter_smb_shares` table (id, tenant_id, matter_id, share_id, folder_path, display_label, auto_scan, created_at)
- [x] Migration 036: add `smb_folders` JSONB column to matters table
- [x] RLS policies for all SMB tables (tenant-scoped)
- [x] SQLAlchemy models: `SmbAgent`, `SmbShare`, `SmbFileIndex`, `SmbAccessLog`, `MatterSmbShare`
- [x] Register models in `models/__init__.py`
- [x] Config additions: `SMB_ENABLED`, `SMB_PAIRING_CODE_TTL_MIN`, `SMB_MAX_FILE_INDEX_PER_SHARE`, `SMB_SNIPPET_MAX_CHARS`

Files: `backend/app/models/smb.py`, `backend/migrations/versions/036_smb_file_shares.py`, `backend/app/config.py`

### 1002. SMB API Endpoints (P0, LARGE) — COMPLETED
- [x] Pydantic schemas for all SMB operations (`schemas/smb.py`)
- [x] `SmbService` — agent registration, pairing code validation, API key hashing, heartbeat, share management
- [x] Agent-facing endpoints: `POST /api/v1/smb/agents/register`, `POST /api/v1/smb/agents/{id}/sync`, `GET /api/v1/smb/agents/{id}/tasks`, `POST /api/v1/smb/agents/{id}/tasks/{task_id}/result`, `POST /api/v1/smb/agents/{id}/heartbeat`
- [x] User-facing endpoints: `GET /api/v1/smb/files/search`, `GET /api/v1/smb/files/{file_id}`, `POST /api/v1/smb/files/{file_id}/fetch-content`
- [x] Admin endpoints: `GET /api/v1/smb/agents`, `PATCH /api/v1/smb/agents/{id}`, `DELETE /api/v1/smb/agents/{id}`, `POST /api/v1/smb/pairing-code`, `GET /api/v1/smb/shares`, `POST /api/v1/smb/shares`, `PATCH /api/v1/smb/shares/{id}`, `DELETE /api/v1/smb/shares/{id}`, `GET /api/v1/smb/stats`
- [x] Matter binding: `POST /api/v1/matters/{id}/smb-shares`, `GET /api/v1/matters/{id}/smb-shares`, `DELETE /api/v1/matters/{id}/smb-shares/{share_id}`
- [x] API key auth dependency for agent endpoints (separate from JWT auth)
- [x] Router registration in `main.py`

Files: `backend/app/routers/smb.py`, `backend/app/services/smb.py`, `backend/app/schemas/smb.py`, `backend/app/middleware/smb_auth.py`

### 1003. RetrievalPlanner + RAG Integration (P0, MEDIUM) — COMPLETED
- [x] Add "smb" source to `RetrievalPlanner` prompt and output schema
- [x] `SmbSearchService` — tsvector full-text search on `smb_file_index`, scoped by tenant and optional matter
- [x] Content fetch orchestration: user query → planner → smb search → if content needed, dispatch fetch task to agent → poll for result → inject into LLM context
- [x] `build_smb_context()` — format SMB file hits into LLM context string (snippet-only default, content fetch on demand)
- [x] Integrate SMB search into `CloudSearchService.search()` or parallel path in chat endpoint
- [x] 2-minute timeout with metadata-only fallback answer

Files: `backend/app/services/smb_search.py`, `backend/app/services/retrieval_planner.py`, `backend/app/services/rag.py`

### 1004. Admin Dashboard Endpoints (P1, MEDIUM) — COMPLETED
- [x] `GET /api/admin/smb/status` — agent status, share counts, last scan times
- [x] `GET /api/admin/smb/activity` — recent access log entries
- [x] Wire into existing admin dashboard router

Files: `backend/app/routers/cloud_admin.py` (extend)

### 1005. Frontend Admin SMB Page + Chat Integration (P1, MEDIUM) — PENDING
- [x] `SmbAdminPage` — agent list, pairing code generation, share management
- [x] Chat integration: "Searching on-prem files..." status, SMB results in chat
- [x] Matter detail: "File Shares" tab for SMB folder binding

Files: `frontend/src/pages/SmbAdminPage.jsx`, `frontend/src/components/SmbFileShareTab.jsx`

### 1006. Relay Agent Core Package (P0, LARGE) — COMPLETED
- [x] `agent/clarity_agent/` package structure: `__init__.py`, `config.py`, `db.py` (SQLite ledger), `smb_scanner.py`, `smb_reader.py`, `api_client.py`, `task_worker.py`, `heartbeat.py`, `utils.py`
- [x] `smb_scanner.py`: directory walk with 3-tier change detection (directory mtime, file mtime, first-4KB hash), legal extension filter, local SQLite ledger for state tracking
- [x] `smb_reader.py`: content extraction (pypdf for PDF, python-docx for DOCX, plaintext) with size cap (500KB) and snippet generation
- [x] `api_client.py`: SaaS API client with API key auth, retry logic, pairing code registration
- [x] `task_worker.py`: long-polling task fetch (30s interval), content fetch execution, result submission
- [x] `heartbeat.py`: periodic heartbeat to SaaS with version/hostname/OS info
- [x] `config.py`: TOML config file support for SMB credentials (Fernet-encrypted), share paths, sync schedule, API URL
- [x] `pyproject.toml` with `smbprotocol` dependency, CLI entry point `clarity-agent`

Files: `agent/clarity_agent/`, `agent/pyproject.toml`

### 1007. Windows Installer (P1, LARGE) — DEFERRED (pip-first)
- [x] Deferred to post-Sprint 10

### 1008. Sync & Change Detection Algorithm (P0, MEDIUM) — PENDING
- [x] 3-tier scan: directory mtime gate → file mtime comparison → first-4KB hash fallback
- [x] Incremental sync endpoint: `POST /api/v1/smb/agents/{id}/sync` handles upserts, deletions, and snippet updates
- [x] Legal extension filtering (matching `LEGAL_EXTENSIONS` from cloud_sync)
- [x] Per-share file count cap (`SMB_MAX_FILE_INDEX_PER_SHARE`, default 500)

Files: `backend/app/services/smb.py` (sync logic), `agent/clarity_agent/smb_scanner.py`

### 1009. Integration & End-to-End Flow Wiring (P0, MEDIUM) — PENDING
- [x] Wire agent heartbeat sync into scheduler (cloud-sync pattern)
- [x] Wire SMB search into chat endpoint question flow
- [x] End-to-end test: agent register → share scan → file sync → user search → content fetch → LLM context
- [x] `app.include_router(smb_router)` in main.py

Files: `backend/app/main.py`, `backend/app/services/scheduler.py`, `backend/app/routers/chat.py`

### 1010. Testing & Documentation (P2, MEDIUM) — PENDING
- [x] Unit tests for SMB models, schemas, service methods
- [x] Integration test: agent pairing, sync, search flow
- [x] API documentation (OpenAPI schemas)
- [x] Agent README with pip install instructions

### 1011. Matter-to-SMB-Folder Binding (P1, MEDIUM) — PENDING
- [x] `MatterSmbShare` model + router endpoints (create, list, delete)
- [x] Add `smb_folders` JSONB column to matters table (migration 036)
- [x] `RetrievalPlanner` scopes SMB searches to matter-bound paths when matter_id present
- [x] Frontend: "File Shares" tab on matter detail page

Files: `backend/app/routers/smb.py`, `backend/app/models/smb.py`, `frontend/src/components/SmbFileShareTab.jsx`

---

## Sprint 9 — Plugin Platform & Matter Workflow Framework (v0.11.0)

**Goal:** Turn plugins from generic prompt/profile pages into paid add-on workflow modules that can be purchased by a tenant, configured by admins, and attached to matters as the governing workflow/context layer.

### 901. Plugin Catalog, Entitlements & Canonical API (P0, MEDIUM) — COMPLETED
- [x] Create a canonical backend plugin catalog/manifest with display metadata, skill ids, workflow routes, matter type mappings, required/optional integrations, and setup requirements
- [x] Add tenant-level plugin entitlement records so "purchased", "trial", "locked", and "setup required" are distinct from practice-profile existence
- [x] Update `GET /api/plugins` to return the canonical catalog plus tenant entitlement/profile/setup status
- [x] Stop relying on duplicated frontend plugin metadata as the source of truth

Files: `backend/app/services/plugins/manifest.py`, `backend/app/models/plugin.py`, `backend/app/routers/plugins.py`, `backend/app/schemas/plugin.py`, `frontend/src/pages/PluginsPage.jsx`

### 902. Matter-to-Plugin Assignment (P0, MEDIUM) — COMPLETED
- [x] Add first-class `primary_plugin` / plugin workflow assignment on matters
- [x] Suggest a plugin from matter practice area / matter type during matter creation
- [x] Expose plugin assignment in matter create/update/list/detail APIs
- [x] Preserve "general matter" behavior when no paid plugin is attached

Files: `backend/app/models/plugin.py`, `backend/app/schemas/matter.py`, `backend/app/routers/matters.py`, `frontend/src/components/NewMatterModal.jsx`, `frontend/src/pages/MatterDetailPage.jsx`

### 903. Setup 2.0 — Structured Plugin Configuration (P1, LARGE) — COMPLETED
- [x] Replace pure text cold-start setup with plugin-specific structured setup schemas
- [x] Persist settings such as jurisdictions, escalation rules, approval thresholds, templates, source folders, calendars, and house style
- [x] Generate/update the practice profile from structured settings instead of treating it as the activation record
- [x] Support setup health checks: missing integrations, missing required fields, stale profile, incomplete setup

### 904. Cloud-Aware Plugin Workflows (P1, LARGE) — COMPLETED
- [x] Let each plugin declare required/optional Microsoft 365 and Google Workspace capabilities
- [x] Bind plugin workflows to cloud folders, mailboxes, labels, calendars, and SharePoint/Drive locations
- [x] Surface integration readiness per plugin
- [x] Use matter context + cloud metadata in plugin skill execution when a matter is attached

### 905. Plugin Workspace UX & Admin Commerce States (P1, LARGE) — COMPLETED
- [x] Redesign Plugins page around Purchased / Available / Trial / Locked / Setup Required states
- [x] Add admin controls to activate trials and configure purchased plugins
- [x] Move workflow modules into matter/plugin tabs instead of unrelated hardcoded islands where appropriate
- [x] Keep general matters usable without paid plugin purchase

---

## Sprint 8 — Tenant Onboarding & Integration Hub (v0.10.0)

**Goal:** Guided admin onboarding wizard, license/seat management, service account safety, cloud folder initialization, customer LLM configuration, and permission audit.

### 801. Admin Onboarding Wizard (P0, LARGE) — COMPLETED
- [x] Migration 027: onboarding_completed, onboarding_step, cloud_root_folder, service_account_email, license_active, granted_by_user_id, customer LLM fields
- [x] `backend/app/routers/onboarding.py`: GET /status, POST /complete, POST /skip, POST /step/{step}
- [x] `backend/app/schemas/onboarding.py`: OnboardingStatusResponse, OnboardingCompleteResponse
- [x] Post-connect hooks in `integrations.py`: store granted_by_user_id + service_account_email, auto-advance step + trigger user sync
- [x] `user_sync.py`: auto-advance onboarding_step after manual sync
- [x] `frontend/src/pages/OnboardingWizard.jsx`: 5-step wizard (Welcome → Connect → Sync → Review → Complete)
- [x] `AuthCallback.jsx`: redirect admin to /onboarding if not completed
- [x] `App.jsx`: /onboarding route (adminOnly)

Files: `backend/app/routers/onboarding.py`, `backend/app/schemas/onboarding.py`, `backend/migrations/versions/027_sprint8_onboarding.py`, `frontend/src/pages/OnboardingWizard.jsx`

### 802. License/Seat Management (P0, MEDIUM) — COMPLETED
- [x] `backend/app/routers/licensing.py`: GET /licensing, PUT /users/{id}/license, PUT /licensing/seats
- [x] `frontend/src/components/LicensingPanel.jsx`: seat slider, progress bar, per-user license toggles, PAYG cost display
- [x] AdminPage: "Licensing" tab added

Files: `backend/app/routers/licensing.py`, `frontend/src/components/LicensingPanel.jsx`

### 803. Service Account Safety (P1, MEDIUM) — COMPLETED
- [x] `integrations.py`: store granted_by_user_id on admin consent, resolve service_account_email from MS Graph / Google id_token
- [x] `admin.py`: GET /integrations/health (grantor info, warnings for deactivated users, expired tokens)
- [x] `admin.py` deactivate_user: check for service account grants before deactivating; require ?force=true

Files: `backend/app/routers/integrations.py`, `backend/app/routers/admin.py`

### 804. Cloud Folder Init & Matter Auto-Folders (P1, MEDIUM) — COMPLETED
- [x] `backend/app/services/cloud_init.py`: initialize_cloud_root_folder (creates "claritylegal-records"), initialize_matter_folders (emails/documents/pleadings/correspondence/billing)
- [x] `plugins.py` create_matter: auto-create cloud matter folders after matter commit (non-fatal)
- [x] onboarding.py complete: triggers cloud_root_folder creation

Files: `backend/app/services/cloud_init.py`, `backend/app/routers/plugins.py`

### 805. Customer LLM Access (P2, MEDIUM) — COMPLETED
- [x] `admin.py`: POST /customer-llm/configure, DELETE /customer-llm/configure (encrypted API key storage)
- [x] AdminPage SettingsTab: Customer LLM section with toggle, provider dropdown, API key, endpoint inputs

Files: `backend/app/routers/admin.py`, `frontend/src/pages/AdminPage.jsx`

### 806. Permission Audit / Integrations Hub (P1, MEDIUM) — COMPLETED
- [x] `admin.py`: GET /permissions — granted vs required scopes, +user_count, +last_sync freshness per provider
- [x] `frontend/src/components/IntegrationsPanel.jsx`: provider cards with scope checkmarks, synced user count, last-sync freshness, "Sync now" button
- [x] AdminPage: "Integrations" tab (renamed from "Permissions")
- [x] Migration 030: user_sync_state columns on tenant_credentials
- [x] `UserSyncService`: persist sync state, license_active=False on new synced users
- [x] `LegalScheduler`: nightly user-sync job (2:00 AM ET) + manual trigger
- [x] `routers/scheduler.py`: agent registry entry for manual trigger

Files: `backend/app/routers/admin.py`, `backend/app/services/user_sync.py`, `backend/app/services/scheduler.py`, `backend/app/routers/scheduler.py`, `backend/migrations/versions/030_user_sync_state.py`, `frontend/src/components/IntegrationsPanel.jsx`, `frontend/src/pages/AdminPage.jsx`

### 807. Integration Tests & Polish (P1, SMALL) — COMPLETED
- [x] `backend/tests/test_onboarding.py`: onboarding flow, license toggle, service account deactivation guard, permission audit

Files: `backend/tests/test_onboarding.py`

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
- [x] P3-2: Clio marketplace listing + API integration
- [x] P3-3: Clio data migration tool
- [x] P3-4: Tabs3 data migration tool
- [x] P3-5: LEDES XML 2.1 export
- [x] P3-6: QBD via unified API partner (Unified.to / Apideck)

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
- [x] P3-1: QBD migration path (CSV import for firms moving to QBO)

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
- [x] P3-2: Clio marketplace listing + API integration
- [x] P3-3: Clio data migration tool
- [x] P3-4: Tabs3 data migration tool
- [x] P3-5: LEDES XML 2.1 export
- [x] P3-6: QBD via unified API partner (Unified.to / Apideck)

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
- [x] Create admin endpoints for error log querying:
  - [x] `GET /admin/errors/user/{user_id}?days=3` — Per-user 72-hour rolling error logs
  - [x] `GET /admin/errors/system?days=3&severity=error` — System-level errors with optional filters
  - [x] `GET /admin/errors/summary` — Error counts by severity/type, trend data
  - [x] `PATCH /admin/errors/{error_id}/resolve` — Mark error resolved with notes
- [x] Implement error capture middleware/service in main.py
- [x] Wire ErrorLog into exception handlers (400, 404, 500, unhandled exceptions)
- [x] Add error logging to chat endpoint for RAG/LLM/cache failures

### Enhancements
- [x] Email verification on registration (requires SMTP)
- [x] Rate limiting on auth endpoints
- [x] OAuth provider credential setup (Google, Microsoft)
- [x] Email notifications for password reset (currently dev-mode only)
- [x] User interface for setting expertise_level and practice_areas
- [x] User interface for privacy_mode toggle
- [x] User memory dashboard (view learned preferences + interaction patterns)
- [x] Admin console: view/delete UserMemory entries per user

### 808. Skills Expansion — 52 New Legal Prompts (P1, LARGE) — COMPLETED
- [x] Recovered 77 prompt constants from stash — 52 net-new skills across all practice areas
- [x] Added detailed workflow-based prompt templates with output formats for all new skills
- [x] Built `ALL_DEFAULT_PROMPTS` dict: 99 entries across 13 plugins — auto-generates `SKILL_PROMPT_MAP` in executor.py
- [x] Added 3 new practice areas: Family/Domestic Law (`family-law`), Criminal Defense (`criminal-defense`), Real Estate (`real-estate`)
- [x] Added trust-estate-legal plugin entries (ESTATE_WILL_TRUST_REVIEW_PROMPT, ESTATE_PROBATE_CHECKLIST_PROMPT, ESTATE_BENEFICIARY_LETTER_PROMPT, ESTATE_TAX_PREP_PROMPT, ESTATE_ACCOUNTING_REVIEW_PROMPT)
- [x] Added 5 additional prompt constants from v0.9.0 prompt management: PORTFOLIO_STATUS_PROMPT, LEGAL_HOLD_PROMPT, CLOSING_CHECKLIST_PROMPT, CND_TRIAGE_PROMPT, NPRM_COMMENT_PROMPT
- [x] `executor.py` SKILL_PROMPT_MAP auto-builds correctly via `for (plugin, skill), prompt in ALL_DEFAULT_PROMPTS.items()`
- [x] Syntax verified; imports verified; 13 plugins × 99 total skills all wired

Files: `backend/app/services/plugins/prompts.py`, `backend/app/services/plugins/executor.py`

### Mediation Platform Module (PR #38) — Backlog

- [x] Portal document delete endpoint (backend has no DELETE for portal docs)
- [x] Proposal accept/reject UI in PortalCasePage (backend supports status updates)
- [x] End-to-end smoke test of full workflow: invite → accept → asset submission → attorney approve → send → opposing decision → proposal exchange
- [x] Run full mediation test suite (`backend/tests/test_mediation.py`, 7 tests) as part of CI — currently only runs cleanly in isolation due to unrelated LLM/network test hangs
- [x] MediationSubTable: wire delete for portal documents (needs backend DELETE endpoint first)
### Future
- [ ] **Time tracking rate management:** Rates should be set per-user by admin, not exposed per line item. Zero-risk rollout: add `hourly_rate` column to `users` table (nullable, default null), admin endpoint to set rate per user, and a `GET /api/admin/users/:id/rate` endpoint. Time entry calculation uses `user.hourly_rate` as default. Backward-compatible — preserves existing per-entry rate override.
- [x] Production static file serving (nginx directly serves Vite dist)
- [x] Backup strategy for postgres
- [x] Monitoring / observability (error log dashboards, alerting)
- [x] CI/CD pipeline
- [x] HTTPS certificate automation (Let's Encrypt)
