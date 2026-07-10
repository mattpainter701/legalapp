# MCP Product Gateway

LegalApp is designed to offer CourtListener MCP access through the LegalApp
backend, not by publicly exposing LiteLLM. It is not currently a public or
sellable product; the global release flag remains off until every gate in this
document has passed.

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

## Release State And Protocol

- Public MCP is disabled unless `MCP_PRODUCT_ENABLED=true`. Production must keep
  it false until protocol, billing, deployment, monitoring and restore gates pass.
- `/api/mcp` is the official SDK-backed Streamable HTTP endpoint and performs
  initialization, protocol negotiation and tool discovery.
- `POST /api/mcp/tools/call` is a compatibility REST adapter, not an MCP transport.
- `/api/mcp/messages` and `/api/mcp/sse` are retired and return HTTP 410.
- `GET /api/mcp/manifest` is optional metadata and returns 404 while the product
  flag is disabled.

The server baseline is MCP `2025-06-18`; the official SDK performs version
negotiation, initialization, `notifications/initialized`, `tools/list`, and
`tools/call`. Header API-key authentication is a product choice, not a promise
that every third-party MCP client can configure that header. Interoperability
must be proven with each client the commercial offering names.

External product calls use `X-MCP-API-Key`. Legacy unscoped `X-API-Key`
credentials are invalidated by migration 087 and issuance returns HTTP 410.

## Tenant Admin Controls

The `/mcp` admin surface can list historical keys and show disabled state while
the release flag is off. Creation remains fail-closed. After a future approved
release, tenant admins can:

- create named `clmcp_` keys
- choose allowed tools
- set bounded monthly and per-minute limits (neither can be unlimited)
- view 30-day usage totals and per-key usage
- revoke keys

Raw keys are shown only once. The database stores `key_hash` and `key_prefix`,
not raw secrets.

## Tool Catalog

The candidate CourtListener MCP scope list is:

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
- `auth_type`: `product_key`, `jwt`, or `internal_chat`
- `transport`: `streamable_http`, `rest`, or `internal`
- `tool_name`, `status_code`, `result_count`, `latency_ms`
- IP/user-agent for external calls

Quota enforcement is per key and counts successful calls in the current calendar
month. Redis enforces the per-key burst limit. Tool scope, tenant activity,
explicit MCP entitlement, billing state and Stripe metering configuration are
checked before proxying.

Stripe subscription/payment webhooks update the MCP billing state. Past-due,
unpaid, canceled, deleted, disabled, or suspended state is denied before tool
execution. Redis failure also denies product traffic in production rather than
falling back to a process-local limiter.

Successful external calls atomically enqueue a `mcp_stripe_meter` durable job in
the same database transaction as the usage event. The scheduler retries Stripe
delivery with a stable identifier; failed jobs remain inspectable and replayable.
This is durable at-least-once delivery with provider idempotency, not synchronous
confirmation that an invoice has already been updated. Operations must reconcile
usage rows, durable-job state, Stripe meter events, and invoices.

There is no prepaid-credit product today. Marketing and UI must describe usage
as metered only after commercial terms are finalized; a call is never represented
as drawing down credits unless a real credit ledger and pre-call balance gate ship.

## Deployment Notes

Migration `070_mcp_product_gateway.py` creates product keys and usage. Migration
`087_mcp_product_security.py` invalidates legacy keys, adds mandatory key limits,
and adds explicit tenant entitlement/billing states.

The backend and private CourtListener sidecar must share a distinct 32+ character
`MCP_UPSTREAM_API_KEY`. The sidecar rejects manifest and tool calls without it;
the backend never forwards application JWTs or customer API keys upstream.

Start the private sidecar under the same Compose project so it shares the
private network with the backend:

```bash
docker compose --env-file .env -p legalapp \
  -f docker-compose.courtlistener-mcp.yml \
  up -d --build courtlistener-db courtlistener-mcp
```

Host diagnostic ports must remain bound to `127.0.0.1`. The existing public
application origin exposes the future transport at `/api/mcp`; introducing a
new MCP hostname would require an explicit nginx, origin/host allowlist, TLS,
monitoring, and client-auth review.

## Product release checklist

Do not change `MCP_PRODUCT_ENABLED` until all evidence is recorded on the exact
release revision:

- official SDK lifecycle/transport suite and target-client interoperability
  through production nginx/TLS;
- legacy issuance and `X-API-Key` rejection after migration `087`;
- tenant deactivation, entitlement suspension, payment failure, cancellation,
  key revocation, tool denial, quota exhaustion, burst exhaustion, and Redis
  outage all fail before upstream execution;
- a successful call creates one usage row and one durable meter job, retry is
  idempotent, Stripe receives it, reconciliation agrees, and exhausted jobs
  alert;
- a dedicated upstream credential is present on both services and neither
  customer keys nor application JWTs appear in upstream logs;
- off-host backup and clean-host restore include MCP keys, usage, and durable
  jobs without exposing raw secrets;
- public readiness, scheduler, queue, HTTP, TLS, disk, database, and Redis
  alerts have a tested delivery and recovery path;
- published pricing, quota, billing, privacy, retention, support, and incident
  terms match the actual enforcement; and
- there is still no “prepaid credits” claim unless a durable credit ledger and
  pre-call balance reservation/gate are implemented and tested.

Protocol references:

- [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)
- [MCP transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [Official Python SDK](https://github.com/modelcontextprotocol/python-sdk)
