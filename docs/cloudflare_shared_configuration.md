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

## Secrets

Use these names only when the repository and workflow require the capability:

| Secret | Allowed use |
| --- | --- |
| `CLOUDFLARE_READ_API_TOKEN` | Read-only inventory, analytics, and health checks. |
| `CLOUDFLARE_DNS_API_TOKEN` | DNS mutation with a token restricted to the required zone and permissions. |
| `CLOUDFLARE_R2_ACCESS_KEY_ID` | R2 access for an approved storage consumer. |
| `CLOUDFLARE_R2_SECRET_ACCESS_KEY` | R2 access for the same approved storage consumer. |

Never store credential values in an Actions variable, repository file, skill, memory file, issue, pull request, workflow input, or command argument. Supply secrets to `gh secret set` over standard input and verify only the resulting secret name and scope.

Do not synchronize R2 credentials across the web allowlist. Scope them to the exact repository and environment that owns the storage integration.

## MCP and Tunnel invariants

- `mcp.getlawhand.com` is the canonical platform/workspace MCP hostname.
- `research.getlawhand.com` remains disabled until a separately authenticated research MCP is deliberately deployed.
- MCP discovery and execution require authentication.
- The Tunnel ingress must end in `http_status:404`.
- Unknown and sensitive paths fail closed.
- DNS is published only after the application route, hostname isolation, authentication, and TLS behavior are verified.

See `docs/mcp_hostname_operations.md` and `docs/mcp_security_operations.md` for the production procedures.

## Change record

On 2026-08-22, the generic variables were synchronized to the five repositories above, and the LawHand-only variables were added to `mattpainter701/legalapp`. No Cloudflare token or R2 credential was committed to Git or stored in an Actions variable.
