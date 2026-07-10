# Legal RAG & MCP Database — Architecture & Design Doc

**Sprint 11 (v0.13.0)** — Legal Knowledge Base, CourtListener Ingest Pipeline, and MCP Server

---

> **Historical design record, not an operations guide.** The original proposal
> below includes superseded SSE, authentication, endpoint, schema, and metering
> details. The implemented product contract is documented in
> `docs/mcp_product_gateway.md`; current sidecar operations are documented in
> `docs/courtlistener_mcp_operations.md`. Public MCP remains disabled by default.

## 1. System Architecture

The MCP/Vector database runs on a **separate server** from the main LegalApp API. In dev, this is a separate Docker Compose file (`docker-compose.mcp.yml`).

```
┌─────────────────────────────────┐     ┌──────────────────────────────────────┐
│  LegalApp API (main server)     │     │  MCP/Vector Server (separate)       │
│                                  │     │                                      │
│  backend (FastAPI)               │     │  vectordb (pgvector:pg16)           │
│  frontend (React/Vite)          │     │    courts                            │
│  main postgres                  │     │    opinions                          │
│    mcp_usage_logs              │     │    opinion_citations                 │
│    mcp_rate_limits             │     │    opinion_chunks (Vector(1024))    │
│  redis                          │     │    legal_topics                      │
│  scheduler (existing jobs)       │     │    ingest_runs                       │
│                                  │     │                                      │
│  Connects to vectordb via       │     │  mcp_server (SSE + REST)            │
│  VECTORDB_URL for RAG queries   │     │    Tool handlers (7 legal tools)     │
│                                  │     │    SSE transport on :8020            │
│  REST proxy: /api/mcp/* ────────┼────→│    REST transport on :8021           │
│  (thin router forwards to MCP)  │     │                                      │
│                                  │     │  cl-ingest (CourtListener ingest)   │
│  Metering writes:               │     │  embed-worker (mxbai-1024 batch)    │
│    mcp_usage_logs → main DB    │     │  cl-scheduler (nightly jobs)        │
│    mcp_rate_limits → main DB   │     │                                      │
└─────────────────────────────────┘     └──────────────────────────────────────┘
```

### Network & Connection Flow

1. **Main app RAG queries** → `VECTORDB_URL` → vectordb (read-only for `opinion_chunks`)
2. **MCP tool calls (REST customers)** → main app `/api/mcp/tools/call` → proxy to MCP server REST endpoint
3. **MCP tool calls (external clients)** → LegalApp `/api/mcp` Streamable HTTP
4. **Metering** → MCP server writes `mcp_usage_logs` + `mcp_rate_limits` to main postgres via `MAIN_DB_URL`
5. **CourtListener ingest** → MCP server writes `opinions` + `opinion_chunks` + `ingest_runs` to vectordb via `VECTORDB_URL`

### Docker Compose Structure

See `docker-compose.mcp.yml` in project root:

```yaml
# docker-compose.mcp.yml (dev)
services:
  vectordb:
    image: pgvector/pgvector:pg16
    ports: ["5433:5432"]
    volumes: [vectordb_data]
    environment:
      POSTGRES_DB: legal_rag
      POSTGRES_USER: legal_rag
      POSTGRES_PASSWORD: ${VECTORDB_PASSWORD}

  mcp:
    build: ./mcp-server
    ports: ["8020:8020", "8021:8021"]
    environment:
      - VECTORDB_URL=postgresql://legal_rag:${VECTORDB_PASSWORD}@vectordb:5432/legal_rag
      - MAIN_DB_URL=postgresql://legalapp:${DB_PASSWORD}@host.docker.internal:5432/legalapp
      - COURTLISTENER_API_KEY=${COURTLISTENER_API_KEY}
      - MCP_SSE_PORT=8020
      - MCP_REST_PORT=8021
    depends_on: [vectordb]

  cl-ingest:
    build: ./mcp-server
    command: python -m mcp_server.ingest_worker
    environment:
      - VECTORDB_URL=postgresql://legal_rag:${VECTORDB_PASSWORD}@vectordb:5432/legal_rag
      - COURTLISTENER_API_KEY=${COURTLISTENER_API_KEY}
    depends_on: [vectordb]

  embed-worker:
    build: ./mcp-server
    command: python -m mcp_server.embed_worker
    environment:
      - VECTORDB_URL=postgresql://legal_rag:${VECTORDB_PASSWORD}@vectordb:5432/legal_rag
      - MAIN_DB_URL=postgresql://legalapp:${DB_PASSWORD}@host.docker.internal:5432/legalapp
    depends_on: [vectordb]

  cl-scheduler:
    build: ./mcp-server
    command: python -m mcp_server.scheduler
    environment:
      - VECTORDB_URL=postgresql://legal_rag:${VECTORDB_PASSWORD}@vectordb:5432/legal_rag
      - COURTLISTENER_API_KEY=${COURTLISTENER_API_KEY}
    depends_on: [vectordb]
```

