# MCP security operations

This runbook covers the two public LawHand MCP products. Hostname and rollout
operations remain in [MCP hostname operations](mcp_hostname_operations.md).

## Security boundary

Workspace MCP and research MCP are separate products and identities:

- `mcp.getlawhand.com/api/mcp/workspace` accepts only an individual,
  audience-bound LawHand OAuth token. Tenant, user, client, grant, scopes, and
  token revocation are revalidated before tool execution.
- `research.getlawhand.com/api/mcp` accepts only a scoped research product key.
  Protocol request limits are separate from the existing per-tool burst and
  monthly product quotas, so JSON-RPC batching does not bypass metering.
- Neither credential type is accepted by the other product. Matter artifacts,
  proposals, reviews, approvals, and tenant cloud-storage references continue
  through the same audited application services used by the portal and chat.

## Enforced controls

| Boundary | Control |
|---|---|
| Cloudflare Tunnel | Exact hostname ingress precedes a final `http_status:404` catch-all. |
| Nginx hostname | Each dedicated hostname exposes only its documented MCP/OAuth paths. |
| Nginx transport | Separate research/workspace IP buckets, 10 concurrent connections per IP, 15-second body timeout, and 256 KiB bodies. |
| HTTP method | Streamable HTTP accepts `GET`, `POST`, and `DELETE`; manifest is `GET` only; compatibility tool-call is `POST` only. Rejections are 405 with `Allow`. |
| Application body | The ASGI boundary independently counts declared and chunked body bytes before protocol parsing. |
| Workspace identity | 120 protocol requests per token per minute and 1,200 per tenant per minute by default. |
| Research identity | 240 protocol requests per product key per minute and 2,400 per tenant per minute by default, in addition to per-tool quotas. |
| Limiter availability | Production fails closed with 503 if Redis cannot enforce principal limits. |
| Hidden paths | Dot-prefixed paths such as `/.env`, `/.git/config`, `/.mcp.json`, and `/.cursor/mcp.json` return 404 instead of portal HTML. Exact OAuth discovery and ACME routes remain available. |
| Revocation durability | Redis runs with an explicit `maxmemory` below its container limit and `--maxmemory-policy noeviction`. See "Redis is a security-bearing store" below. |
| Untrusted document text | Extracted matter-document text is returned inside `<untrusted_document_text>` delimiters with counterfeit closing tags neutralized. See the [workspace adapter](workspace_mcp_adapter.md#untrusted-text-delimiting). |

Rate and body defaults are configuration, not authorization. Raising them must
not weaken tenant scoping, tool scopes, approval gates, audit records, or cloud
storage integrity checks.

## Redis is a security-bearing store

Redis holds ordinary cache alongside two controls whose loss is not merely a
performance event:

- workspace refresh-token replay tombstones and revoked-family records
- the revoked-JWT `jti` denylist

Every key written by this application carries a TTL. That makes **all** keys
"volatile", so `volatile-lru` is no safer here than `allkeys-lru` — either would
discard revocation state under memory pressure and allow a token that was
already burned to be replayed. An OOM kill has the same effect, without leaving
the store to report it.

Production therefore sets an explicit `maxmemory` below the container limit and
`--maxmemory-policy noeviction`, so exhaustion refuses writes loudly (new
sessions error) instead of silently weakening revocation.

Operational rule: monitor `used_memory` against `maxmemory`. If it trends toward
the ceiling, move the RAG cache to a separate instance rather than relaxing the
eviction policy on the instance holding revocation state.

## Cloudflare edge policy

Keep the Cloudflare API credential used for analytics read-only. Apply DNS,
Tunnel, WAF, and rate-rule changes through a reviewed administrator session or
infrastructure-as-code change.

The origin must remain private and reachable only through the reviewed host and
Tunnel boundary. Nginx currently trusts private Docker peer ranges when it
accepts forwarded client IP and scheme headers. Before any untrusted workload
can reach that private network, pin the production Docker subnet, narrow both
the `geo` and `set_real_ip_from` entries to the exact cloudflared/host gateway,
and rerun the proxy-spoofing runtime gate.

Recommended edge policy:

1. Allow only the exact hostname/path combinations documented in the hostname
   runbook. The origin still enforces this allowlist if an edge rule is absent.
2. Block unexpected methods on exact MCP paths. Do not use a browser challenge
   for MCP transports or OAuth metadata: desktop and command-line clients
   cannot complete an interactive challenge reliably.
3. Introduce any Cloudflare per-IP rate rule in log/observe mode first. Keep its
   threshold at or above the origin budget, confirm legitimate desktop-client
   bursts, then use a bounded block action rather than Managed Challenge.
4. Keep OAuth protected-resource metadata and authorization-server metadata
   publicly readable. Authentication happens at the authorization and resource
   endpoints, not by hiding discovery documents.
5. Do not place Cloudflare Access in front of the standard remote-MCP OAuth
   flow unless every supported client has been validated with that additional
   authentication layer.

Cloudflare's current API guidance recommends method restrictions and endpoint
rate limiting; see [Discover and secure API endpoints](https://developers.cloudflare.com/use-cases/solutions/discover-secure-api-endpoints/)
and [WAF feature interoperability](https://developers.cloudflare.com/waf/feature-interoperability/).

## Probe and traffic triage

Cloudflare `visits` is not a human-only metric. MCP clients, scheduled checks,
SEO bots, and scanners can all create visits. For an MCP traffic alert, group by
host, path, method, status, user agent, and time before classifying it.

- Repeated 401 from a known desktop-client user agent usually means a configured
  connector has no current token; it is not proof of successful access.
- Scheduled `curl` requests to disabled research paths should be correlated
  with the production-health workflow before being treated as hostile.
- A 404 on hidden-file probes is expected. A 200 with `text/html` historically
  meant the SPA catch-all answered the scanner, not that the named file leaked;
  the Nginx hidden-path gate now removes that ambiguity.
- Investigate any 2xx workspace transport response without a correlated OAuth
  grant, audit event, and expected user/client identity.
- Never log raw bearer tokens, product keys, OAuth codes, or refresh tokens.

## Incident response

1. Disable the affected MCP product flag if identity or tenant isolation is in
   doubt. Do not disable the main portal as a first response.
2. Revoke the affected workspace grant/token or research product key and retain
   the related audit and request identifiers.
3. Remove only the affected dedicated Tunnel ingress rule if the hostname must
   be withdrawn; preserve the final 404 catch-all and unrelated portal routes.
4. Export Cloudflare and application evidence, identify the deployed commit,
   and verify tenant cloud-storage references and document-integrity events.
5. Restore service only after the protected deployment, public 401/404 checks,
   OAuth metadata checks, and an authenticated tenant-isolation smoke test pass.

## Desktop-client retry control

Keep a connector disabled while its DNS, product flag, or OAuth grant is not
ready. In Codex, retain the server block and set `enabled = false`; this stops
background retries without deleting the configuration. Re-enable it only after
the canonical hostname passes the acceptance checks. See the official
[Codex MCP configuration options](https://learn.chatgpt.com/docs/extend/mcp#other-configuration-options).
