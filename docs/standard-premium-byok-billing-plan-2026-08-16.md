# Standard, Premium, BYOK, and MCP commercial plan

## Decision summary

- **OpenCode Go** is a development and synthetic-benchmark source only. It is
  not a customer-serving Standard route: its consumer terms limit use to the
  customer's internal use, and its capacity and geography are not suitable for
  an external legal-product promise.
- **Standard** is a low-cost, platform-funded *general legal information*
  experience. `deepseek-v4-flash` is the initial price/performance candidate,
  not an automatic production approval.
- **Premium** is a platform-funded, privacy-qualified route with an included
  allowance and metered overage. The platform sells LawHand's product result,
  never raw upstream tokens or provider access.
- **BYOK** lets a firm connect its own approved enterprise provider account.
  The firm pays the upstream provider directly; LawHand charges the SaaS/BYOK
  product fee rather than marking up tokens it did not buy.
- **MCP** remains a separately metered legal-authority product. A customer can
  use it from Claude, GPT, Cursor, or another client with that customer's model
  account; LawHand must not proxy the customer's model API key through MCP.

## Data classes and route eligibility

| Class | Examples | Eligible route |
| --- | --- | --- |
| `synthetic` | benchmark prompts, generated fixtures | approved test providers, including OpenCode Go |
| `public_general` | public legal information with no tenant or matter context | qualified Standard candidate |
| `client_confidential` | matter facts, names, uploaded documents, attorney work product | qualified Premium or tenant BYOK only |
| `regulated` | PHI, client-contract restricted data, special-category data | tenant BYOK or a separately contracted/approved platform route only |

PII detection is a guardrail, not a guarantee. The public Standard path must
not attach tenant documents, matter memory, prior private chat, or confidential
retrieval context. It must reject or require a safer route when the input is
flagged; it must not claim that automated redaction makes a provider safe for
confidential work.

## Standard: DeepSeek V4 Flash qualification

`deepseek-v4-flash` is commercially attractive: the current direct API price
is listed as $0.14/M uncached input tokens and $0.28/M output tokens, with an
OpenAI-compatible API. The model supports a non-thinking mode, which should be
the Standard default to bound latency and output-token cost.

Before activation, record and approve for the exact direct endpoint:

1. commercial SaaS/resale terms and account ownership;
2. processing/storage geography, retention, training, subprocessors, DPA, and
   incident/deletion terms;
3. a legal-quality benchmark and 20 full-context synthetic canaries;
4. a hard request budget: input, output, tool, and timeout limits;
5. an explicit `public_general` data-class policy, with no silent fallback to
   another provider.

If any qualification item is unknown, direct DeepSeek remains benchmark-only.

## Premium: platform-funded usage pricing

Do not enable an arbitrary 100% markup until actual cost reconciliation exists.
For every completed request, persist:

- tenant, user, product route, immutable route revision, and request ID;
- final provider, final model, endpoint, fallback count, and effective price
  snapshot version;
- input, cached-input, output, and reasoning tokens;
- provider cost, customer charge, and calculated contribution margin;
- a metadata-only failure/cancellation record when no completion occurs.

The billing worker should create a Stripe meter event only after the final
provider usage reconciles. Unknown cost blocks normal activation; it never
becomes a $0 cost or a customer charge. Pricing policy must support an included
monthly allowance, overage units, a minimum operation charge, and a
finance-configured margin floor.

## BYOK

Replace the current narrow Gemini/Azure path with a deliberate tenant BYOK
product:

1. provider adapters for direct OpenAI, Azure OpenAI, Anthropic, and other
   individually approved providers;
2. per-tenant encrypted keys, provider/endpoint allowlists, key test/rotation,
   and no secret in logs, browser payloads, LiteLLM admin, or support traces;
3. a firm-selected data policy and a direct route with no platform fallback
   unless the firm explicitly authorizes one;
4. tenant-visible upstream-account ownership and a LawHand BYOK/SaaS charge;
5. provider-specific DPA, region, retention, and enterprise-account checks.

The existing BYOK schema and direct-routing code are migration material, not a
customer launch surface.

## MCP product boundary

LawHand's MCP server offers scoped legal-authority tools. It can charge for
tool calls while the customer's Claude/GPT/Cursor account pays for inference.
It must not accept, store, or forward a customer's model-provider API key. A
separate future LLM gateway API would require its own tenancy, pricing, abuse,
and upstream commercial review.

## Delivery order

1. Add route data-class enforcement and prevent tenant/matter context from
   reaching `public_general` Standard.
2. Add provider/endpoint qualification records and a DeepSeek Flash synthetic
   benchmark/canary runner.
3. Reconcile actual LiteLLM/provider usage into a new immutable cost/charge
   ledger; remove static model-name pricing and the fixed 10x multiplier.
4. Add Premium entitlements, allowance preflight, Stripe metering, and a clear
   exhaustion outcome (never a hidden Standard downgrade).
5. Design and ship the tenant BYOK surface with direct-provider adapters.
6. Keep MCP tool billing isolated from LLM-provider billing.

## Acceptance gates

- Standard rejects flagged/confidential requests and proves it attaches no
  tenant-private retrieval context.
- Every active platform route has a recorded endpoint qualification and current
  price snapshot.
- 99%+ of billable requests reconcile final provider/model/tokens/cost/charge.
- Premium never silently falls back to a free or Standard route.
- A BYOK request reaches only the tenant-approved direct endpoint and records
  no customer key or raw prompt in logs.
