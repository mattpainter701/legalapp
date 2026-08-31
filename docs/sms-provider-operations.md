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
the selected rows must be exact candidates captured on that review item, so an
otherwise accessible same-phone contact or matter cannot be substituted. Both
decisions are audited without copying message content into operator metadata.

## Provider configuration

Keep the provider inactive while credentials, sender registration, ownership,
consent policy, or quiet-hours policy are incomplete. Repeating the same
idempotency key can only read the original accepted outcome; failed, blocked,
or failure-after-acceptance rows never become a new provider call or a
successful response. Do not create a replacement key while an outcome is
uncertain, because the provider may have accepted the first request. A dispatch
lease that expires becomes explicit `provider_unknown` work; the scheduler
never resends it. An authorized operator may attest that a message was not
visible in the provider console, but that observation remains unresolved and
never unlocks a resend. Only an authenticated provider lookup can settle the
row. A lookup cannot manufacture acceptance: its configured account, SID,
exact sender, destination, body, messaging service, direction, creation-time
window, request digest, and returned status must match the reserved dispatch.
Any response carrying a provider SID represents acceptance or possible
delivery, including `failed` and `undelivered`; it is never classified as a
safe no-send. If another submission is appropriate after a settled failure,
create and review a new SMS proposal with a new key and explicit
duplicate-delivery awareness rather than re-approving the old attempt. The
Intake reconciliation queue shows unresolved dispatches and keeps attestations
and failed lookups unresolved for later review.

Webhook endpoints verify Twilio's request signature with the tenant's actual
Account Auth Token; there is no second application-defined webhook secret. They
deduplicate provider message IDs and ignore unknown, replayed, or out-of-order
status regressions. Credential rotation should be followed by a signed callback
test and a review of the provider configuration audit trail. Send admission and
rotation share one tenant/provider transaction fence until the dispatch
reservation is durable; bounded generation retirement therefore sees every
in-flight reservation before discarding credential bytes. Audit metadata
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
content. Creation and replay require the actor's current matter ownership or
assignment before recipient, source, consent, or idempotency details are read.
SMS proposal content stays in LawHand: it is not copied into assignment email or
third-party calendar events. Assignment revocation synchronously removes any
legacy calendar copy and fails closed if a connected provider cannot complete
that cleanup. No model action sends SMS autonomously, and provider outages or
uncertain outcomes remain visible for operator review.

## Retention, export, and legal hold

The tenant retention inventory reports count/age metadata for every dedicated
SMS store: message content and delivery/reconciliation state, current lead
consent state, current number suppression, immutable consent events, immutable
STOP/START number events, inbound review evidence, current provider
configuration, and bounded credential-generation records. It also reports the
SMS-bearing copies in shared `communication_logs`, `tasks`, `task_events`, and
`task_automation_runs` rows (including approved action snapshots).
The endpoint never emits message bodies, phone numbers, provider account or
sender identifiers, compliance snapshots, or encrypted authentication tokens.

Authorized customer export treats SMS messages, current consent state, and
current suppression state as customer records. Immutable consent/STOP events and
review evidence are exposed only as bounded evidence summaries. Provider
configuration and credential generations are classified as security metadata
only, with no secret or configured-provider values. Timeline/task copies use
their existing authorized customer export path; task-event and automation-run
copies expose bounded evidence summaries rather than action content. Message reconciliation fields travel with the
authorized message record; audit and receipt surfaces must continue to use
sanitized evidence rather than content.

There is no automated SMS deletion path. The chat-attachment retention job does
not remove SMS rows, and normal demo cleanup is a separate purge boundary. When
a tenant legal hold is active, preserve SMS content, suppression truth, consent
and number events, review evidence, configuration metadata, related audit
records, all shared-table SMS copies, and applicable backups. Lifting the hold does not itself authorize SMS
deletion. Counsel and operations must approve a future disposition policy that
covers customer authority, provider copies, backups, and immutable compliance
evidence before any destructive workflow is introduced.

Generic communication creation cannot manufacture an SMS timeline row; only the
provider-backed lifecycle writes those records. SMS proposal tasks and their
event evidence cannot be hard-deleted, even before an automation run exists or
while a legal hold is active. Operators cancel review work instead, preserving
the proposal and decision trail. Expired disposable-demo purge remains a
separate authorized boundary and deletes message/review content plus current and
retired provider credentials in dependency-safe order without cloning them.