---

## 2. Database Schema

### 2.1 Vectordb Tables (Legal Knowledge Base)

No RLS needed — all data is public legal case law.

#### `courts`

| Column | Type | Notes |
|-|-|-|
| court_id | VARCHAR(50) PK | CourtListener court_id (e.g., "scotus", "ca1", "cal") |
| full_name | VARCHAR(500) NOT NULL | e.g., "United States Court of Appeals for the First Circuit" |
| short_name | VARCHAR(200) | e.g., "1st Circuit" |
| jurisdiction_type | VARCHAR(20) NOT NULL | `federal` / `state` / `territorial` |
| jurisdiction_level | VARCHAR(30) NOT NULL | `supreme` / `appellate` / `district` / `bankruptcy` / `special` |
| jurisdiction_scope | VARCHAR(20) | `national` / `circuit` / `state` |
| circuit_or_state | VARCHAR(50) | e.g., "1st Circuit", "California", "New York" |
| created_at | TIMESTAMPTZ DEFAULT now() | |

Indexes: `ix_courts_jurisdiction_type` on jurisdiction_type, `ix_courts_jurisdiction_scope` on jurisdiction_scope.

#### `opinions`

| Column | Type | Notes |
|-|-|-|
| id | UUID PK DEFAULT gen_random_uuid() | |
| opinion_id | VARCHAR(255) UNIQUE NOT NULL | CourtListener opinion ID (stable external key) |
| case_name | VARCHAR(500) NOT NULL | e.g., "Roe v. Wade" |
| court_id | VARCHAR(50) FK → courts.court_id | |
| decision_date | DATE | |
| status | VARCHAR(30) NOT NULL DEFAULT 'published' | `published` / `unpublished` / `precedential` / `non_precedential` |
| docket_number | VARCHAR(255) | e.g., "No. 70-18" |
| source_url | TEXT | CourtListener opinion URL |
| practice_areas | JSONB DEFAULT '[]' | Array of practice area slugs, e.g., `["constitutional", "civil-rights"]` |
| full_text_hash | VARCHAR(64) | SHA-256 of plain_text, for change detection |
| ingested_at | TIMESTAMPTZ DEFAULT now() | |
| updated_at | TIMESTAMPTZ DEFAULT now() | |

Indexes: `ix_opinions_court_id`, `ix_opinions_decision_date`, `ix_opinions_practice_areas` (GIN), `ix_opinions_status`.

#### `opinion_citations`

| Column | Type | Notes |
|-|-|-|
| id | UUID PK DEFAULT gen_random_uuid() | |
| citing_opinion_id | UUID FK → opinions.id NOT NULL | The opinion making the citation |
| cited_reporter | VARCHAR(100) | e.g., "U.S." |
| cited_volume | VARCHAR(20) | e.g., "410" |
| cited_page | VARCHAR(20) | e.g., "113" |
| cited_opinion_id | UUID FK → opinions.id NULL | Resolved link (NULL until matched) |
| created_at | TIMESTAMPTZ DEFAULT now() | |

Indexes: `ix_opinion_citations_citing` on citing_opinion_id, `ix_opinion_citations_cited` on cited_opinion_id, `ix_opinion_citations_reporter` on (cited_reporter, cited_volume, cited_page).

#### `opinion_chunks`

Replaces `public_chunks`. Key changes: FK to `opinions` and `courts`, `practice_areas` JSONB for filtering, `legal_topics` JSONB, `embedding_version` for model tracking, and `Vector(1024)` for mxbai-embed-large.

