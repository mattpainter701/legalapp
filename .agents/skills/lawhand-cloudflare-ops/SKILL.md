---
name: lawhand-cloudflare-ops
description: Safely inspect, document, and update shared Cloudflare configuration for LawHand and related web projects. Use for Cloudflare DNS, Tunnel ingress, GitHub Actions variables and secrets, MCP hostnames, credential placement, or requests to reuse Cloudflare settings across repositories.
---

# LawHand Cloudflare Operations

Use `docs/cloudflare_shared_configuration.md` for the canonical variable names, repository scopes, and current sharing model.

## Sources of truth

- Read live Cloudflare state before relying on copied identifiers.
- Read nonsecret automation values from GitHub Actions configuration variables.
- Store credential values only in GitHub Actions secrets or the approved secret manager.
- Keep this skill as workflow guidance; never turn it into a credential store.
- Never put secret values in agent memory, issues, documentation, committed files, command arguments, or chat output.
- For every MCP-facing change, follow `docs/mcp/README.md`, update the canonical source in the same pull request, and leave a meaningful wiki handoff note.

## Safe workflow

1. Identify the exact Cloudflare account, zone, hostname, tunnel, GitHub repository, and environment in scope.
2. Inspect current Cloudflare and GitHub state without printing secret values. List secret names only.
3. Classify every value as nonsecret configuration or secret credential.
4. Check `docs/cloudflare_shared_configuration.md` for canonical names and detect stale hostname variants before writing.
5. In a personal GitHub namespace, fan out variables only to the documented repositories. True account-wide sharing requires an organization.
6. Store nonsecret identifiers in Actions variables. Store credentials in Actions secrets only for repositories that require the capability.
7. Apply DNS or Tunnel changes with the smallest possible scope. Preserve the Tunnel ingress `http_status:404` catch-all.
8. Verify DNS resolution, proxying, TLS, hostname isolation, and the intended application response after any change.
9. Record variable or secret names, scopes, timestamps, and verification evidence without recording credential values.

## Credential boundaries

- Use `CLOUDFLARE_READ_API_TOKEN` only for read-only inventory or health checks.
- Use a separate `CLOUDFLARE_DNS_API_TOKEN` for narrowly scoped DNS mutation.
- Do not share R2 access credentials globally. Limit them to the exact storage consumer and environment.
- Never infer permission to rotate, broaden, or distribute credentials from permission to publish nonsecret configuration.

## LawHand MCP invariants

- `mcp.getlawhand.com` is the canonical platform/workspace MCP hostname.
- `research.getlawhand.com` is reserved for a research-only MCP and remains disabled until explicitly deployed and secured.
- `research.mcp.getlawhand.com` is legacy, not canonical.
- Do not expose a research MCP route on the apex or workspace hostname.
- Require authentication before tool discovery or execution.
- Unknown or sensitive paths must fail closed.

## Stop conditions

Stop and request direction when the destination repository set, DNS hostname, credential scope, or production effect is materially ambiguous.
