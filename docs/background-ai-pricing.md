# Background AI pricing and activation

Background route activation checks a provider-qualified price for every primary,
balanced target, and fallback. A successful **Test provider** confirms credential
and model connectivity; it does not establish pricing or activate the route.
The `Missing: provider/model` activation message identifies entries absent from
the application's price card, rather than a failed provider credential.

The built-in price card now covers the native DeepSeek V4 Flash, Pro, and Flash
Vision endpoints, plus the explicitly verified free OpenCode Zen endpoints listed
in `backend/app/services/ai_price_card.py`. This includes the reported combination
of `deepseek/deepseek-v4-flash` and `opencode-zen/mimo-v2.5-free`.

Native DeepSeek rates were verified on September 7, 2026 against its
[published pricing](https://api-docs.deepseek.com/quick_start/pricing/). Admission
reserves peak rates: Flash/Flash Vision $0.44 input and $1.32 output per million
tokens; Pro $1.32 input and $3.96 output. Cache-hit rates are retained, but the
reservation assumes uncached input. LiteLLM spend reconciliation remains the
source for actual provider charges, discounts, and time-dependent adjustments.

Free Zen endpoints were verified against [OpenCode's pricing table](https://opencode.ai/docs/zen/#pricing).
These entries are explicit; a `-free` suffix, arbitrary catalog label, or missing
rate never implies zero cost. Provider pricing can change, so maintain the
versioned price card when the provider changes its terms.

The platform setting `ai_price_card_v1` can override named rates while retaining
other built-in entries. Rates use USD per million tokens. A zero-rate override
must identify a provider-qualified model, include both input and output as zero,
and explicitly set `free: true`. Partial zero rates, absent attestation, negative
rates, and nonfinite numbers are rejected. Use a new operator `version` when
changing the price card so reservations retain an auditable pricing version.

Free work still reserves one USD micro ($0.000001) as a bookkeeping hold and
counts against request limits. A confirmed zero-cost response releases that hold
and settles at zero. Mixed pools reserve the most expensive eligible target,
including fallbacks. Unknown models or outcomes retain the conservative hold
until provider-spend reconciliation resolves them. No request-limit or data-policy
guard is bypassed by free pricing.

After deployment, retry **Validate & Activate** for the background profile.
The existing confidential-data policy and synthetic gateway activation check
still apply. This code change does not activate a live route, alter the configured
background pool, or change Standard/Premium chat or template-specific profiles.
It introduces no MCP tool, endpoint, or entitlement.
