# LiteLLM Gateway Operations

LiteLLM is the primary LLM execution gateway when `LITELLM_ENABLED=true`.
LegalApp remains the control plane for tenant policy, legal context assembly,
guardrails, usage records, and operator decisions.

## Required Secrets

Set these in the deployment environment. Do not commit real values.

- `LITELLM_API_KEY`: LiteLLM master key used by LegalApp to call the gateway.
- `LITELLM_SALT_KEY`: dedicated permanent encryption key for secrets stored in
  the LiteLLM database. Generate it once, back it up, and do not change it when
  rotating `LITELLM_API_KEY`.
- `LITELLM_DB_PASSWORD`: password for the dedicated `litellm-postgres` container.
- `LITELLM_DATABASE_URL`: synchronous Postgres URL for LiteLLM spend/log tables.
- `STORE_MODEL_IN_DB=True`: required for operator-console route saves and
  reloads through LiteLLM `/config/update`.
- `DEEPSEEK_API_KEY`: required provider key for the standard and premium
  primary routes; production preflight rejects an absent or placeholder value.
- `OPENROUTER_API_KEY`: optional fallback provider key. The current Premium
  availability chain can fall back through Standard even when OpenRouter is
  unavailable.

## App Routing

When enabled, the app fallback route uses separate standard and premium
profiles:

- Standard: `LITELLM_STANDARD_MODEL` (default `clarity-standard`)
- Premium: `LITELLM_PREMIUM_MODEL` (default `clarity-premium`)

Operators can still override global or per-tenant standard/premium routes in
the operator console. Direct providers remain available as emergency fallbacks.

### Customer Route Capacity Policy

Standard and Premium are customer-facing, operator-managed products. The route
activation API rejects an explicitly free model in any primary, alternate, or
fallback position. The same policy runs on manual reload, so a previously saved
free target cannot bypass validation. Rejected attempts return HTTP 409 and are
written to the metadata-only operator audit log.

Free models may still be discovered and exercised with the synthetic provider
test for lab evaluation. If a lab route is added later, it must use a separate
non-customer alias. Do not place it behind Standard or Premium. An emergency
override is not implemented: a future override must be time-limited, enforce
expiry in the serving path, and automatically roll back to a qualified revision.

This API policy does not silently rewrite legacy file-backed aliases. Before a
production release, migrate any free targets in `litellm_config.yaml` to qualified
paid capacity, canary both customer aliases, and retain a tested rollback revision.
Models with missing or ambiguous price metadata still require operator review;
strict unknown-price qualification is tracked in BK24 `AIP-04`.

## Gateway Profiles

`litellm_config.yaml` defines these operator-selectable aliases:

- `clarity-standard`: primary standard profile, using OpenCode Zen with
  `DEEPSEEK_API_KEY` for the OpenAI-compatible key. It fails over inside
  LiteLLM to `clarity-standard-deepseek-flash-free`.
- `clarity-premium`: primary premium profile, using OpenCode Go through the
  OpenAI-compatible base URL. If Premium capacity is unavailable, LiteLLM
  falls back to the configured `clarity-standard` chain so the customer
  request remains available. Operators should disclose that a fallback may
  return standard-tier quality while the requested alias remains Premium.

The operator console should point global standard and premium routes at these
aliases by setting provider `litellm` and model `clarity-standard` /
`clarity-premium`.

## Privacy Defaults

`litellm_config.yaml` sets `turn_off_message_logging: true` and does not enable
success/failure callbacks by default. Keep raw prompt/response logging disabled
for legal customer data unless there is a short, explicit, audited support
exception.

LegalApp gateway telemetry is metadata-only by default. `LLMService` accepts a
`gateway_metadata` dict and strips it to these fields before sending it to
LiteLLM: `tenant_id`, `user_id`, `conversation_id`, `operation_type`,
`matter_id`, `plugin`, `skill`, and `premium`. Do not add prompt, response,
message, context, attachment text, or document content to this metadata.

App-side usage and debug tables also suppress raw prompt text by default:

- `usage_records.query_text`: null unless `GATEWAY_RAW_TEXT_RETENTION_ENABLED=true`
- `mcp_usage_events.query_text`: null unless `GATEWAY_RAW_TEXT_RETENTION_ENABLED=true`
- `error_logs.query_text`: null unless `GATEWAY_RAW_TEXT_RETENTION_ENABLED=true`