| Column | Type | Notes |
|-|-|-|
| id | UUID PK DEFAULT gen_random_uuid() | |
| opinion_id | UUID FK → opinions.id NOT NULL | Parent opinion |
| court_id | VARCHAR(50) FK → courts.court_id | Denormalized for filtered search |
| content | TEXT NOT NULL | Chunk text |
| chunk_index | INTEGER NOT NULL | 0-based index within opinion |
| embedding | Vector(1024) NULL | mxbai-embed-large-v1 embedding |
| practice_areas | JSONB DEFAULT '[]' | Inherited from opinion, for WHERE filters |
| legal_topics | JSONB DEFAULT '[]' | Extracted topic slugs |
| embedding_version | INTEGER DEFAULT 0 | 0=unembedded, 1=mxbai-1024 |
| created_at | TIMESTAMPTZ DEFAULT now() | |

Indexes:
- `ix_opinion_chunks_opinion_id` on opinion_id
- `ix_opinion_chunks_court_id` on court_id
- `ix_opinion_chunks_practice_areas` GIN on practice_areas
- `ix_opinion_chunks_legal_topics` GIN on legal_topics
- `ix_opinion_chunks_embedding_version` on embedding_version
- IVFFlat index on embedding (1024-dim, cosine ops) — created after initial bulk load
  ```sql
  CREATE INDEX CONCURRENTLY ix_opinion_chunks_embedding
    ON opinion_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
  ```

#### `legal_topics`

Hierarchical taxonomy of legal topics. Self-referential for tree structure.

| Column | Type | Notes |
|-|-|-|
| id | UUID PK DEFAULT gen_random_uuid() | |
| name | VARCHAR(200) NOT NULL | e.g., "Constitutional Law" |
| slug | VARCHAR(100) UNIQUE NOT NULL | e.g., "constitutional" |
| parent_id | UUID FK → legal_topics.id NULL | NULL for top-level topics |
| path | VARCHAR(500) | Materialized path, e.g., "law.civil-rights.voting" |
| description | TEXT | Brief description of the topic scope |
| created_at | TIMESTAMPTZ DEFAULT now() | |

#### `ingest_runs`

Tracks each ingest operation for observability and retry.

| Column | Type | Notes |
|-|-|-|
| id | UUID PK DEFAULT gen_random_uuid() | |
| source | VARCHAR(20) NOT NULL | `api` or `bulk` |
| started_at | TIMESTAMPTZ NOT NULL | |
| completed_at | TIMESTAMPTZ | NULL while running |
| status | VARCHAR(20) NOT NULL DEFAULT 'running' | `running` / `completed` / `failed` |
| opinions_processed | INTEGER DEFAULT 0 | |
| chunks_created | INTEGER DEFAULT 0 | |
| embeddings_generated | INTEGER DEFAULT 0 | |
| errors | JSONB DEFAULT '[]' | Array of {opinion_id, error, timestamp} |
| created_at | TIMESTAMPTZ DEFAULT now() | |

### 2.2 Main App DB Tables (Metering & Rate Limiting)

These live in the main LegalApp postgres with RLS (tenant-scoped).

#### `mcp_usage_logs`

| Column | Type | Notes |
|-|-|-|
| id | UUID PK DEFAULT gen_random_uuid() | |
| tenant_id | UUID NOT NULL FK → tenants.id | RLS tenant isolation |
| user_id | UUID NULL FK → users.id | NULL for API key calls |
| tool_name | VARCHAR(100) NOT NULL | e.g., "search_caselaw", "search_by_jurisdiction" |
| arguments_hash | VARCHAR(64) | SHA-256 of arguments (for dedup/analytics) |
| input_tokens | INTEGER DEFAULT 0 | Estimated token count of input |
| output_tokens | INTEGER DEFAULT 0 | Estimated token count of output |
| latency_ms | INTEGER | Request latency in milliseconds |
| status_code | SMALLINT DEFAULT 200 | HTTP-style status code |
| created_at | TIMESTAMPTZ DEFAULT now() | |

Indexes: `ix_mcp_usage_logs_tenant_created` on (tenant_id, created_at), `ix_mcp_usage_logs_tool_created` on (tool_name, created_at).

RLS policy: tenant_isolation — `tenant_id = current_setting('app.current_tenant_id')::uuid`.

#### `mcp_rate_limits`

