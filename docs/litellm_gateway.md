# LiteLLM Gateway Operations

LiteLLM is the primary LLM execution gateway when `LITELLM_ENABLED=true`.
LegalApp remains the control plane for tenant policy, legal context assembly,
guardrails, usage records, and operator decisions.

## Required Secrets

Set these in the deployment environment. Do not commit real values.

- `LITELLM_API_KEY`: LiteLLM master key used by LegalApp to call the gateway.
- `LITELLM_DB_PASSWORD`: password for the dedicated `litellm-postgres` container.
- `LITELLM_DATABASE_URL`: synchronous Postgres URL for LiteLLM spend/log tables.
- Provider keys used by `litellm_config.yaml`, such as `DEEPSEEK_API_KEY` and
  `OPENROUTER_API_KEY`.

## App Routing

When enabled, the app fallback route uses separate standard and premium
profiles:

- Standard: `LITELLM_STANDARD_MODEL` (default `clarity-standard`)
- Premium: `LITELLM_PREMIUM_MODEL` (default `clarity-premium`)

Operators can still override global or per-tenant standard/premium routes in
the operator console. Direct providers remain available as emergency fallbacks.

## Gateway Profiles

`litellm_config.yaml` defines these operator-selectable aliases:

- `clarity-standard`: primary standard profile, using OpenCode Zen with
  `DEEPSEEK_API_KEY` for the OpenAI-compatible key. Fallbacks:
  `clarity-standard-openrouter-free`, then
  `clarity-standard-openrouter-deepseek`.
- `clarity-premium`: primary premium profile, using OpenCode Go through the
  OpenAI-compatible base URL. Fallbacks: `clarity-premium-openrouter`, then
  `clarity-premium-openrouter-qwen`.

The operator console should point global standard and premium routes at these
aliases by setting provider `litellm` and model `clarity-standard` /
`clarity-premium`.

## Privacy Defaults

`litellm_config.yaml` sets `turn_off_message_logging: true`. Keep raw
prompt/response logging disabled by default for legal customer data. Prefer
metadata-only observability: tenant ID, user ID, operation type, route, model,
tokens, latency, cost, error class, and gateway request ID.

If a tenant-specific debug mode is added, it must be short-retention, explicit,
audited, and visible in operator logs.

## Deployment Notes

Local development exposes LiteLLM on `http://localhost:4000` and the dedicated
LiteLLM database on localhost port `5435`.

Production should run LiteLLM behind the internal Docker network only. Do not
expose port 4000 publicly. Nginx should not route public traffic to LiteLLM.

Use pinned image tags for production once a tested version is selected; the
current compose file uses the upstream `main-latest` image as starter
scaffolding only.
