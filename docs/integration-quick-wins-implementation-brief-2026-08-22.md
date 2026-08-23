# Integration quick wins — implementation brief

**Date:** 2026-08-22
**Purpose:** turn the integration audit into sequenced, evidence-backed work without promising a provider connection that has not completed tenant consent, commercial review, and production proof.

## Decision

Prioritize the integrations below in this order:

1. DocuSign external e-signature provider;
2. Calendly booking-to-intake connector;
3. signed tenant outbound webhooks (Zapier-compatible);
4. Twilio two-way SMS; and
5. LawPay payments, subject to partner/commercial validation.

Keep QuickBooks Online, Stripe, Microsoft 365, Google Workspace, Zoom Phone, Teams, and the Office add-ins focused on activation and reliability rather than adding overlapping providers. Defer a direct HubSpot connector until an outbound-webhook pilot proves the required fields and event set.

## Existing LawHand leverage

| Workstream | Existing reusable surface | Material gap |
| --- | --- | --- |
| External e-sign | `SignatureRequest`, `SignatureSigner`, client portal signing, `ESignProvider`, source/artifact hashes | `app/services/esign/dropbox_sign.py` intentionally raises `NotImplementedError`; no provider credentials, envelope dispatch, or webhook reconciliation |
| Booking to intake | lead/intake models, `ScheduledEvent`, Microsoft/Google calendar services, marketing demo funnel | no public booking webhook endpoint or external-event idempotency mapping |
| Automation | durable jobs, Zoom Phone signature-verified webhooks, token vault, Slack notification helper | no tenant-configured outbound event subscriptions, signing, retries, or delivery log |
| SMS | `CommunicationLog`, `Task` routing, task history recognizes `sms` | no SMS provider client, inbound endpoint, delivery-status mapping, or consent controls |
| Legal payments | Stripe billing and invoice reconciliation paths, billing models, encrypted credential conventions | no LawPay credential/OAuth model, payment-link flow, or explicit trust-versus-operating routing |

## 1. DocuSign — first external provider integration

### Why this is the first build

LawHand already has the hardest product-side components: a matter document, ordered signers, portal access, status state machine, evidence hashes, and a provider interface. Add a real `docusign` provider; do not rename the current Dropbox Sign stub or make a provider switch silently select the wrong API.

