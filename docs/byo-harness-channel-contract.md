# BYO-harness and metered LawHand AI

Owner decision: 2026-09-08. Accepted direction; broad capability coverage remains
implementation work, not a claim that every portal function is an MCP tool.

## Channels and costs

BYO-harness is a supported platform channel, alongside LawHand's owned assistant.
The target is assistance across almost every portal, matter, and firm-management
workflow through `https://mcp.getlawhand.com/api/mcp/workspace`, and research
through `https://research.getlawhand.com`. The products retain their distinct
identities, grants, scopes, entitlement and audit boundaries. A research grant
does not grant access to a firm's matter data.

| Operation | Inference and metering owner |
| --- | --- |
| External assistant reasons over authorized Workspace results | Customer's assistant account; LawHand enforces infrastructure budgets |
| Ordinary Workspace read or deterministic reviewable proposal | No AI debit merely for using MCP; existing feature entitlements still apply |
| LawHand-run premium PDF templating or other model-backed preparation | Existing LawHand feature price, admission and usage accounting |
| LawHand Standard/Premium research inference | Existing research tier, entitlement and usage accounting |
| Research retrieval | Existing Research MCP entitlement and quotas; external inference does not make hosted retrieval free |

A harness connection never substitutes for a paid feature entitlement, turns a
metered operation into an unmetered one, or silently authorizes a Premium
fallback. New model-backed tools must expose their service/tier and cost contract
before invocation and reuse the same server-side admission and settlement path
as the portal. External reasoning and LawHand inference must not be double-billed.
Customer-supplied provider API keys are a separate route/credential concept.

## Coverage rule

For each portal workflow, identify its shared capability and expose authorized
reads and reviewable proposals where supported. Reuse business logic, current
RBAC, tenant isolation, feature policy and audit evidence across channels.
"Almost every function" is the coverage goal, not permission to export all data
or grant arbitrary execution. Approval and privileged administration remain
authenticated human controls; irreversible effects use deterministic workers.

The current [Workspace catalog](workspace_mcp_adapter.md) is authoritative for
implemented tools. Lifecycle triggers, synthesized firm configuration, staged
artifact review and durable multi-step runs are implemented in W1–W4. The
synthesis controls currently use the authenticated portal/API. Broader portal
administration and PDF preview-bound MCP proposals remain outside the catalog.
Research coverage
is maintained separately in the [MCP documentation index](mcp/README.md).

## Read-load admission

Workspace admission has an additional Redis-atomic, minute-window budget of
60 actual tool calls per consent grant, shared across refreshed tokens and API
workers. Existing token and tenant transport limits still apply; tool calls also
share the configured tenant request ceiling. Settings:

- `WORKSPACE_MCP_GRANT_CALLS_PER_MINUTE` (default 60, range 1–600).
- `WORKSPACE_MCP_READ_RESULT_MAX_BYTES` (default 1 MiB, range 1 KiB–8 MiB).
- `WORKSPACE_MCP_GRANT_READ_BYTES_PER_MINUTE` (default 8 MiB, range 1 KiB–256 MiB).
- `WORKSPACE_MCP_TENANT_READ_BYTES_PER_MINUTE` (default 64 MiB, range 1 KiB–1 GiB).

The result ceiling counts UTF-8 JSON for both MCP result representations and
their text escaping. It excludes the outer JSON-RPC framing. Oversized reads
return `result_size_exceeded` without content or partial/truncated evidence.
Narrow the query or lower its result limit. Grant exhaustion returns
`rate_limited` with `retry_after_seconds`. Production fails closed when Redis is
unavailable. A token refresh does not reset a grant's counter. Separate grants
share the tenant ceiling. These are infrastructure limits, not AI balance checks.

Read audit events include result byte counts; refusals include a bounded error
code. Neither records query text or returned document bodies. Proposal results
are not rejected after materializing a cloud artifact; their existing bounded
input/output contracts and the grant call budget apply. Aggregate byte admission
atomically checks both grant and tenant before reserving either counter. Refused
reads consume no byte reservation. Durable MCP run reads use the same budgets;
exhaustion blocks at the read checkpoint until the user continues the run.
These deployment settings are shared defaults, not per-firm editable pricing.

The [firm setup and activity guide](mcp/harness-firm-setup.md) covers connections,
bounded audit history, exact review evidence and supported client setup. Full
production TLS interoperability remains a release gate.

## Governance

Firm enablement, per-user access, explicit OAuth consent, scopes, live RBAC,
revocation and Privacy Mode apply to the external channel. An assistant's personal
subscription is not a LawHand assurance about firm confidentiality. The firm
decides which external accounts and vendors it permits. This decision neither
mandates a particular subscription tier nor enables unattended runs.

W2's revision-bound review spine and W4's durable runtime are implemented.
Named service identities and approved scheduled rules still gate unattended
operation. The [scaling plan](capability-first-automation-scaling-plan-2026-09-08.md)
tracks the remaining workstreams and acceptance conditions.
