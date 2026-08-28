# MCP hostname operations

## Canonical boundary

| Host | Product and identity | Allowed public paths | Release state |
|---|---|---|---|
| `mcp.getlawhand.com` | Workspace/platform MCP; individual LawHand OAuth grant | `/` internally routes to `/api/mcp/workspace`; that transport and its two OAuth protected-resource metadata paths | Tenant-gated pilot |
| `research.getlawhand.com` | Legal-research/RAG MCP; individual OAuth grant or tenant Research API token | `/` internally routes to `/api/mcp`; that transport, its public OAuth protocol paths, `/api/mcp/manifest`, and `/api/mcp/tools/call` | Production |
| `getlawhand.com` | Main portal and Workspace OAuth authorization server; authenticated Research consent surface | Existing portal/API, Research consent and grant-management APIs, plus bounded legacy MCP aliases | Production |

The workspace OAuth protected resource and authorization server intentionally
have different origins. The resource is
`https://mcp.getlawhand.com/api/mcp/workspace`; its issuer and interactive
authorization endpoints remain on `https://getlawhand.com`.

Research uses its dedicated origin for both resource and issuer. Hosted
ChatGPT and Claude clients discover OAuth 2.1 at
`https://research.getlawhand.com/.well-known/oauth-authorization-server` and
use dynamic client registration plus PKCE. API clients may instead send a
LawHand Research token as `Authorization: Bearer lhrk_...`;
`X-MCP-API-Key` remains supported for existing clients.

The Research authorization endpoint starts on the dedicated issuer and then
redirects the user to the signed-in portal for consent. The portal page reads
and decides the pending request through
`/api/research-mcp/oauth/requests/{request_id}` on `getlawhand.com`; Research
grant listing and revocation are portal-only as well. Those authenticated APIs
must return 404 on the dedicated Research host. Conversely, public Research
OAuth discovery, registration, authorization-start, token, revocation, and
JWKS routes must return 404 on the portal origin. This split preserves the
declared OAuth issuer while allowing the existing LawHand session to authorize
the connection.

Neither MCP hostname is a second portal origin. Official documentation and
generated configuration use the full transport URLs:

- Workspace: `https://mcp.getlawhand.com/api/mcp/workspace`
- Research: `https://research.getlawhand.com/api/mcp`

### Workspace scope monitoring

The production acceptance check validates the complete published Workspace
scope set: `communications:propose`, `contacts:read`, `documents:propose`,
`documents:read`, `intakes:read`, `matters:read`, `offline_access`, `tasks:propose`,
`tasks:read`, and `templates:read`. When a Workspace MCP feature adds or
removes a scope, update the protected-resource metadata, this checklist, and
the production check in the same release. A scope drift is an operator signal,
not an OAuth failure by itself.

The bare origins remain supported shorthand aliases. Nginx internally routes
them to the corresponding transport without a client-visible redirect,
preserving POST bodies and avoiding client-dependent redirect behavior. Nginx
returns 404 for every other path outside the corresponding allowlist. The raw
CourtListener sidecar stays private and is never a public Cloudflare origin.

### IONOS core and Skynet research placement

For the first-customer IONOS cutover, all three public product hostnames remain
on one IONOS core Tunnel and the existing nginx hostname/path allowlists. The
CourtListener/vector database, source corpus, embedding workers, and raw MCP
sidecar remain on Skynet. The IONOS backend reaches that sidecar only through a
Tailscale-restricted private address and the separate
`MCP_UPSTREAM_API_KEY`; customer keys, OAuth tokens, and application JWTs are
never forwarded upstream.

This placement keeps the IONOS tenant database as the identity, entitlement,
billing, quota, and audit source of truth for both public MCP gateways. Pointing
`research.getlawhand.com` directly at an independent Skynet application copy
would split that source of truth and is forbidden. A later dedicated research
gateway may replace the private sidecar path only after it implements the same
central authorization and billing contract.

The raw sidecar's loopback listener may be published to the tailnet, but never
to public DNS, a public VM port, or a Cloudflare hostname. The complete host and
rollback sequence is in [IONOS Cube M production cutover](IONOS_CUTOVER_RUNBOOK.md).

## Search-engine exposure

Neither MCP hostname publishes a human-readable page, and neither may appear in
search results. Nginx chooses `X-Robots-Tag` per host: the path-keyed
`$x_robots_tag` map treats `/` as indexable because the marketing home page
lives there, so the host-keyed `$robots_tag` map overrides both dedicated
hostnames with `noindex, nofollow, noarchive` and falls back to the path-keyed
value everywhere else. `robots.txt` is intentionally outside each host's
allowlist and returns 404; the header, not a robots file, is what keeps these
origins out of an index.

