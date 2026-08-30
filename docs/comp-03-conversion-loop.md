# COMP-03 conversion loop

The conversion loop is a bounded path from an attributed public inquiry to a
reviewed matter. Firms create versioned intake forms with conditional fields
and availability slots. Public submissions are IP-rate-limited, honeypot
protected, idempotent, tenant-resolved, and recorded with allowlisted source
attribution. They create a prospect contact, lead, consent record, and funnel
event.

Staff triage is explicit. A public lead cannot be converted until an attorney
records a clear conflict decision. Appointment booking accepts only a slot
published by the form and writes a local calendar event plus a durable pending
reminder state; no external provider success is inferred. Email follow-up
requires current channel consent and records the actual provider result.
SMS returns an explicit unavailable response until ECO-23–29 provider,
webhook, opt-out, and reconciliation gates are complete. Existing BK26 fee
agreement packet review/e-signature and lead conversion remain the canonical
agreement/matter path; the new funnel ledger records those downstream events
without creating a parallel agreement implementation.

The public API intentionally excludes mass marketing, autonomous engagement,
and a website builder. Form answers and attribution are tenant-scoped with
RLS; IP addresses are stored only as a one-way abuse-control hash.