Retention defaults:

- `GATEWAY_LOG_RETENTION_DAYS=30`
- `GATEWAY_DEBUG_LOG_RETENTION_DAYS=7`
- `GATEWAY_SPEND_LOG_RETENTION_DAYS=365`

If a tenant-specific debug mode is added, it must be short-retention, explicit,
audited, and visible in operator logs.

## Operator Audit

Operator LLM actions write metadata-only entries to `operator_audit_logs`.
Current audited actions:

- `llm.routes_saved`: global standard/premium route changes and LiteLLM reload result.
- `llm.routes_activation_blocked`: rejected customer-route activation or reload,
  with policy reason and provider/model placement only.
- `llm.provider_disabled`: provider key removal/disablement.
- `llm.model_tested`: synthetic provider/model test result, latency, and token counts.

Future tenant debug-mode controls should call
`operator_debug_mode_audit_payload()` and record an `llm.debug_mode_changed`
entry. The payload must include only tenant/conversation IDs, enabled state,
retention days, and an operator reason. Never log prompt, response, context,
attachment, API key, or raw customer content in operator audit metadata.

## Deployment Notes

Local development exposes LiteLLM on `http://localhost:4000` and the dedicated
LiteLLM database on localhost port `5435`.

Production should run LiteLLM behind the internal Docker network only. Do not
expose port 4000 publicly. Nginx should not route public traffic to LiteLLM.
The image is digest-pinned. `litellm-migrator` applies upstream Prisma
migrations; `litellm-schema-migrator` then accepts either a zero diff or the
single reviewed 1.93 production repair. Any other drift blocks the proxy and
therefore the backend. The runtime uses `DISABLE_SCHEMA_UPDATE=true`, while the
proxy entrypoint repeats a read-only exact schema check on every process start,
including host reboot recovery. The production monitor independently requires
a zero diff and authenticated model discovery. Rehearse upgrades against a
restored database copy and never downgrade the image over a newer schema.

CourtListener MCP resale is handled by the LegalApp backend MCP product gateway,
not public LiteLLM exposure. See `docs/mcp_product_gateway.md` for the
`X-MCP-API-Key` product-key surface, usage logging, and quota controls.

### Docker Image Pinning

The Dockerfile pins the official LiteLLM registry image by immutable multi-arch
digest (currently version 1.93.0). To upgrade:

1. Check the [LiteLLM releases page](https://github.com/BerriAI/litellm/releases) and avoid versions covered by an active security advisory.
2. Back up the LiteLLM database and restore it into an isolated rehearsal instance.
3. Update the `FROM` digest in `litellm/Dockerfile` and the descriptive `image:` tag in both Compose roots.
4. Update the reviewed schema hash/diff repair only when an isolated restore proves the exact SQL, unchanged row counts, and a zero post-repair diff.
5. Prove authenticated model discovery and a real synthetic completion, then deploy.

Never point an older Prisma schema at the production database and never use a
mutable tag without a digest.

### Backend Startup Dependency

`docker-compose.yml` configures `backend` to wait for `litellm: service_healthy`
before starting. This prevents chat requests failing against an unready gateway.
If you disable LiteLLM (`LITELLM_ENABLED=false`) and want to skip running the
litellm container entirely, remove or comment out the `litellm` entry in the
backend `depends_on` block, or use a Docker Compose profile to exclude it.

The app's startup log will emit `LiteLLM gateway reachable` or a warning with
the connection error if LiteLLM is unreachable. Startup continues either way.

### Per-Tenant Spend Tracking

LiteLLM records token counts, costs, model names, and latency in its spend
tables (`LITELLM_DATABASE_URL`). LegalApp records tenant/user/conversation
metadata in `usage_records` and sends the metadata-only payload above to
LiteLLM for gateway-side correlation. Query LiteLLM spend tables plus
LegalApp usage records for cost and usage breakdowns without enabling raw
prompt logging.

Do not add `success_callback` or `failure_callback` to the default
`litellm_settings`. If callback telemetry is introduced later, it must be
metadata-only, keep `turn_off_message_logging: true`, and follow the retention
windows above.