| Column | Type | Notes |
|-|-|-|
| tenant_id | UUID PK FK → tenants.id | |
| monthly_limit | INTEGER NOT NULL DEFAULT 5000 | API calls per month |
| used_this_month | INTEGER NOT NULL DEFAULT 0 | Counter, resets monthly |
| reset_at | TIMESTAMPTZ NOT NULL | Next reset timestamp |
| created_at | TIMESTAMPTZ DEFAULT now() | |
| updated_at | TIMESTAMPTZ DEFAULT now() | |

RLS policy: tenant_isolation.

---

## 3. CourtListener Ingest Service

**Design doc reference:** TASKS 1101, 1102

### 3.1 Ingest Service Architecture

`mcp-server/mcp_server/courtlistener_ingest.py` — `CourtListenerIngestService`

Two modes:

#### API Incremental Mode (Nightly)

```
cl-scheduler (3:00 AM)
  → CourtListenerIngestService.ingest_incremental(since=last_run_date)
    → GET https://www.courtlistener.com/api/rest/v3/opinions/?date_filed__gte={since}&order_by=date_filed
    → For each opinion page:
      → extract_opinion_data()
      → practice_area_classify()
      → upsert_opinion()
      → chunk_opinion()
      → upsert_citation_links()
    → Write ingest_runs record
```

Uses `COURTLISTENER_API_KEY` for authenticated requests (higher rate limits).

#### Bulk Import Mode (Initial Load / Backfill)

```
cl-ingest worker
  → CourtListenerIngestService.ingest_bulk(file_path, batch_size=1000)
    → For each opinion in gzip JSONL:
      → Same extraction pipeline as incremental
    → Batch INSERT with ON CONFLICT DO NOTHING
```

### 3.2 Practice Area Classification (Rule-Based)

No LLM cost. Deterministic rules:

| Pattern | Practice Area(s) |
|-|-|
| Court ends with "ca1"-"ca11", "cadc", "cafc" | `["federal-appellate"]` |
| Court = "scotus" | `["constitutional", "federal-appellate"]` |
| Court ends with "d" (district courts) | `["federal-district"]` |
| Court ends with "bap" or "br" (bankruptcy) | `["bankruptcy"]` |
| Court = "uscfc" (federal claims) | `["federal-claims"]` |
| Case name contains "v." | `["litigation"]` |
| Case name starts with "In re" | `["bankruptcy", "regulatory"]` |
| Case name starts with "Petition for" | `["appellate"]` |
| Case name contains "divorce", "custody", "family" | `["family"]` |
| Case name contains "estate of", "probate" | `["trust-estate"]` |
| Case name contains "contract", "breach" | `["contract"]` |
| Case name contains "tort", "negligence", "injury" | `["tort"]` |
| Case name contains "criminal", "state v." | `["criminal"]` |
| Case name contains "patent", "trademark", "copyright" | `["intellectual-property"]` |
| Court in state supreme/appellate list + case name patterns | `["state-{jurisdiction}"]` |
| Default (no match) | `["general"]` |

All practice areas are a superset — a case can have multiple areas. Slugs match `legal_topics.slug` values.

### 3.3 Citation Parsing

Extract `(reporter, volume, page)` tuples from CourtListener `cluster.citations` array:

```python
# Input: [{"volume": "410", "reporter": "U.S.", "page": "113"}]
# Output: [("U.S.", "410", "113")]
```

For Bluebook-style citations found in opinion text, use regex:

```python
CITATION_RE = r'(\d+)\s+([A-Z][a-z\.]+(?:\s+[A-Z][a-z\.]+)*)\s+(\d+)'
```

Unresolved citations (cited_opinion_id = NULL) are resolved later by a background matcher job.

---

## 4. Embedding Pipeline — Mixedbread mxbai-embed-large-v1

**Design doc reference:** TASKS 1103

### 4.1 Model Specs

| Property | Value |
|-|-|
| Model | `mixedbread-ai/mxbai-embed-large-v1` |
| Dimensions | 1024 |
| Max sequence length | 512 tokens |
| Query prefix | `"Represent this sentence for searching relevant passages: "` |
| Framework | sentence-transformers (local) or OpenAI-compatible API |

### 4.2 Embedding Service Refactor

The main app `EmbeddingService` keeps only `embed_public_query()` for runtime RAG queries. The MCP server has its own `MCPEmbeddingService` with batch capabilities.

