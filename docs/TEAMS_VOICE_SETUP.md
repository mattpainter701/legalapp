# Microsoft Teams voice (Teams Phone) capture setup

This runbook provisions Teams Phone call capture for one LawHand customer. It is
the Teams counterpart of `ZOOM_PHONE_TENANT_APP_SETUP.md`: both land inbound
calls in the intake dashboard, and a firm may run either, both, or neither.

This is an operator and customer-admin procedure.

## What this integration does

LawHand uses an application-only Microsoft Graph credential, scoped to the
customer's Entra directory, to:

- subscribe to `communications/callRecords` change notifications so a completed
  call reaches intake within moments;
- read the authoritative call record for each notified call;
- sweep the Teams PSTN usage report hourly to heal anything the notification
  path dropped; and
- import **inbound** calls into Call Intake as communication logs.

It does not capture outbound or internal Teams calls. It does not read
recordings or meeting transcripts. It does not share one credential's call data
across tenants.

## Why this cannot reuse the Teams chat grant

Everything else in the Teams integration runs on the delegated Microsoft grant
an admin authorizes under Integrations. Microsoft exposes call records **only**
through the application permission `CallRecords.Read.All` — there is no
delegated equivalent — so voice capture runs a separate client-credentials
grant against the customer's directory GUID.

Two consequences follow, and both are load-bearing:

- The multi-tenant `common` / `organizations` endpoints cannot issue an
  application-only token. The customer's real directory GUID is required, and
  LawHand rejects the multi-tenant aliases rather than storing a configuration
  that can never authenticate.
- Enabling voice does not widen the chat grant, and revoking one does not
  revoke the other.

## Ownership and security model

| Item | Owner | Storage |
|---|---|---|
| Entra application | LawHand platform, or the customer's own single-tenant app | Entra ID |
| Application client ID and secret | Whoever owns the app | Platform app: environment. Customer app: encrypted in the tenant's `tenant_oauth_apps` row (provider `teams_voice`) |
| Entra directory (tenant) GUID | Customer Microsoft admin | `teams_voice_settings.entra_tenant_id` |
| Notification `clientState` secret | LawHand, generated per tenant | Encrypted in `teams_voice_settings` |
| Subscription id and expiry | Microsoft Graph | Mirrored in `teams_voice_settings` |
| Notification URL | LawHand tenant | Tenant-specific public HTTPS endpoint |

A firm that prefers to own the registration should create a **single-tenant**
Entra app holding only `CallRecords.Read.All` and supply its client ID and
secret. That is usually the easier security conversation: the app can do exactly
one thing.

## Procedure

1. **Confirm Teams chat is connected.** The Voice tab lives behind the same
   gate; voice is an extension of a working Teams connection.
2. **Record the directory GUID.** Entra admin center → Overview → Tenant ID.
   Save it on the Voice tab.
3. **Grant admin consent.** A Microsoft 365 global administrator opens the
   consent link the panel builds and approves `CallRecords.Read.All`.
4. **Enable capture, then start live notifications.** Graph validates the
   notification URL synchronously by POSTing a `validationToken` to it, so this
   step only succeeds against a publicly reachable deployment.
5. **Test.** The connection test reads the last 24 hours of PSTN usage. It
   proves the credential and the permission without waiting for a real call.

## Notification handling

Microsoft calls the notification endpoint unauthenticated. Two things
authenticate it instead:

- Every notification carries the tenant's `clientState`, compared in constant
  time. A mismatch is rejected with 401.
- The payload is treated as an **identifier only**. LawHand never trusts
  notification content for call data; it re-reads the call record from Graph
  with its own token, and refuses a record whose id differs from the one
  requested.

A notification for a disabled tenant is accepted and dropped rather than
refused. Replying non-2xx would make Graph retry and eventually tear down the
subscription for a firm that switched the feature off deliberately.

## Idempotency

`teams_voice:call:<callRecordId>` is the idempotency key, enforced by a partial
unique index on `communication_logs`. The notification path and the hourly sweep
both write through it, so a call seen twice is stored once.

Once intake staff have worked a captured call — corrected the caller, linked a
contact, opened a task — reconciliation refreshes only provider-owned metadata
(duration, result, the raw payload). The curated narrative and caller identity
belong to the humans who worked the call and are never overwritten.

## Subscription lifecycle

Graph caps a `callRecords` subscription at roughly 4230 minutes (about three
days). LawHand checks every six hours and renews anything within 12 hours of
expiry, so several missed ticks during a deploy are survivable.

If renewal fails, the error is recorded on the tenant's settings row and shown
on the Voice tab. Capture continues through the hourly PSTN sweep — slower, but
uninterrupted. Do not treat a failed renewal as an outage of capture itself.

Changing the directory GUID clears the stored subscription id: the old
subscription lived in the old directory, and renewing against it would target a
subscription the firm no longer owns.

## Verification checklist

- Directory GUID matches the customer's Entra Overview page.
- Consent shows `CallRecords.Read.All` granted to the expected application.
- Connection test returns without error.
- The notification URL is the tenant-specific one, over HTTPS.
- A real inbound call appears in the intake feed badged **Teams**.
- Subscription expiry is in the future and moves forward after a renewal cycle.