The official `docusign-esign-python-client` supports Python 3.9+ and exposes the eSignature v2.1 API. Its maintained examples cover authorization-code and JWT workflows. [SDK repository](https://github.com/docusign/docusign-esign-python-client) and [official examples](https://github.com/docusign/code-examples-python).

DocuSign supports an envelope-specific `eventNotification` during envelope creation. That is preferable to an account-wide Connect configuration for the first release because LawHand only needs notifications for envelopes it sent. The provider recommends webhooks rather than frequent polling, although the handler must remain idempotent because intermediate status changes can be coalesced. [DocuSign Connect guidance](https://www.docusign.com/blog/developers/dsdev-common-api-tasks-add-a-connect-webhook-to-your-envelopes).

### MVP contract

- Add a tenant-owned DocuSign configuration: integration key, OAuth client configuration, account id, environment, encrypted refresh token, and webhook-verification material. Reuse `TenantCredential` and the token vault where possible.
- On `send`, upload immutable source bytes already checked by `_source_document_is_unchanged`, create an envelope with ordered recipients, and record the returned envelope id.
- Register a per-envelope webhook for `sent`, `delivered`, `completed`, `declined`, and `voided`. Persist an event id or deterministic fingerprint before changing request state.
- On `completed`, fetch the authoritative completed PDF and certificate through `MatterFileStore`, calculate hashes, then mark the request complete. Never treat a client redirect as signature completion.
- Add an hourly bounded reconciliation for non-terminal envelopes as recovery, not the primary state delivery path.

### Acceptance evidence

Use a DocuSign demo account with a tenant-owned sender. Prove OAuth refresh; an ordered multi-signer envelope; duplicate webhook delivery; decline/void; completed artifact and certificate hashes; sender disconnect; and recovery after a simulated webhook outage. Confirm commercial/API entitlement before offering it in a plan.

## 2. Calendly — quickest path to public consult booking

Calendly API v2 supports OAuth for public integrations, and its webhook API can notify a system when invitees are created, cancelled, or rescheduled. A reschedule emits both cancellation and creation events, so LawHand must map provider event/invitee identifiers rather than create a second lead. [Calendly API overview](https://developer.calendly.com/getting-started) and [webhook behavior](https://developer.calendly.com/trigger-automations-with-other-apps-when-invitees-schedule-or-cancel-events).

### MVP contract

- Add a tenant Calendly OAuth connection (or a narrowly scoped pilot token only for an internal firm integration); do not put a long-lived token in the marketing frontend.
- Create `external_booking_events` with tenant id, provider, event URI, invitee URI, external event id, lead id, scheduled-event id, status, payload hash, and timestamps. Give it a unique provider identity constraint.
- Add a public HTTPS webhook endpoint that verifies the provider's subscription secret, queues processing, and returns promptly.
- `invitee.created`: upsert lead/contact, create or update the scheduled consult, link consented intake fields, and route to the configured owner.
- `invitee.canceled`: cancel only the matching LawHand scheduled event; do not delete the lead or prior communications.
- Do not synchronize broad calendar data in this connector. Existing Microsoft/Google services remain the source for staff calendar connectivity.

## 3. Outbound events — Zapier-compatible before a marketplace app

Zapier's webhook trigger accepts ordinary HTTPS callbacks, and its official platform separates triggers, searches, and creates for a later marketplace integration. [Webhook trigger documentation](https://help.zapier.com/hc/en-us/articles/8496288690317-Trigger-Zaps-from-webhooks) and [Zapier Platform CLI](https://docs.zapier.com/integrations/quickstart/cli-tutorial).

### MVP contract

- Add `integration_webhook_subscriptions` and `integration_webhook_deliveries`. Encrypt endpoint secrets, never return them after creation, and retain only a redacted delivery excerpt.
- Start with `lead.created`, `consult.booked`, `matter.created`, `signature.completed`, `invoice.paid`, and `task.overdue`.
- Emit a versioned envelope (`event_id`, `event_type`, `occurred_at`, `tenant_id`, `data`) signed with HMAC-SHA-256. Include delivery id/idempotency key; never include document bytes, chat content, or raw phone recordings/transcripts.
- Send from the durable worker with exponential retry, a short timeout, dead-letter status, replay control, and admin-visible delivery history.
- Offer Zapier recipes using **Webhooks by Zapier** first. Build a private Zapier app only after customer adoption justifies authentication, triggers, searches, and creates.

## 4. Twilio SMS — shared matter thread, not a contact center

Twilio can POST inbound messages to a configured webhook and POST outbound delivery state to a status callback. The official Python SDK supports Python 3.13 and async requests. [Messaging webhooks](https://www.twilio.com/docs/usage/webhooks/messaging-webhooks) and [Python SDK](https://github.com/twilio/twilio-python).

### MVP contract

- Use tenant-owned Messaging Services or a carefully partitioned platform service; store credentials encrypted and bind every inbound number to one tenant.
- Add provider-message id and direction/status fields to `CommunicationLog` or a linked SMS-message table. Enforce provider-message-id uniqueness.
- Validate Twilio's request signature against the exact externally visible URL; enqueue inbound work before responding with empty TwiML.
- Match inbound messages to a contact/matter only on exact normalized-number match. Ambiguous matches go to an intake review queue.
- Provide explicit consent, opt-out, quiet-hour, staff-permission, and retention controls before enabling sending. First use cases: consult, signature, and invoice reminders plus replies in the matter timeline.

## 5. LawPay — valuable, but commercially gated

8am/LawPay publishes a TLS REST payment platform with test/live credentials and OAuth-based contact/payment-method APIs, and its merchant settings support webhooks. [8am API reference](https://developers.8am.com/reference/api.html) and [LawPay integration settings](https://help.lawpay.com/en/articles/10671852-integrations-settings-overview).

### Gate before engineering

Confirm partner status, API access for intended merchant accounts, sandbox availability, supported payment-link/hosted-field flow, webhook signature scheme, and terms for platform storage of customer identifiers. Do not promise this integration from public documentation alone.

### MVP contract after the gate

- Treat LawPay as a client-invoice provider, not a replacement for Stripe subscription billing.
- Configure separate operating and trust destinations per tenant, requiring a finance-admin selection on every payment request. Never infer a trust destination from a matter name or client field.
- Use hosted/tokenized payment methods; never receive card or bank-account numbers in LawHand.
- Reconcile signed provider webhook events idempotently to an invoice/payment; ambiguous events become finance-review records.
- Add a payment-provider ledger/audit report before offering trust workflows.

## Why HubSpot is deferred

HubSpot has a viable OAuth and webhook model, but subscriptions are configured at app level and affect every installed customer. Its webhook receiver must be publicly reachable and validate `X-HubSpot-Signature` using the raw body. [HubSpot webhook guide](https://developers.hubspot.com/docs/api-reference/latest/webhooks/guide).

That is worthwhile once contact/deal mappings are known. Until then, outbound events provide a lower-risk pilot and avoid copying confidential matter data into a CRM by default.

## Shared engineering requirements

Every provider build must use the production discipline visible in Zoom Phone:

- tenant-scoped encrypted credentials and explicit ownership;
- strict OAuth state/PKCE and scoped consent;
- public HTTPS callback validation and provider-signature verification;
- durable, idempotent event processing; retry/reconciliation; and audit logs;
- disconnect/revocation and data-retention behavior; and
- documented live proof before a customer is told the integration is enabled.

## Estimated sequence

| Item | Engineering estimate | External dependency |
| --- | ---: | --- |
| DocuSign provider and webhook lifecycle | 2–3 weeks | API plan, sandbox, sender identity |
| Calendly booking-to-intake | 1–2 weeks | OAuth app and paid-plan webhook access |
| Signed outbound webhooks | 1–2 weeks | none; pilot endpoint(s) |
| Twilio SMS MVP | 2 weeks | tenant messaging ownership and compliance design |
| LawPay | 2–4 weeks after discovery | partner/API/sandbox approval |