#### Main App (`backend/app/services/embeddings.py`)

- `PUBLIC_EMBEDDING_MODEL` config changes from `BAAI/bge-small-en-v1.5` to `mixedbread-ai/mxbai-embed-large-v1`
- `PUBLIC_EMBEDDING_DIM` changes from `384` to `1024`
- `embed_public_query()` updated to use mxbai model with query prefix
- Backward-compatible: falls back to BGE if mxbai not available (for gradual rollout)

#### MCP Server (`mcp-server/mcp_server/embeddings.py`)

- `MCPEmbeddingService` class with three backends:
  - **local** (default): sentence-transformers in-process
  - **api**: OpenAI-compatible embedding API endpoint
  - **jetson**: Remote Jetson GPU for batch processing
- `embed_public_batch(texts, where_clause)` — batch embed with progress tracking
- `embed_public_query(text)` — single query embedding
- `reembed_all(batch_size=128)` — re-embed all rows where `embedding_version < PUBLIC_EMBEDDING_VERSION`
- Metadata enrichment: prepend `[{jurisdiction_type}] [{practice_area}] ` to chunk text before embedding (config: `PUBLIC_EMBEDDING_ENRICH_METADATA=true`)

### 4.3 Re-Embedding Migration

Existing `public_chunks` rows use BGE-384 embeddings (embedding_version=0). Migration plan:

1. Create `opinion_chunks` table with `Vector(1024)` column
2. Migrate all `public_chunks` rows → `opinion_chunks` (content, case_name, citation, court, date, chunk_index preserved)
3. Set `embedding = NULL`, `embedding_version = 0`
4. Run `embed_worker` to re-embed all 0-version rows with mxbai-1024
5. Build IVFFlat index on `opinion_chunks.embedding`
6. Update main app `search_public_chunks()` to query `opinion_chunks` instead of `public_chunks`
7. Drop or archive `public_chunks` table

### 4.4 Config Values (MCP Server)

```env
PUBLIC_EMBEDDING_MODEL=mixedbread-ai/mxbai-embed-large-v1
PUBLIC_EMBEDDING_DIM=1024
PUBLIC_EMBEDDING_BACKEND=local
PUBLIC_EMBEDDING_ENRICH_METADATA=true
PUBLIC_EMBEDDING_VERSION=1
PUBLIC_EMBEDDING_BATCH_SIZE=128
VECTORDB_URL=postgresql://legal_rag:...@vectordb:5432/legal_rag
```

---

## 5. MCP Tool Definitions

**Design doc reference:** TASKS 1105

### 5.1 Tool Registry

All tools are defined in `mcp-server/mcp_server/mcp_tools.py` with full JSON Schema.

#### `search_caselaw`

General-purpose semantic search across all case law with optional jurisdiction, practice area, and date filters.

```json
{
  "name": "search_caselaw",
  "description": "Search case law database with semantic similarity and optional filters.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Legal question or research query"},
      "top_k": {"type": "integer", "description": "Results to return (1-50, default 8)", "default": 8},
      "jurisdiction": {"type": "string", "description": "Filter: 'federal', 'state', or court_id (e.g., 'scotus', 'ca1')"},
      "practice_area": {"type": "string", "description": "Filter: practice area slug (e.g., 'constitutional', 'contract')"},
      "date_from": {"type": "string", "format": "date", "description": "Filter: decisions from this date (YYYY-MM-DD)"},
      "date_to": {"type": "string", "format": "date", "description": "Filter: decisions up to this date (YYYY-MM-DD)"}
    },
    "required": ["query"]
  }
}
```

SQL query pattern (with filters):
```sql
SELECT o.case_name, o.citation, c.full_name AS court_name, o.decision_date,
       oc.content, oc.chunk_index,
       1 - (oc.embedding <=> :vec::vector) AS similarity
FROM opinion_chunks oc
JOIN opinions o ON o.id = oc.opinion_id
JOIN courts c ON c.court_id = oc.court_id
WHERE oc.embedding IS NOT NULL
  AND oc.embedding_version = :current_version
  [:court_filter] [:practice_filter] [:date_filter]
ORDER BY oc.embedding <=> :vec::vector
LIMIT :top_k
```

#### `search_by_jurisdiction`

