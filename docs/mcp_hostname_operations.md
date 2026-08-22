# MCP hostname operations

## Canonical boundary

| Host | Product and identity | Allowed public paths | Release state |
|---|---|---|---|
| `mcp.getlawhand.com` | Workspace/platform MCP; individual LawHand OAuth grant | `/api/mcp/workspace` and its two OAuth protected-resource metadata paths | Tenant-gated pilot |
| `research.getlawhand.com` | Legal-research/RAG MCP; tenant product key | `/api/mcp`, `/api/mcp/manifest`, `/api/mcp/tools/call` | Disabled; expected 404 |
| `getlawhand.com` | Main portal and OAuth authorization server | Existing portal/API plus bounded legacy MCP aliases | Production |

The workspace OAuth protected resource and authorization server intentionally
have different origins. The resource is
`https://mcp.getlawhand.com/api/mcp/workspace`; its issuer and interactive
authorization endpoints remain on `https://getlawhand.com`.

Neither MCP hostname is a second portal origin. Nginx returns 404 for every
path outside the corresponding allowlist. The raw CourtListener sidecar stays
private and is never a public Cloudflare origin.

## Cloudflare topology

Create proxied CNAME records for both MCP hosts targeting the existing
LawHand Tunnel:

```text
mcp.getlawhand.com      CNAME  1d780272-f71d-4b23-9381-bbfa0ff94388.cfargotunnel.com
research.getlawhand.com CNAME  1d780272-f71d-4b23-9381-bbfa0ff94388.cfargotunnel.com
```

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

## Safe rollout

1. Merge the application, nginx, configuration, test, and monitoring changes.
2. Create both proxied DNS records while the tunnel still sends unknown hosts
   to its `http_status:404` catch-all.
3. Deploy the exact green revision through the protected production workflow.
4. Add both hostname ingress rules before the tunnel catch-all.
5. Run the checks below and record the deployed revision and results.

This order avoids exposing the portal on a new hostname before nginx host/path
isolation is active.

## Acceptance checks

The scheduled production-health workflow is the authoritative recurring check.
For rollout, also verify:

```bash
# Canonical workspace endpoint requires OAuth and advertises the mcp host.
curl -i https://mcp.getlawhand.com/api/mcp/workspace
curl -sS https://mcp.getlawhand.com/.well-known/oauth-protected-resource/api/mcp/workspace

# The research product remains unavailable until explicitly released.
curl -i https://research.getlawhand.com/api/mcp
curl -i https://research.getlawhand.com/api/mcp/manifest

# Neither dedicated hostname exposes an ordinary portal/API route.
curl -i https://mcp.getlawhand.com/api/version
curl -i https://research.getlawhand.com/api/version

# Legacy workspace clients remain bounded and receive the canonical challenge.
curl -i https://getlawhand.com/api/mcp/workspace
```

Expected results:

- workspace transports return 401 without a token, and the Bearer challenge
  identifies the canonical `mcp.getlawhand.com` protected-resource metadata;
- workspace metadata reports the canonical resource and the apex authorization
  server;
- research transport and manifest return 404 while
  `MCP_PRODUCT_ENABLED=false`;
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
