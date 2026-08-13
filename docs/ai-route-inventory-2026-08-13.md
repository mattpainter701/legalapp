# BK24 AI Route Inventory — 2026-08-13

## Evidence boundary

This inventory distinguishes facts proven on the production host from committed
configuration and unresolved live state. It contains no provider keys, database
URLs, prompts, responses, or customer content.

## Proven production state

- GitHub production verification run:
  `https://github.com/mattpainter701/legalapp/actions/runs/31748121309`
- Runner: `skynet-lawhand-prod`
- Deployed and available main commit:
  `3c8a31258543982a7c5c2823e439a31c7642aaa7`
- Public version endpoint reports the same commit.
- GitHub has no open pull requests and no merge newer than PR #95; `origin/main`
  and production therefore agree on the complete merged history as of this audit.
- Readiness reports `ok` for disk, database, Redis, scheduler, queue, and host
  disks.
- The deployment production-check implementation requires authenticated LiteLLM
  discovery of `clarity-standard` and `clarity-premium`. The read-only verify
  operation did not rerun or expose model details.

## Committed route graph at the deployed commit

| Alias | Model | Capacity class | Provider credential |
|---|---|---:|---|
| `clarity-standard` | `openrouter/google/gemma-4-31b-it:free` | Free | OpenRouter |
| `clarity-standard-zen-nemotron` | `openai/nemotron-3-ultra-free` | Free | OpenCode-compatible |
| `clarity-standard-deepseek-flash-free` | `openai/deepseek-v4-flash-free` | Free | OpenCode-compatible |
| `clarity-premium` | `openai/deepseek-v4-pro` | Paid/price not recorded | OpenCode Go-compatible |
| `clarity-premium-openrouter-gemma` | `openrouter/google/gemma-4-31b-it:free` | Free | OpenRouter |

`litellm_config.yaml` defines these as separate aliases. It does not contain a
LiteLLM `fallbacks` mapping connecting the auxiliary aliases to Standard or
Premium. Application requests default to `clarity-standard` or
`clarity-premium` unless the platform database selects versioned aliases.

## Unresolved live state

- The database-managed `llm_route_config_v2` revision and its placements.
- The platform Standard/Premium model overrides currently selected by the app.
- Non-secret presence/health of each provider credential.
- A real synthetic completion, latency, and failover result for each live target.
- The last-known-good database-managed route revision suitable for rollback.

The production verification workflow deliberately does not expose this data.
No authenticated browser session was available in the current environment, so
the operator console could not be inspected. These items remain open rather than
being inferred from the file-backed defaults.

## Paid candidate catalog observation

The public OpenRouter model catalog was queried on 2026-08-13. These are
qualification candidates, not approved production routes:

| Tier | Candidate | Context | Input / 1M | Output / 1M | Required API features |
|---|---|---:|---:|---:|---|
| Standard | `openai/gpt-5.6-luna` | 1.05M | $0.10 | $0.60 | tools, structured output |
| Standard comparison | `anthropic/claude-haiku-4.5` | 200K | $1.00 | $5.00 | tools, structured output |
| Premium | `openai/gpt-5.6-terra` | 1.05M | $1.00 | $6.00 | tools, structured output |
| Premium comparison | `anthropic/claude-sonnet-4.5` | 1M | $3.00 | $15.00 | tools, structured output |

Prices are observations, not constants. Re-run
`python scripts/qualify_ai_route_catalog.py` immediately before qualification.
The tool blocks free/missing prices, insufficient context, and missing required
API features while always leaving `activation_approved=false` until synthetic
completion, legal benchmark, privacy, and provider-redundancy review pass.

All four candidates currently share the OpenRouter control plane. That is model
diversity, not provider redundancy. A paid direct-provider fallback must be
qualified before `AIP-02` is complete.

## Next gates

1. Inspect the authenticated operator route panel and export the metadata-only
   database route revision.
2. Run the catalog qualifier and preserve its timestamped JSON output.
3. Run synthetic completion and BK24 contract/citation benchmarks on the four
   candidates.
4. Approve a paid Standard/Premium pair plus a paid direct-provider fallback.
5. Canary on `cybersafeadvisor.com`, verify spend/latency, then promote with a
   tested rollback revision.
