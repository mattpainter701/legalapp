# MCP Product Gateway

LegalApp sells CourtListener MCP access through the LegalApp backend, not by
publicly exposing LiteLLM.

## Runtime Shape

- Internal chat path: LegalApp chat calls `MCP_SERVER_URL` directly and records
  `mcp_usage_events.auth_type='internal_chat'` under the tenant.
- External product path: clients call the public LegalApp MCP route with
  `X-MCP-API-Key: clmcp_...`.
- CourtListener engine: `courtlistener-mcp` remains private to the app network
  and handles tool execution against the CourtListener pgvector DB.
- LiteLLM remains the model gateway. Do not route public MCP customers directly
  to LiteLLM unless a later design deliberately adopts LiteLLM MCP Gateway
  virtual keys.

## Public Endpoints

- `GET /api/mcp` returns the tool manifest.
- `POST /api/mcp/tools/call` accepts the existing REST tool-call shape.
- `POST /api/mcp/messages` accepts JSON-RPC `tools/call` payloads.
- `GET /api/mcp/sse` returns a minimal SSE discovery event pointing clients at
  `/api/mcp/messages`.

External product calls must use `X-MCP-API-Key`. The legacy `X-API-Key` tenant
key remains for backward compatibility but is not the sellable product-key
surface.

## Tenant Admin Controls

Tenant admins manage product keys from `/mcp`:

- create named `clmcp_` keys
- choose allowed tools
- set an optional monthly call limit
- view 30-day usage totals and per-key usage
- revoke keys

Raw keys are shown only once. The database stores `key_hash` and `key_prefix`,
not raw secrets.

## Tool Catalog

The current sellable CourtListener MCP scope list is:

- `search_caselaw`: hybrid keyword/vector search over locally loaded authority.
- `get_case_details`: metadata and chunks for one `opinion_id` or `cluster_id`.
- `get_full_opinion`: complete locally loaded opinion text.
- `find_similar_cases`: similar-case lookup from a query, opinion, cluster, or chunk.
- `search_by_citation`: local citation table lookup.
- `validate_citation`: parse and report whether a citation resolves locally.
- `normalize_citation`: canonical citation fields for messy user input.
- `get_citation_network`: local cited/citing edges.
- `get_authority_treatment`: citation-history signal; not a Shepard's substitute.
- `search_by_jurisdiction`: search constrained by court/jurisdiction.
- `search_recent_authority`: search after a filing date.
- `get_court_info`: one court's metadata and local counts.
- `get_court_coverage`: loaded court/date/count coverage.
- `search_dockets`: local docket metadata search.
- `export_research_bundle`: structured cases/citations bundle for drafting workflows.
- `sync_status`: ingest and embedding progress.
- `corpus_status`: global corpus counts and coverage.

## Billing And Quotas

`mcp_usage_events` is the billing/monitoring source:

- `tenant_id`
- `product_key_id` for external keys, null for internal chat
- `user_id` for app-authenticated calls
- `auth_type`: `product_key`, `legacy_tenant_key`, `jwt`, or `internal_chat`
- `transport`: `rest`, `messages`, or `internal`
- `tool_name`, `status_code`, `result_count`, `latency_ms`
- IP/user-agent for external calls

Quota enforcement is per product key and counts successful calls in the current
calendar month. Tool scopes are checked before proxying to `courtlistener-mcp`.

## Deployment Notes

Migration `070_mcp_product_gateway.py` creates the product-key and usage-event
tables with tenant RLS. Apply migrations before exposing product-key creation.

Recommended public DNS shape:

- `mcp.legalapp.example.com` -> nginx/cloudflare route -> backend `/api/mcp`
- keep `courtlistener-mcp`, `courtlistener-db`, and LiteLLM private
