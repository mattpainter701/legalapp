# Clarity Legal — Data Architecture

How documents, context, and memory flow through the system. This doc covers the retrieval pipeline, storage decisions, tenant isolation, and the cloud-native search model.

---

## Three-tier retrieval model

The system answers user questions by assembling context from three independent sources, each with a different storage and retrieval strategy.

### Tier 1 — Session Attachments (zero embeddings)

**Trigger:** User drags a file into a chat conversation.

**Flow:**
```
File upload → save to UPLOAD_DIR/{tenant_id}/{document_id}
  → User sends message with attachment_ids: [doc_id, ...]
  → extract_text() on-demand (pypdf for PDF, python-docx for DOCX, UTF-8 for .txt)
  → Inject raw text into LLM context window (capped at 4000 chars per file)
  → LLM answers
  → Text discarded after response
```

**What's stored:** Only the original file on disk (for re-download). No embeddings, no chunks, no vector search.

**Why:** Like attaching a file in ChatGPT or Claude — the file is relevant to this conversation only. Embedding every drag-and-drop would waste GPU cycles and storage on transient context.

**Memory bridge:** If the conversation reveals durable information (client preferences, matter facts, workflow patterns), the memory service harvests it automatically every 10 messages and stores it in `UserMemory`.

### Tier 2 — Project / Matter Documents (full RAG)

**Trigger:** User explicitly uploads a document for permanent indexing (drag-and-drop in a matter workspace, or "Save to project").

**Flow:**
```
File upload → save to UPLOAD_DIR/{tenant_id}/{document_id}
  → Background task: _process_document()
    → extract_text() (via asyncio.to_thread, offloads CPU work)
    → chunk_text() (500-token chunks, 50-token overlap, tiktoken cl100k_base)
    → embed_batch() (OpenAI text-embedding-3-small, 1536-dim)
    → INSERT into pgvector `chunks` table
  → Document.status = "indexed"
  → Chunks are now RAG-searchable
```

**At query time:**
```
User question → embed_text(question) → pgvector cosine similarity (<=>)
  → SELECT top_k chunks WHERE tenant_id = ? ORDER BY similarity
  → build_rag_context() with case names, citations, relevance scores
  → Inject into LLM context
```

**What's stored:** Full document text in pgvector chunks, embeddings, metadata (case_name, citation, court, decision_date).

**Why:** These are the firm's authoritative documents — engagement letters, precedent, templates, key cases. They should be permanently searchable across all sessions and matters.

### Tier 3 — Cloud Search / Live RAG (metadata only)

**Trigger:** Tenant has connected Google Workspace or Microsoft 365 in onboarding. Cloud search is enabled.

**Flow:**
```
User question
  → RetrievalPlanner.plan(question)
    → LLM decides: should_search? which sources? what keywords?
    → Output: {"sources":["gmail","drive"],"keywords":["renewal","Acme"],...}
  → CloudSearchService.search(plan)
    → Google Drive: GET /drive/v3/files?q=fullText contains 'keyword'...
    → Gmail: GET /gmail/v1/users/me/messages?q=from:acme.com...
    → Microsoft Graph: POST /search/query {entityTypes: [driveItem,message]}
    → Returns ranked CloudHit objects (snippet-only, no full text yet)
  → CloudSearchService.fetch_contents(top_N_hits)
    → Fetch full text for top N results from provider API
    → Truncate to 2000 chars each
  → build_cloud_context() → merge with pgvector context
  → LLM answers
  → Content discarded after response
```

**What's stored locally (cloud_metadata_index table):**

| Column | Purpose |
|-|-|
| `object_id` | Provider-native ID (Drive file ID, Gmail message ID, Graph item ID) |
| `title` | File name or email subject |
| `snippet` | First 500 characters — routing hint, never full content |
| `modified_time` | For sync freshness |
| `owner_email`, `participants` | For access control and people-filtered queries |
| `mime_type`, `size_bytes` | For type/size filtering |
| `web_url` | Direct link to open in cloud |
| `sync_cursor` | Delta token for incremental sync |

**What's never stored:** Full document bodies, email bodies, attachment contents, or embeddings of cloud data.

**Why:** A firm might have 4TB of records in Google Drive. Ingesting everything is impractical, expensive, and creates a data residency problem. Cloud search treats the customer's own cloud as the retrieval index — we only store routing metadata. Customer data stays in the customer's tenant. Offboarding means deleting metadata rows and revoking OAuth — no data migration needed.

---

## Context assembly order

When a chat message is processed, context is assembled in this priority:

```
1. Conversation history (last 10 messages)
2. Session attachments (if attachment_ids provided)
3. Matter context (if matter_id provided — cached)
4. pgvector RAG chunks (tenant documents + public CourtListener)
5. Cloud search hits (if tenant has active integrations)
6. User memory context (from memory_service)
```

Attachment text and matter context appear first because they're the most directly relevant. RAG and cloud search provide supporting legal authority. Memory provides user-level preferences and learned patterns.

---

## Memory system

The memory service bridges sessions without requiring document embeddings.

**Auto-memory (every 10 messages):**
```
Conversation reaches 10 messages
  → summarize_conversation()
    → LLM reads transcript (truncated to 2000 chars)
    → Produces summary + key facts
    → Stored as UserMemory (type: interaction_pattern)
  → update_user_memory_summary()
    → Concatenates last 5 interaction_patterns + all preferences
    → Writes to User.memory_summary (raw text)
```

**Injection at query time:**
```
get_memory_context_for_injection()
  → Reads User.memory_summary
  → Appends 3 most recent interaction_pattern memories
  → Returns formatted string
  → Injected into LLM system prompt as "USER CONTEXT"
```

