# MCP hostname operations

## Canonical boundary

| Host | Product and identity | Allowed public paths | Release state |
|---|---|---|---|
| `mcp.getlawhand.com` | Workspace/platform MCP; individual LawHand OAuth grant | `/` internally routes to `/api/mcp/workspace`; that transport and its two OAuth protected-resource metadata paths | Tenant-gated pilot |
| `research.getlawhand.com` | Legal-research/RAG MCP; individual OAuth grant or tenant Research API token | `/` internally routes to `/api/mcp`; that transport, its OAuth discovery/authorization paths, `/api/mcp/manifest`, and `/api/mcp/tools/call` | Disabled; all Research paths expected 404 |
| `getlawhand.com` | Main portal and OAuth authorization server | Existing portal/API plus bounded legacy MCP aliases | Production |

The workspace OAuth protected resource and authorization server intentionally
have different origins. The resource is
`https://mcp.getlawhand.com/api/mcp/workspace`; its issuer and interactive
authorization endpoints remain on `https://getlawhand.com`.

Research uses its dedicated origin for both resource and issuer. Hosted
ChatGPT and Claude clients discover OAuth 2.1 at
`https://research.getlawhand.com/.well-known/oauth-authorization-server` and
use dynamic client registration plus PKCE. Header-capable clients may instead
send a LawHand Research API token as `X-MCP-API-Key: lhrk_...`.

Neither MCP hostname is a second portal origin. Official documentation and
generated configuration use the full transport URLs:

- Workspace: `https://mcp.getlawhand.com/api/mcp/workspace`
- Research: `https://research.getlawhand.com/api/mcp`

The bare origins remain supported shorthand aliases. Nginx internally routes
them to the corresponding transport without a client-visible redirect,
preserving POST bodies and avoiding client-dependent redirect behavior. Nginx
returns 404 for every other path outside the corresponding allowlist. The raw
CourtListener sidecar stays private and is never a public Cloudflare origin.

## Cloudflare topology

Create proxied CNAME records for both MCP hosts targeting the existing
LawHand Tunnel:

```text
mcp.getlawhand.com      CNAME  1d780272-f71d-4b23-9381-bbfa0ff94388.cfargotunnel.com
research.getlawhand.com CNAME  1d780272-f71d-4b23-9381-bbfa0ff94388.cfargotunnel.com
```

In the Cloudflare dashboard, use `CNAME`, names `mcp` and `research`, the
target above, `Proxied` status, and `Auto` TTL. Do not add a wildcard record or
change the existing apex and `www` records.

Keep proxying enabled. In the existing tunnel configuration, place these
rules before the catch-all rule and preserve all existing entries:

```yaml
- hostname: mcp.getlawhand.com
  service: http://localhost:80
- hostname: research.getlawhand.com
  service: http://localhost:80
- service: http_status:404
```

Cloudflare documents the effect of proxied records in
[Proxy status](https://developers.cloudflare.com/dns/proxy-status/). Keep the
zone TLS mode at Full (strict) and maintain a valid origin certificate as
described in [Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/).
Defense-in-depth controls, Cloudflare rule guidance, traffic triage, and
incident response are documented in
[MCP security operations](mcp_security_operations.md).

## Safe rollout

1. Merge the application, nginx, configuration, test, and monitoring changes.
2. While both MCP names are absent from public DNS, stage their exact hostname
   ingress rules before the Tunnel catch-all and confirm the catch-all remains
   `http_status:404`.
3. Deploy the exact green revision through the protected production workflow,
   then verify the apex compatibility routes and nginx hostname/path isolation.
4. Create both proxied DNS records. DNS is deliberately last so a dedicated
   hostname cannot reach an older or unisolated origin configuration.
5. Run the checks below and record the deployed revision and results.

If a DNS record must be created before deployment, leave that hostname absent
from Tunnel ingress so it reaches only the final 404 catch-all. Never make DNS
and ingress live together until nginx host/path isolation is deployed.

## Acceptance checks

The scheduled production-health workflow is the authoritative recurring check.
For rollout, also verify:

```bash
# Canonical workspace endpoint requires OAuth and advertises the mcp host.
curl -i https://mcp.getlawhand.com/api/mcp/workspace
curl -sS https://mcp.getlawhand.com/.well-known/oauth-protected-resource/api/mcp/workspace

# Shorthand roots resolve to their canonical transports.
curl -i https://mcp.getlawhand.com/
curl -i https://research.getlawhand.com/

# The research product remains unavailable until explicitly released.
curl -i https://research.getlawhand.com/api/mcp
curl -i https://research.getlawhand.com/api/mcp/manifest
curl -i https://research.getlawhand.com/.well-known/oauth-protected-resource/api/mcp
curl -i https://research.getlawhand.com/.well-known/oauth-authorization-server

# Neither dedicated hostname exposes an ordinary portal/API route.
curl -i https://mcp.getlawhand.com/api/version
curl -i https://research.getlawhand.com/api/version

# Legacy workspace clients remain bounded and receive the canonical challenge.
curl -i https://getlawhand.com/api/mcp/workspace
```

Expected results:

- workspace transports return 401 without a token, and the Bearer challenge
  identifies the canonical `mcp.getlawhand.com` protected-resource metadata;
- the two shorthand roots return the same response as their product's
  canonical transport, without a `Location` redirect;
- workspace metadata reports the canonical resource and the apex authorization
  server;
- Research transport, manifest, OAuth discovery, registration, and token paths
  return 404 while `MCP_PRODUCT_ENABLED=false`;
- unrelated paths on both dedicated hosts return 404;
- all three public origins present HSTS and certificates with at least the
  configured minimum remaining lifetime.

Use a nonexistent matter query for an authenticated read-only smoke test.
Never create a proposal merely to test connectivity: proposal calls create
auditable tenant work.

## Rollback

If hostname routing or isolation fails, remove only the two MCP tunnel ingress
rules and proxied DNS records. Preserve the existing apex and `www` tunnel
entries and the final catch-all. The bounded apex MCP compatibility routes let
existing workspace clients continue while the dedicated hosts are repaired.

Do not enable the research product as a rollback action. Do not publish the
private sidecar, weaken tenant/product-key checks, or redirect one MCP product
to the other.