Scoped to a specific jurisdiction (federal circuit, state, or individual court).

```json
{
  "name": "search_by_jurisdiction",
  "description": "Search case law within a specific jurisdiction or court.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "jurisdiction": {"type": "string", "description": "Required. Court ID ('scotus'), circuit ('ca1'), state ('cal'), or type ('federal', 'state')"},
      "query": {"type": "string", "description": "Legal question"},
      "top_k": {"type": "integer", "default": 8}
    },
    "required": ["jurisdiction", "query"]
  }
}
```

Joins through `courts` table on `jurisdiction_type`, `jurisdiction_scope`, `circuit_or_state`, or direct `court_id`.

#### `search_by_practice_area`

Scoped to a legal practice area (contract, tort, criminal, etc.).

```json
{
  "name": "search_by_practice_area",
  "description": "Search case law within a specific practice area.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "practice_area": {"type": "string", "description": "Required. Practice area slug (constitutional, contract, tort, criminal, family, etc.)"},
      "query": {"type": "string", "description": "Legal question"},
      "top_k": {"type": "integer", "default": 8}
    },
    "required": ["practice_area", "query"]
  }
}
```

Uses `WHERE practice_areas @> :area::jsonb` filter.

#### `search_by_citation`

Exact citation lookup — returns the full opinion details.

```json
{
  "name": "search_by_citation",
  "description": "Look up an opinion by its citation (e.g., '410 U.S. 113'). Returns full opinion details.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "citation": {"type": "string", "description": "Full citation, e.g. '410 U.S. 113'"}
    },
    "required": ["citation"]
  }
}
```

Looks up in `opinions` table joined with `opinion_citations` for exact reporter+volume+page match.

#### `get_case_details`

Retrieve full opinion metadata, all chunks, and related citations.

```json
{
  "name": "get_case_details",
  "description": "Get full case details including metadata, all opinion chunks, and citation network.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "opinion_id": {"type": "string", "description": "CourtListener opinion ID"},
      "citation": {"type": "string", "description": "Citation as alternative lookup"}
    }
  }
}
```

#### `get_court_info`

Court profile: jurisdiction type, level, opinion count, date range of coverage.

```json
{
  "name": "get_court_info",
  "description": "Get court profile including jurisdiction type, opinion count, and date range.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "court_id": {"type": "string", "description": "Court ID (e.g., 'scotus', 'ca1', 'cal')"}
    },
    "required": ["court_id"]
  }
}
```

#### `search_similar_cases`

Find cases similar to a given opinion using embedding distance.

```json
{
  "name": "search_similar_cases",
  "description": "Find cases similar to a given opinion using semantic similarity.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "opinion_id": {"type": "string", "description": "Source opinion ID"},
      "citation": {"type": "string", "description": "Source citation as alternative lookup"},
      "top_k": {"type": "integer", "default": 8}
    }
  }
}
```

Uses the opinion's first chunk embedding as the query vector.

---

## 6. MCP Protocol Server

> **Implemented architecture:** LegalApp owns the SDK-backed, stateless
> Streamable HTTP endpoint at `/api/mcp` and negotiates protocol version
> `2025-06-18`. The private CourtListener process is a REST tool engine bound to
> the app network and accepts only `X-Clarity-Internal-Key`. It does not expose
> public SSE. Product clients use scoped `X-MCP-API-Key` credentials; the
> unscoped legacy `X-API-Key` and `/api/mcp/api-key` issuance routes are retired.
> Treat the remaining Sprint 11 text in this section as proposal history.

**Design doc reference:** TASKS 1106

### 6.1 Architecture

LegalApp exposes one official-SDK Streamable HTTP protocol route at `/api/mcp`.
It is stateless, negotiates protocol version `2025-06-18`, and implements
initialization, initialized notifications, tool discovery, and tool calls.

The separate `courtlistener-mcp` process is not a public protocol server. It is
a private REST tool engine on port 8021, reachable only from the app network.
The LegalApp backend authenticates to it with a dedicated internal credential.

### 6.2 Server Structure

```
mcp-server/
  mcp_server/
    __init__.py
    server.py          # private authenticated REST tool engine
    tools.py           # validated tool manifest
    repository.py      # CourtListener queries
    query_embeddings.py
    loader.py
    dispatcher.py
    embedding_scheduler.py
    database.py
  Dockerfile
```