**What memory captures (without storing documents):**
- Client names and preferences mentioned across sessions
- Practice area expertise patterns
- Workflow preferences ("always use California law", "prefer email over Slack")
- Matter facts surfaced in conversation (harvested from context, not from files)

---

## Cloud integration flow

### OAuth token model

```
┌─────────────────────────────────────────────────────┐
│                 Token Hierarchy                      │
├─────────────────┬───────────────────────────────────┤
│ TenantCredential│ Admin-consented org-wide access    │
│ (per tenant)    │ Used for: user dir sync, cloud     │
│                 │ folder init, tenant-level search   │
├─────────────────┼───────────────────────────────────┤
│ UserOAuthToken  │ Per-user delegated access          │
│ (per user)      │ Used for: reading user's mail,     │
│                 │ calendar, drive files              │
└─────────────────┴───────────────────────────────────┘
```

Both token types are Fernet-encrypted at rest (`TOKEN_ENCRYPTION_KEY`). `token_vault.py` handles transparent refresh — services call `get_fresh_token()` / `get_fresh_user_token()` and always get a valid access token.

### Cloud search token fallback

```
CloudSearchService.search()
  → Try get_fresh_user_token() first (narrower scope, safer)
  → Fall back to get_fresh_token() (tenant-level, broader)
  → Missing token → skip that provider, return empty results
  → Never fails the chat — cloud search is additive
```

---

## Tenant isolation

Every query path enforces tenant isolation at two layers:

**Application layer:** `TenantMiddleware` extracts JWT from cookies, sets `request.state.tenant_id`. All router handlers call `set_tenant_context(db, tenant_id)`.

**Database layer:** PostgreSQL Row Level Security. Every tenant-scoped table has:
```sql
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {table}
  USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
```

`FORCE ROW LEVEL SECURITY` means even the table owner can't bypass the policy. Unauthenticated requests see empty result sets (not errors) because of `true` (missing_ok).

This applies to: documents, chunks, conversations, messages, contacts, tasks, matters, practice_profiles, prompt_overrides, cloud_metadata_index, and all billing records.

---

## Cache strategy

| Cache Type | Key Format | TTL |
|-|-|-|
| RAG results | `rag:{tenant_id}\|{user_id}\|{question[:50]}` | 900-3600s (expertise-based) |
| LLM responses | `llm:{tenant_id}\|{user_id}\|{question[:50]}\|{context_hash[:16]}` | 300-1800s |
| Matter context | `matter:{tenant_id}\|{matter_id}` | 1800-7200s |
| Prompt overrides | `prompt:{tenant_id}\|{plugin}\|{skill}` | 3600s (overrides) / 300s (defaults) |
| Cloud search | `cloud_search:{tenant_id}\|{md5(question)[:16]}` | 300s |

Expertise-aware TTLs: junior users get longer caches (they ask repetitive questions), senior users get shorter caches (they ask novel questions). Skill-based multipliers further adjust TTLs (commercial contract review: ×1.5, AI governance: ×0.6).

---

## Feature flags

Per-tenant configuration in `TenantSettings`:

| Flag | Default | Controls |
|-|-|-|
| `enable_auto_memory` | true | Auto-memory generation every 10 messages |
| `enable_pii_detection` | true | PII scanning on user input + matter context |
| `enable_skill_routing` | true | Plugin skill routing in chat |
| `enable_matter_context` | true | Matter metadata injection |
| `use_customer_llm` | false | Firm brings own Azure/Gemini key |
| `cache_enabled` | true | Master cache toggle |
| `cache_ttl_multiplier` | 1.0 | Per-tenant TTL scaling (0.5-2.0) |

Cloud search is controlled globally by `CLOUD_SEARCH_ENABLED` (env var) — it's additive and non-blocking, so safe to enable for all tenants with connected integrations.

---

## Diagram: end-to-end chat flow

```
User: "Find the Acme renewal discussion and check if the SOW conflicts with our playbook"

  1. TenantMiddleware → JWT → tenant_id, user_id
  2. set_tenant_context() → PostgreSQL RLS active
  3. Save user message
  4. Load conversation history (last 10 messages)
  5. Load session attachments → extract text → prepend to context
  6. Load matter context (cached) → prepend to context
  7. pgvector RAG:
     a) embed_text("renewal Acme SOW playbook") → 1536-dim vector
     b) SELECT FROM chunks WHERE tenant_id=? ORDER BY embedding <=> vec LIMIT 8
     c) embed_public_query(same) → 384-dim vector (BGE-small)
     d) SELECT FROM public_chunks ORDER BY embedding <=> vec LIMIT 8
  8. Cloud search (in parallel with #7):
     a) RetrievalPlanner → {"sources":["gmail","drive"],"keywords":["renewal","SOW","Acme"]}
     b) Gmail API → messages?q=renewal SOW Acme → 4 hits
     c) Drive API → files?q=fullText contains 'SOW' → 3 hits
     d) fetch_contents(top 5) → 5 x 2000-char excerpts
     e) build_cloud_context() → formatted with source URLs
  9. Merge contexts: attachments + matter + RAG + cloud
  10. Load memory context: User.memory_summary + last 3 interaction_patterns
  11. LLM.complete(history + merged_context + memory_context)
  12. apply_guardrails() → PII scrub, AI disclosure check
  13. Save assistant message with sources, citations, relevance scores
  14. Record UsageRecord (tokens, cost, cache hits, RAG metadata)
  15. If message_count % 10 == 0 → trigger auto-memory generation

  Total latency: ~2-4s (embeddings + pgvector + cloud APIs run in parallel)
```
