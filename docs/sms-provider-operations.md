# Provider-backed SMS operations

SMS is disabled unless a tenant administrator configures the Twilio Account SID
and encrypted Account Auth Token, a sender, and the compliance snapshot required by
the configuration API. The system never reports delivery from the outbound
request alone: provider acceptance is recorded as `submitted`, and delivery is
updated only by a valid signed status callback or an authenticated provider
lookup that matches the configured account, message SID, destination, and
provider status exactly.

## Consent and routing

Public intake records the disclosure version, language, source, timestamp,
timezone, quiet-hours window, verified mobile, and allowed message categories.
Every grant, revoke, STOP, and START transition appends an immutable,
tenant-scoped evidence snapshot. The legacy contact `sms_opt_in` field is only
a synchronized compatibility indicator and never authorizes delivery; client
CRUD cannot turn it on without the canonical intake consent workflow.
Outbound SMS requires active consent, a verified E.164 mobile matching the
contact, a non-revoked consent row, and an explicit grant for the message
category. STOP-family replies revoke SMS consent; START/UNSTOP can restore it
only when the verified mobile, disclosure, category grants, and consent expiry
remain valid. LawHand records HELP and opt-out keywords; the provider messaging
service remains responsible for its configured regulatory keyword responses.

STOP-family suppression is persisted by tenant and normalized phone before
matter routing, even when no contact exists yet. It applies to every matching
contact/lead consent and serializes against the final outbound consent check.
Every STOP and START attempt appends immutable number-level evidence; an
unmatched, ambiguous, expired, or otherwise invalid START stays suppressed.
Inbound messages with missing, duplicate, or ambiguous contact/matter matches
become review items and are not attached to a matter timeline automatically.
Authorized staff must select an exact contact and matter or reject the item;
both decisions are audited without copying message content into operator
metadata.

## Provider configuration

Keep the provider inactive while credentials, sender registration, ownership,
consent policy, or quiet-hours policy are incomplete. Repeating the same
idempotency key can only read the original accepted outcome; failed, blocked,
or reconciled-not-sent rows never become a new provider call or a successful
response. Do not create a replacement key while an outcome is uncertain,
because the provider may have accepted the first request. A dispatch lease that
expires becomes explicit `provider_unknown` work; the scheduler never resends
it. An authorized operator must record either confirmed-not-sent or the
provider message ID for an authenticated lookup. A lookup cannot manufacture
acceptance: its configured account, SID, destination, and returned status must
match the reserved dispatch. After confirmed non-delivery, create and review a
new SMS proposal with a new key rather than re-approving the old attempt. The
Intake reconciliation queue shows unresolved dispatches and keeps failed
lookups unresolved for later review.

Webhook endpoints verify Twilio's request signature with the tenant's actual
Account Auth Token; there is no second application-defined webhook secret. They
deduplicate provider message IDs and ignore unknown, replayed, or out-of-order
status regressions. Credential rotation should be followed by a signed callback
test and a review of the provider configuration audit trail. Audit metadata
records tenant, actor, provider, readiness, activation, and ownership model,
but never credentials.

## Assistant behavior

`propose_client_sms` creates reviewable work only. It binds the proposal to one
verified, consented matter party and exact source-document hashes. Task Board
shows the destination, body, category, source chips, consent/quiet-hours checks,
and retry risk, then requires a dedicated acknowledgment before approval. The
server revalidates sources, consent, category, and party membership at approval
and immediately before dispatch. External or mutable citations are displayed as
unverified and block approval until exact local evidence exists. Workspace MCP
request identities are idempotent and cannot be reused for changed proposal
content. No model action sends SMS autonomously, and provider outages or
uncertain outcomes remain visible for operator review.
