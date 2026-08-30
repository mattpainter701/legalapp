# Shared Cloudflare configuration

## Purpose

This document defines how Cloudflare configuration is shared across GitHub projects without copying credentials into source control, agent memory, or documentation.

GitHub Actions configuration variables are the automation source of truth for nonsecret identifiers. GitHub Actions secrets are the only GitHub location for credential values.

## GitHub account constraint

The repositories currently live under the personal namespace `mattpainter701`. GitHub Actions supports shared organization variables and secrets, but it does not provide an equivalent account-wide Actions scope for a personal namespace. Until the projects move into a GitHub organization, shared values must be synchronized to an explicit repository allowlist.

## Shared web repository allowlist

The generic Cloudflare variables are synchronized to these active web repositories:

- `mattpainter701/Varta_Systems_Website`
- `mattpainter701/perevagagroup`
- `mattpainter701/cybersafeadvisor.com`
- `mattpainter701/circuit-weaver-website`
- `mattpainter701/legalapp`

Do not infer that a newly created repository belongs on this list. Confirm its deployment model and least-privilege needs first.

## Generic variables

These nonsecret variables may be used by every repository in the web allowlist:

| Variable | Purpose |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Selects the shared Cloudflare account. |
| `CLOUDFLARE_R2_S3_ENDPOINT` | Identifies the account-level R2 S3-compatible endpoint. It is not an access credential. |

## LawHand-only variables

These nonsecret variables are scoped to `mattpainter701/legalapp`:

| Variable | Purpose |
| --- | --- |
| `LAWHAND_CLOUDFLARE_ZONE_ID` | Selects the `getlawhand.com` zone. |
| `LAWHAND_CLOUDFLARE_ZONE_NAME` | Records the canonical zone name. |
| `LAWHAND_CLOUDFLARE_TUNNEL_ID` | Selects the production Cloudflare Tunnel. |
| `LAWHAND_CLOUDFLARE_TUNNEL_NAME` | Records the production Tunnel name. |
| `LAWHAND_CLOUDFLARE_TUNNEL_TARGET` | Records the Tunnel CNAME target. |
| `LAWHAND_MCP_HOSTNAME` | Records `mcp.getlawhand.com` as the platform/workspace MCP hostname. |
| `LAWHAND_RESEARCH_MCP_HOSTNAME` | Records `research.getlawhand.com` as the reserved research-only MCP hostname. |

`research.mcp.getlawhand.com` is a legacy spelling and must not be introduced into new configuration.

## LawHand inbound email resources

These resources belong only to `mattpainter701/legalapp`. They are not shared
with the generic web repository allowlist.

| Resource | Canonical value | Purpose |
| --- | --- | --- |
| Email Routing subdomain | `intake.getlawhand.com` | Receives opaque per-matter addresses through isolated MX/SPF records. |
| Email Worker | `lawhand-inbound-email` | Validates the envelope and size, signs the raw MIME bytes, and posts them to LawHand. |
| Backend ingest path | `/api/inbound-email/cloudflare` | Accepts only timestamped HMAC-authenticated raw messages from the Worker. |
| Delivery secret name | `INBOUND_EMAIL_WEBHOOK_SECRET` | Shared secret name in the GitHub production environment, production backend, and encrypted Worker settings. Values must never be documented or printed. |

The mail subdomain is not a web hostname and must not be added to Cloudflare
Tunnel ingress. Its catch-all Email Routing action targets the Email Worker.
The existing Tunnel terminal `http_status:404` rule remains unchanged.

Deployment, verification, rotation, and troubleshooting are in the [inbound
matter email runbook](inbound_email_setup.md).

## Secrets

Use these names only when the repository and workflow require the capability:

| Secret | Allowed use |
| --- | --- |
| `CLOUDFLARE_READ_API_TOKEN` | Read-only inventory, analytics, and health checks. |
| `CLOUDFLARE_DNS_API_TOKEN` | DNS mutation with a token restricted to the required zone and permissions. |
| `CLOUDFLARE_R2_ACCESS_KEY_ID` | R2 access for an approved storage consumer. |
| `CLOUDFLARE_R2_SECRET_ACCESS_KEY` | R2 access for the same approved storage consumer. |
| `INBOUND_EMAIL_WEBHOOK_SECRET` | LawHand production environment only; authenticates Email Worker delivery to the backend. The same value is provisioned as an encrypted Worker secret. |

Never store credential values in an Actions variable, repository file, skill, memory file, issue, pull request, workflow input, or command argument. Supply secrets to `gh secret set` over standard input and verify only the resulting secret name and scope.

Do not synchronize R2 credentials across the web allowlist. Scope them to the exact repository and environment that owns the storage integration.

## MCP and Tunnel invariants

- `mcp.getlawhand.com` is the canonical platform/workspace MCP hostname.
- `research.getlawhand.com` exposes only the separately authenticated Research
  MCP; it is never a portal alias or a route to the raw research sidecar.
- MCP discovery and execution require authentication.
- The Tunnel ingress must end in `http_status:404`.
- `intake.getlawhand.com` uses Email Routing MX records and is never a Tunnel
  ingress hostname.
- Unknown and sensitive paths fail closed.
- DNS is published only after the application route, hostname isolation, authentication, and TLS behavior are verified.

During the IONOS host migration, create a separately credentialed Tunnel and
stage its canonical ingress before changing any record. Keep the existing
`LAWHAND_CLOUDFLARE_TUNNEL_*` repository variables pointed at the live Skynet
Tunnel until public DNS has moved and the exact IONOS revision passes
acceptance. Then update the three Tunnel variables together and retain the old
target in the private release record for the bounded rollback window. Never
route `research.getlawhand.com` directly to the raw Skynet sidecar; it remains a
public IONOS gateway backed by a private authenticated research connection.

See `docs/mcp_hostname_operations.md` and `docs/mcp_security_operations.md` for the production procedures.

## Change record

On 2026-08-22, the generic variables were synchronized to the five repositories above, and the LawHand-only variables were added to `mattpainter701/legalapp`. No Cloudflare token or R2 credential was committed to Git or stored in an Actions variable.

On 2026-08-24, `intake.getlawhand.com` was onboarded to Email Routing, its
catch-all was attached to `lawhand-inbound-email`, and the encrypted delivery
secret name was added to the `legalapp` production environment. The existing
Cloudflare Tunnel ingress was not changed.

On 2026-08-26, the repository documented the staged IONOS Tunnel cutover model.
No DNS record, live Tunnel identifier, or credential was changed by that
documentation update.

On 2026-08-27, the separately authenticated Research MCP was activated at
`research.getlawhand.com`. Hosted clients use OAuth dynamic registration and
PKCE; header-capable clients may use separately issued Research API tokens.

On 2026-08-28, IONOS remained the production Tunnel for the apex, `www`,
Workspace MCP, and Research MCP hostnames. Skynet was assigned two independent
roles: the private Research sidecar and an isolated development/DR host. The
optional `dev1.getlawhand.com` record targets the separately credentialed
Skynet Tunnel only after its app, private origin TLS, and Cloudflare Access
gate pass acceptance. It never changes or aliases the production MCP records;
