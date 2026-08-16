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

## PII firewall and end-user privacy control

### Product decision

Add a user-facing **Protect private details** toggle in Chat and the user
profile. It expresses the user's preference to minimize detected personal
details before a provider call; it is **not** permission to use a lower-trust
route with confidential work. The server's route/data-class policy is always
stricter than the toggle.

For the initial launch:

- Standard is always protected. Its toggle is shown as on and cannot weaken
  Standard's enforced policy.
- Premium and approved BYOK honour the user's choice to request detected-PII
  redaction, while their provider qualification determines whether confidential
  context is eligible at all.
- A tenant administrator can choose the default, require protection for the
  tenant, or disable private-context features. A user cannot override an
  administrator or route restriction.
- The existing `privacy_mode` setting is migration material, but it must be
  replaced by an explicit policy result rather than used as the sole switch.

Copy must be precise: “We remove detected details before a public/general
model request; detection is not perfect and does not make client or matter
information suitable for this route.” Never describe it as anonymous, secure,
or legally sufficient by itself.

### Route policy before LiteLLM

LiteLLM's `drop_params: true` only removes unsupported API parameters. It does
not inspect, redact, or prevent storage of prompt content. Build a server-side
**pre-provider privacy firewall** before any LiteLLM or direct-BYOK call:

1. Resolve the final route and its approved data class before loading
   retrieval or composing a system prompt.
2. Build a typed `ContextEnvelope`, where every candidate input has a source
   and classification: current message, conversation history, user profile,
   learned memory, tenant identity, matter, attachment, firm retrieval, cloud
   retrieval, plugin/tool result, and public authority.
3. Apply a policy decision of `allow`, `redact`, `block`, or `reroute`; build
   the provider payload only from allowed/redacted items. No raw prompt may
   reach LiteLLM first and be cleaned afterward.
4. Run PII detection and redaction over every textual item that survives the
   structural policy—not only the latest user message. Record only counts,
   categories, policy result, and opaque request IDs; never raw values,
   redacted values, prompts, or provider payloads in analytics/error logs.
5. Apply the existing output detection/redaction boundary to the completed
   answer as defense in depth. It does not compensate for an unsafe input.

For `public_general` Standard, the policy removes—not merely regex-scrubs—all
private context: tenant/firm name, user name/profile, learned memory, prior
private history, active matter and its cloud folder, matter summaries/notes/
events/communications/budgets/team/client metadata, attachments, tenant RAG,
cloud search, email content, plugin or action-agent results, and hidden
system-prompt fields. The only optional augmentation is curated public legal
authority. The Standard system prompt must be a separate general-information
template, rather than the current firm/matter-aware template with empty fields.

When Standard detects PII in free-form text, it may continue only with the
redacted message and a visible “redacted general mode” state. It must not ever
fall back to raw text. A linked matter, an attachment, a tenant/firm retrieval
request, or a tool result is a hard block for Standard; the user must start a
general unlinked conversation or choose an approved Premium/BYOK route. This
is deliberately more conservative than attempting to sanitize matter context.

### Matter-context boundary

`MatterContextService` currently returns a formatted matter record even when
privacy mode is enabled; it redacts selected fields but can retain identifiers,
case metadata, team, budget, and other context. Treat every matter-derived
field as `client_confidential` for the Standard decision, irrespective of
whether the regex detector found a match.

- Enforce the restriction at the chat entry point and in each context loader:
  matter service, attachment builder, RAG/retrieval planner, cloud search,
  memory service, plugin/tool paths, action-agent transcripts, and background
  memory generation.
- Do not call these loaders, populate their caches, pass a `matter_id`/
  cloud-folder scope, or concatenate their results for Standard. A check only
  in the UI or only in `get_safe_matter_context` is insufficient.
- A Standard conversation must be unlinked from a matter for provider work.
  If the user has selected a matter, display a blocking card explaining that
  matter context requires Premium or firm BYOK, with actions to start a clean
  general conversation or change route. Do not silently omit the matter while
  leaving the user to believe it was considered.
- Premium/BYOK matter injection remains available only when the resolved
  provider/endpoint is qualified for `client_confidential`; the user privacy
  preference can redact detected values but cannot promote an ineligible
  endpoint.

### UX states

Place the control next to the route selector in the composer and persist the
user preference in Profile. The UI must show the effective server policy,
not merely the switch position:

| Situation | Composer state |
| --- | --- |
| Standard, no private source selected | “General mode: detected personal details are removed before this request.” |
| Standard, matter/attachment/private retrieval selected | Block send; explain the source will not be sent and offer a clean chat or Premium/BYOK. |
| Premium or qualified BYOK, protection on | “Detected personal details will be redacted before provider processing where possible.” |
| Tenant policy requires protection | Toggle shown locked, with the firm policy explanation. |

The composer should also show a lightweight local warning before send when it
recognises likely PII, but the server decision is authoritative. Do not expose
detected values in the browser telemetry or trust client-side detection for
enforcement.

### Implementation and verification sequence

1. Define the data-class/route policy object and `ContextEnvelope`; add an
   immutable policy revision to request/usage metadata.
2. Refactor non-streaming and streaming chat to resolve policy before any
   private loader, then assemble the dedicated Standard or confidential prompt.
3. Apply the same firewall to skills, plugins, action-agent second passes,
   memory jobs, direct BYOK calls, and every future inference entry point.
4. Add the user preference, tenant default/lock controls, route-state API, and
   composer/Profile UX. Backfill existing users to the tenant default; make
   Standard protected from day one.
5. Add a provider-payload test seam around LiteLLM and direct BYOK. Test that
   synthetic SSNs, emails, names, tenant labels, matter data, documents,
   cached history, cloud hits, and tool output cannot appear in a Standard
   outbound body or log record. Test both streaming and non-streaming paths,
   blocked-state UX/API responses, policy precedence, and no raw fallback.
6. Run only synthetic fixtures in shadow/canary validation. Turn on strict
   enforcement before enabling the public Standard provider; measure policy
   outcomes and false positives using metadata, not retained prompt text.

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
