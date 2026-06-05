# TASKS.md

## Sprint 12 — LiteLLM Gateway & AI Operations Control Plane (v0.14.0)

**Goal:** Make LiteLLM the primary LLM execution gateway for chat, plugin skills, retrieval planning, memory summaries, email drafting, and operator-managed model routing. The LegalApp backend remains the business control plane for tenant policy, RAG/context assembly, legal guardrails, usage records, and support workflows; LiteLLM owns provider abstraction, model aliases, fallback chains, provider health, and gateway telemetry.

**Architecture:** App resolves a logical route (`standard` / `premium` / tenant override) to a LiteLLM model alias such as `clarity-standard` or `clarity-premium`. App sends legal context and metadata to the LiteLLM OpenAI-compatible proxy. LiteLLM executes provider routing/fallbacks and returns provider telemetry where available. App records requested route, resolved route, actual model, tenant/user/matter/plugin metadata, tokens, cost, error class, and gateway request IDs.

### 1201. LiteLLM Gateway Foundation (P0, MEDIUM) — COMPLETED
- [x] Add app config: `LITELLM_ENABLED`, `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `LITELLM_STANDARD_MODEL`, `LITELLM_PREMIUM_MODEL`
- [x] Add `litellm` as a valid LLM provider in app routing and operator console provider metadata
- [x] Add LiteLLM OpenAI-compatible chat + streaming execution path in `LLMService`
- [x] Add `/models` fetch for LiteLLM in platform provider list, with configured alias fallback
- [x] Add LiteLLM service to Docker Compose with config mount and Postgres dependency
- [x] Add starter `litellm_config.yaml` with `clarity-standard` and `clarity-premium` aliases
- [x] Add dedicated `litellm-postgres` service and local inspection port
- [x] Define standard/premium fallback profiles in `litellm_config.yaml`
- [x] Add production secret docs for LiteLLM master key, DB password, and provider keys

### 1202. LiteLLM-Only Routing Refactor (P0, LARGE) — CLAIMED

**Claimed:** 2026-06-05 — Codex. Replanned after LiteLLM deployment: LegalApp must stop acting as a provider router. All LLM execution goes through LiteLLM aliases; the app only resolves logical routes, assembles legal context, applies guardrails, and records audit/billing metadata.

- [ ] Refactor `LLMService` to a concise LiteLLM OpenAI-compatible client only; remove direct DeepSeek/OpenCode/OpenRouter/Anthropic/Azure/Gemini execution paths from backend code
- [ ] Replace provider-first route resolution with logical route resolution: `standard`, `premium`, `tenant-standard`, `tenant-premium` → LiteLLM aliases (`clarity-standard`, `clarity-premium`, tenant override aliases)
- [ ] Persist requested route, resolved route, gateway alias, gateway request ID/fallback metadata when available, and model used on `usage_records`
- [ ] Include resolved LiteLLM alias in cache keys for all LLM-backed flows
- [ ] Route chat, stream chat, plugin skills, cold-start interviews, memory summaries, retrieval planner, prompt tests, and email agent through one resolver/gateway path
- [ ] Update platform/admin UI and API language from provider selection to gateway alias override
- [ ] Remove direct-provider fallback from backend app; provider failover belongs inside LiteLLM config

### 1203. Operator Console — AI Operations (P0, LARGE) — PENDING
- [ ] Add AI Operations tab with global standard/premium aliases and per-tenant override table
- [ ] Add model/provider disable switch with immediate route validation
- [ ] Add model test action using synthetic prompt and no tenant data
- [ ] Show recent LLM failures by tenant, route, provider, model, status code, and latency
- [ ] Show fallback activity and provider health summary from LiteLLM telemetry
- [ ] Add short-retention debug mode toggle per tenant/conversation with explicit audit entry

### 1204. Gateway Audit, Privacy, and Retention (P0, MEDIUM) — PENDING
- [ ] Disable raw prompt/response logging in LiteLLM by default
- [ ] Send metadata only: tenant_id, user_id, conversation_id, operation_type, matter_id, plugin, skill, premium flag
- [ ] Define retention windows for gateway logs, spend logs, and debug logs
- [ ] Add operator audit entries for route changes, provider disables, tenant debug mode, and model tests
- [ ] Document legal-data handling rules for LiteLLM logs and callbacks

### 1205. Cutover and Rollback (P1, MEDIUM) — PENDING
- [ ] Add shadow route logging to compare current direct-provider path vs LiteLLM alias
- [ ] Canary selected tenants through LiteLLM standard route
- [ ] Canary premium route through LiteLLM
- [ ] Add rollback playbook: switch global route to direct provider or emergency alias
- [ ] Remove direct-provider default once LiteLLM stability is proven

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

---

## Recently Completed

### 1111. Operator Console — Error Diagnostics & API Traffic Logs (P0, LARGE) — COMPLETED
- [x] Fixed `LIMIT is not defined` ReferenceError
- [x] Masked user emails in tenant detail view
- [x] Platform error log endpoints: `GET /api/platform/logs` (cross-tenant, paginated, filterable), `/logs/summary`, `/logs/tenant/{id}`, `/logs/tenant/{id}/summary`
- [x] `ApiAccessLog` model (migration 038) + middleware logging every request (metadata only)
- [x] Platform access log endpoints: `GET /api/platform/access-logs`, `/access-logs/summary`
- [x] Logs tab in operator console: System Errors, Tenant Logs, API Traffic sub-tabs

### Cloud Drive Integration Fix — COMPLETED
- [x] Fixed Google Drive scope: `drive.readonly` → `drive` (write ops were 403 silently)
- [x] Fixed Microsoft scope: `Files.Read.All` → `Files.ReadWrite.All`
- [x] `_ensure_cloud_root()` auto-backfill on admin re-auth
- [x] `POST /api/integrations/cloud-init/retry` endpoint with matter folder backfill
- [x] `cloud_init.py`: matter folders store `url` for both providers
- [x] `MatterDetailPage.jsx`: Cloud Storage links in Case Details

### 1109. Calendar Sync — Multi-User Sync Fix (P0, MEDIUM) — COMPLETED
- [x] Per-user sync error logging in `token_vault.py`; `RuntimeError` → `ValueError` in `calendar_sync.py`
- [x] Return 401 with readable message in `email_agent.py`
- [x] `CalendarPage` shows sync button + success/error banner

### 1110. Mobile Responsive UI Overhaul (P1, LARGE) — COMPLETED
- [x] Sidebar: hamburger button + overlay on mobile
- [x] ChatPage, MatterDetailPage, AdminPage, MatterPortfolioPage: responsive padding, scrollable tabs
- [x] ChatInput: iOS safe-area bottom padding; model selector hidden on small screens

### 1112. AppShell Layout — Restore Consistent UI (P0, LARGE) — COMPLETED
- [x] Create `AppShell.jsx` with shared sidebar (always-on desktop, overlay mobile) + top header bar
- [x] Prominent Admin button (Shield icon) in top-right for admin users across all pages
- [x] `AppShellContext` for shared conversations/documents state
- [x] `ShellRoute` wrapper composing `ProtectedRoute` + `AppShell` in App.jsx
- [x] Refactor ChatPage to use shared context; remove direct sidebar rendering
- [x] Collapsible admin tab bar with toggle + dropdown picker
- [x] Remove redundant `min-h-screen` wrappers from key pages

### 1113. Bug Fixes — Calendar, Estate, Time Tracking (P0, MEDIUM) — COMPLETED
- [x] Calendar page: single "Sync Calendar" button auto-detects connected provider (microsoft/google)
- [x] Estate creation: map human-readable types (Probate → probate) to match backend schema, fix 422
- [x] Time tracking: hide hourly_rate from non-admin users; use `user.default_billing_rate` as default
- [x] Time tracking: add `Rate` column (inline-edit) to admin Users tab for `default_billing_rate`
- [x] Reports: make `budget_currency` Optional in schema with "USD" default (fix potential 500)

---

## Future

- [ ] **Time tracking advanced:** allow rate override on invoice creation screen for admin
- [ ] **Templates overhaul:** support PDF/DOCX native templates with field mapping (currently text-only)