A person who opens `research.getlawhand.com` in a browser reaches the protocol
endpoint and receives its unauthenticated JSON reply. That body carries a
`documentation` pointer to the public `/product/mcp` page, which is the
supported way to give that reader somewhere to go. Do not add an HTML landing
page, a redirect, or any additional allowed path to a dedicated MCP hostname:
the marketing site is the only place public product content belongs.

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
  service: https://127.0.0.1:443
  originRequest:
    originServerName: origin.getlawhand.internal
    caPool: /etc/cloudflared/lawhand-origin-ca.pem
    http2Origin: true
- hostname: research.getlawhand.com
  service: https://127.0.0.1:443
  originRequest:
    originServerName: origin.getlawhand.internal
    caPool: /etc/cloudflared/lawhand-origin-ca.pem
    http2Origin: true
- service: http_status:404
```

The apex and `www` rules use the same HTTPS origin settings. Provision the
private CA and matching nginx certificate on the production VM with
`scripts/provision_private_origin_tls.sh`, then validate the complete chain
with `scripts/validate_private_origin_tls.sh`. This private CA is only for the
Cloudflare Tunnel-to-VM hop. Installed file-share agents should continue to
connect to `https://getlawhand.com` and use normal operating-system public CA
validation; do not distribute this CA to customer machines.

Never set `noTLSVerify: true`, use a plain `http://` Tunnel service, or enable
Cloudflare Flexible mode. Keep `originServerName` aligned with the certificate
SAN and `caPool` pinned to the VM's private CA.
Hypervisor nginx publishes 127.0.0.1:80/443 only, so cloudflared must run on
the VM host. Stage and validate the certificate before changing ingress to
HTTPS; never run the legacy Let's Encrypt initializer/renewal cron afterward
because it can replace the pinned leaf.

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

# Research MCP is the public hosted-client endpoint. It must return an OAuth
# Bearer challenge, not a product-key prompt or a successful anonymous call.
curl -i https://research.getlawhand.com/api/mcp
curl -i https://research.getlawhand.com/api/mcp/manifest
curl -i https://research.getlawhand.com/.well-known/oauth-protected-resource/api/mcp
curl -i https://research.getlawhand.com/.well-known/oauth-authorization-server
curl -i https://research.getlawhand.com/api/research-mcp/oauth/jwks

# The apex is not a second Research MCP origin.
curl -i https://getlawhand.com/api/mcp
curl -i https://getlawhand.com/api/mcp/manifest
# The authenticated consent API belongs to the portal. Without a LawHand
# session it returns 401 there and remains unavailable on the Research host.
curl -i https://getlawhand.com/api/research-mcp/oauth/requests/diagnostic-request-id
curl -i https://research.getlawhand.com/api/research-mcp/oauth/requests/diagnostic-request-id
# Neither dedicated hostname exposes an ordinary portal/API route.
curl -i https://mcp.getlawhand.com/api/version
curl -i https://research.getlawhand.com/api/version

# Neither dedicated hostname is indexable.
curl -sI https://mcp.getlawhand.com/ | grep -i x-robots-tag
curl -sI https://research.getlawhand.com/api/mcp/manifest | grep -i x-robots-tag

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
  are available only on `https://research.getlawhand.com`; the transport and
  manifest return a `401` Bearer challenge without a token, while the metadata
  documents and JWKS return `200`;
- Research authorization metadata advertises the registration endpoint and
  `S256` PKCE; configure ChatGPT or Claude with
  `https://research.getlawhand.com/api/mcp`;
- the apex Research transport and manifest return `404` so clients cannot mix
  the OAuth issuer and resource origins;
- unauthenticated Research consent requests return `401` on the apex, proving
  the portal route is reachable and session-protected, while the same consent
  path returns `404` on the Research host;
- unrelated paths on both dedicated hosts return 404;
- every response from both dedicated hosts carries
  `X-Robots-Tag: noindex, nofollow, noarchive`;
- all three public origins present HSTS and certificates with at least the
  configured minimum remaining lifetime.

## Production activation

Set only these reviewed values in the protected production environment:

- `MCP_PRODUCT_ENABLED=true`;
- `RESEARCH_MCP_PUBLIC_URL=https://research.getlawhand.com/api/mcp` and
  `RESEARCH_MCP_ISSUER=https://research.getlawhand.com`;
- `RESEARCH_MCP_OAUTH_ENABLED=true`,
  `RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED=true`, and the approved audience;
- the existing shared RSA signing keyring (private key, public key, key ID, and
  previous public-key list). Never place those keys in source control.

Use a nonexistent matter query for an authenticated read-only smoke test.
Never create a proposal merely to test connectivity: proposal calls create
auditable tenant work.

## Rollback

If Research OAuth discovery, registration, JWKS, or the Bearer challenge fails,
set `MCP_PRODUCT_ENABLED=false` in the protected production environment and
run the normal production deployment workflow. The research transport and
OAuth routes then fail closed with `404`; do not redirect clients to the apex.

Preserve the existing apex and `www` tunnel entries, dedicated hostname routing,
and the final catch-all while investigating. Do not publish the private sidecar,
weaken tenant/product-key checks, or redirect one MCP product to the other.