### 6.3 Transport And Compatibility Routes

- `/api/mcp`: official SDK-backed Streamable HTTP lifecycle.
- `/api/mcp/manifest`: optional public metadata, hidden while disabled.
- `/api/mcp/tools/call`: authenticated REST compatibility adapter.
- `/api/mcp/product` and `/api/mcp/product-keys`: tenant administration.
- `/api/mcp/messages` and `/api/mcp/sse`: retired, HTTP 410.
- `/api/mcp/api-key`: retired unscoped issuance, HTTP 410.

### 6.4 Authentication

- Product clients send a scoped `X-MCP-API-Key` to LegalApp.
- Application users may use their normal JWT only on the internal compatibility
  path; their JWT is never forwarded upstream.
- The private CourtListener service accepts only `X-Clarity-Internal-Key`, whose
  value comes from `MCP_UPSTREAM_API_KEY` on both services.
- Legacy `X-API-Key` credentials are invalidated and rejected.

### 6.5 Release Gate

`MCP_PRODUCT_ENABLED=false` hides the protocol endpoint and manifest, prevents
key creation, and is mandatory for the first-customer release. Enabling it later
also requires active tenant, explicit entitlement, healthy billing, Stripe
customer/meter configuration, per-key limits, and production monitoring.

---

## 7. Nightly Ingest Scheduler

**Design doc reference:** TASKS 1104

### 7.1 MCP Server Scheduler

Runs on the MCP server container, independent of main app scheduler.

| Job ID | Schedule | Action |
|-|-|-|
| `cl-ingest` | Daily 3:00 AM ET | Incremental CourtListener API pull → ingest new/changed opinions → chunk → tag practice areas |
| `cl-embed` | Daily 3:30 AM ET | Embed all `opinion_chunks WHERE embedding IS NULL OR embedding_version < PUBLIC_EMBEDDING_VERSION` |
| `cl-stats` | Weekly Sun 4:00 AM ET | Count opinions/chunks/unembedded, court distribution, log to `ingest_runs` |

### 7.2 Manual Trigger Endpoints

Exposed on the MCP server REST API, proxied through main app admin:

- `POST /admin/cl/ingest/trigger` → manual incremental ingest
- `POST /admin/cl/embed/trigger` → manual embed batch
- `GET /admin/cl/status` → last ingest run, counts, court distribution
- `GET /admin/cl/ingest/history` → paginated ingest_runs

### 7.3 Main App Scheduler

No changes to existing jobs. The main app scheduler continues to run renewal-watcher, reg-monitor, task-reminders, etc. on its own APScheduler instance.

---

## 8. Usage Metering & Rate Limiting

> **Implemented architecture:** LegalApp enforces a Redis per-product-key burst
> limit and a transaction-serialized monthly key quota before tool execution.
> Successful external calls atomically write `mcp_usage_events` and enqueue a
> durable `mcp_stripe_meter` outbox job with a stable Stripe identifier. Product
> access also requires active tenant, entitlement, billing, Stripe customer, and
> metering configuration state. The older proposal below is retained only for
> historical context.

**Design doc reference:** TASKS 1107

### 8.1 Metering Flow

```
Client → LegalApp product gateway
  → resolve product key and recheck tenant/entitlement/billing state
  → enforce allowed tool, Redis burst limit, and monthly usage quota
  → call private CourtListener engine with the service credential
  → atomically write mcp_usage_events and durable Stripe outbox job
  → Return tool result
```

The private CourtListener engine never connects to the main application database
and never receives customer credentials. LegalApp owns policy and metering.

### 8.2 Rate Limiting

- **Default limits**: 60 requests/minute and 1000 successful calls/month per key.
- **Bounded configuration**: every product key has mandatory burst and monthly
  limits; neither may be unlimited.
- **Concurrency**: monthly quota checks use a PostgreSQL advisory transaction lock.
- **Availability**: the key limiter fails closed in production when Redis is down.

### 8.3 Admin Endpoints (on main app)

| Endpoint | Method | Description |
|-|-|-|
| `/api/platform/mcp` | GET | Cross-tenant product readiness and usage overview. |
| `/api/platform/tenants/{id}` | PUT | Explicitly manage entitlement and billing state. |

### 8.4 Tenant Self-Service

