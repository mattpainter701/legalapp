# Provider-backed SMS operations

SMS is disabled unless a tenant administrator configures an approved provider,
an encrypted credential pair, a sender, and the compliance snapshot required by
the configuration API. The system never reports delivery from the outbound
request alone: provider acceptance is recorded as `submitted`, and delivery is
updated only by a valid signed status callback.

## Consent and routing

Public intake records the disclosure version, language, source, timestamp,
timezone, quiet-hours window, verified mobile, and allowed message categories.
Outbound SMS requires active consent, a verified E.164 mobile matching the
contact, a non-revoked consent row, and an explicit grant for the message
category. STOP-family replies revoke SMS consent; START/UNSTOP can restore it
only when the verified mobile, disclosure, category grants, and consent expiry
remain valid. LawHand records HELP and opt-out keywords; the provider messaging
service remains responsible for its configured regulatory keyword responses.

Inbound messages are matched inside the tenant. Missing, duplicate, or
ambiguous contact/matter matches become review items and are not attached to a
matter automatically. Staff should resolve those items before treating the
message as matter correspondence.

## Provider configuration

Keep the provider inactive while credentials, sender registration, ownership,
consent policy, or quiet-hours policy are incomplete. Use the same idempotency
key for a retry after an uncertain provider response. Do not retry with a new
key until the original message has been reconciled, because a provider may have
accepted the first request.

Webhook endpoints require the provider signature and tenant-specific secret.
They deduplicate provider message IDs and ignore out-of-order status regressions.
Credential rotation should be followed by a signed callback test and a review of
the provider configuration audit trail. Audit metadata records tenant, actor,
provider, readiness, activation, and ownership model, but never credentials.

## Assistant behavior

`propose_client_sms` creates reviewable work only. It binds the proposal to one
verified, consented matter party and re-checks consent and party membership at
approval time. No model action sends SMS autonomously, and provider outages or
uncertain outcomes remain visible for operator review.