| Endpoint | Method | Description |
|-|-|-|
| `/api/mcp/product` | GET | Product state, keys, usage, and outbox status. |
| `/api/mcp/product-keys` | POST | Create one scoped, bounded key when every gate passes. |
| `/api/mcp/product-keys/{id}` | DELETE | Revoke a tenant product key. |
| `/api/mcp/usage` | GET | Tenant usage summary. |

---

## 9. Backfill & Migration Plan

### Phase 1: Schema Creation (Task 1101)

1. Create vectordb migration: `courts`, `opinions`, `opinion_citations`, `opinion_chunks`, `legal_topics`, `ingest_runs` tables
2. Seed `courts` table with ~200 CourtListener court entries
3. Seed `legal_topics` table with top-level legal taxonomy
4. Create indexes (including IVFFlat placeholder for embedding)

### Phase 2: Data Migration (Task 1101)

1. Migrate `public_chunks` → `opinion_chunks` + `opinions`:
   - Extract unique `(opinion_id, case_name, citation, court, decision_date)` from `public_chunks` → insert into `opinions`
   - For each `public_chunks` row, create `opinion_chunks` row with `court_id` resolved from `court` string, `practice_areas` from rule-based classification, `embedding_version=0`
   - Leave `embedding` as NULL initially (will be re-embedded)

### Phase 3: Re-Embedding (Task 1103)

1. Deploy mxbai-embed-large-v1 model on MCP server
2. Run `embed_worker` to batch-embed all `opinion_chunks WHERE embedding_version = 0`
3. Update `embedding_version = 1` for each embedded batch
4. Build IVFFlat index after all rows are embedded

### Phase 4: Cutover (Task 1103)

1. Update main app `search_public_chunks()` to query `opinion_chunks` + JOIN `opinions` + JOIN `courts`
2. Update main app `EmbeddingService` to use mxbai-1024 for public queries
3. Update `hybrid_rag_query()` to pass jurisdiction/practice_area filters to search functions
4. Drop or rename `public_chunks` table (keep for rollback)

### Phase 5: MCP Server Deployment (Tasks 1105-1107)

1. Deploy MCP server with SSE + REST transports
2. Wire main app proxy router
3. Enable metering and rate limiting
4. Test with Claude Desktop (SSE) and direct API calls (REST)

---

## 10. Configuration Summary

### Main App `.env` Additions

```env
# CourtListener (used by proxy for CL status endpoints)
COURTLISTENER_API_KEY=
COURTLISTENER_ENABLED=false

# Vector DB (already exists)
VECTORDB_URL=postgresql://legal_rag:password@vectordb:5432/legal_rag

# Public embedding model (changed from BGE)
PUBLIC_EMBEDDING_MODEL=mixedbread-ai/mxbai-embed-large-v1
PUBLIC_EMBEDDING_DIM=1024
PUBLIC_EMBEDDING_ENRICH_METADATA=true
PUBLIC_EMBEDDING_VERSION=1

# MCP proxy
MCP_SERVER_URL=http://courtlistener-mcp:8021
MCP_UPSTREAM_API_KEY=<dedicated-32+-character-service-secret>
MCP_PRODUCT_ENABLED=false
MCP_DEFAULT_BURST_LIMIT_PER_MINUTE=60
MCP_DEFAULT_MONTHLY_CALL_LIMIT=1000
STRIPE_MCP_METER_EVENT_NAME=mcp_product_key_calls
```

### MCP Server `.env`

```env
# Private CourtListener database
VECTORDB_URL=postgresql://legal_rag:password@vectordb:5432/legal_rag

# CourtListener
COURTLISTENER_API_KEY=
COURTLISTENER_API_BASE_URL=https://www.courtlistener.com/api/rest/v3/
COURTLISTENER_ENABLED=true

# Embedding
PUBLIC_EMBEDDING_MODEL=mixedbread-ai/mxbai-embed-large-v1
PUBLIC_EMBEDDING_DIM=1024
PUBLIC_EMBEDDING_BACKEND=local
PUBLIC_EMBEDDING_ENRICH_METADATA=true
PUBLIC_EMBEDDING_VERSION=1
PUBLIC_EMBEDDING_BATCH_SIZE=128

# Private service authentication
MCP_UPSTREAM_API_KEY=<same-secret-as-legalapp-backend>
```
