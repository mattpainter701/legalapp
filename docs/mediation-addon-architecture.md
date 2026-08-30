# Mediation add-on architecture

## Product boundary

LawHand's matter workspace and **My Matters** client portal are native platform
capabilities. The licensed `mediation-legal` add-on supplements that same
matter; it does not create a second client record or replace the native portal.

Every new mediation is therefore created with, or linked to, a tenant-owned
`Matter`. The matter remains the system of record for the client relationship,
documents, tasks, dates, messages, invoices, and signatures. Mediation-specific
records supply the negotiation workflow: parties, disclosures, reviewed
documents, proposals, counters, sessions, and eventually settlement packets.

## Portal surfaces

| Surface | Identity | Purpose |
| --- | --- | --- |
| Staff mediation workspace | Firm user and tenant | Review submissions, approve immutable content, choose recipients, and record releases. |
| Native My Matters overlay | Matter-scoped client portal invite/account | Show the licensed mediation workflow inside the client's existing matter portal. |
| External-party mediation portal | Mediation party invitation | Give an opposing or other invited party only its own submissions and content deliberately released to that party. |

All three mediation surfaces require an active purchased, included, or
unexpired trial entitlement. Staff routes return an upgrade/disabled response;
the party portals fail closed without exposing whether a mediation exists. The
native overlay additionally requires exactly one mediation linked to the
current matter and exactly one `our_client` party matching the portal contact.
Other participant roles use the external-party portal. Missing, inactive,
ambiguous, cross-contact, cross-role, and cross-tenant states fail closed
without affecting the base My Matters portal.

## Confidentiality and release contract

Mediation negotiation is private by default.

- A portal upload is visible to its uploading party and firm staff. It is not
  visible to another party merely because both parties share a case.
- A submitted proposal is visible to its proposing party and firm staff while
  attorney review is pending.
- Attorney review records an explicit decision: `approved`,
  `changes_requested`, or `rejected`.
- Firm-side approval and release require the live `approve_legal_work` RBAC
  capability. A professional-title field is never treated as authorization;
  paralegals and other staff can prepare the record without receiving legal
  release authority.
- Approved content is delivered through recipient-specific release rows. A
  party sees only a release addressed to that party.
- A counterproposal may reference only a proposal from the same case that was
  released to the countering party. Drafting a counter does not supersede its
  parent; attorney-approved release does.
- Released documents and proposals are immutable. Corrections are new records
  with new hashes and lineage, not edits to released evidence.
- Approved or sent asset-schedule rows are likewise immutable.
- New uploads and proposal bodies receive SHA-256 digests. Downloads verify a
  stored digest before returning bytes, and recipient download timestamps are
  recorded.
- Portal responses do not disclose other recipients' identifiers or internal
  staff identifiers.

This is an application authorization boundary in addition to PostgreSQL tenant
row-level security. Every lookup remains scoped by tenant and case or matter.

## Current first slice

The first implementation slice establishes the safe extension seam:

1. recipient-scoped document and proposal releases;
2. attorney proposal review before release;
3. same-case, released-parent counteroffer lineage;
4. immutable released/approved records and content-integrity checks;
5. capability-gated attorney recipient-selection and review controls, while
   other staff retain preparation access;
6. private/pending/released states in the external-party portal; and
7. a read-only, entitlement-gated Mediation tab in native My Matters, including
   authenticated downloads.

The separate external-party portal remains supported for participants who are
not the firm's client. It is not embedded into My Matters and its party token
cannot be used against native client-portal endpoints.

## Next workflow slices

### Demand and issue ledger

The next slice should make the negotiation unit granular instead of treating a
whole proposal body as one undifferentiated offer. Introduce first-class
`MediationIssue` and immutable `MediationDemandVersion` records with:

- category, requested relief or allocation, amount/value, rationale, and
  supporting document links;
- proposing party, intended recipient parties, attorney review state, and
  release evidence;
- explicit accept, reject, counter, withdraw, and supersede transitions;
- issue-by-issue comparison plus a whole-case gap summary; and
- private attorney/paralegal notes stored separately from party-visible text.

Client UX should let a client draft demands one issue at a time, attach support,
and submit a coherent batch for firm review. Staff UX should support bulk
review without losing per-demand decisions. A recipient should be able to
respond to each released demand while still seeing how the current package fits
together.

### Certified settlement packet and e-sign

An approved set of demand versions should be assembled into a versioned packet
through the native matter-document workflow. The generated DOCX/PDF, source
record IDs, render hash, approver, and release audience form one immutable
packet version. Sending for signature must use the native `SignatureRequest`
and signer-evidence workflow. Export alone is not a signature, and a signed
certificate must remain linked to the exact packet hash.

### Add-on contribution contract

As more licensed modules are added, the platform should formalize a module
contribution contract rather than hard-code tabs indefinitely. A contribution
should declare its entitlement key, matter applicability, staff section,
portal section, counters, actions, and permission checks. The server remains
authoritative for entitlement and data scope; a frontend contribution can hide
or arrange a surface but cannot grant access.

## Release gates for later slices

- Postgres-backed tests prove tenant, matter, case, contact, party, and
  recipient isolation.
- Concurrent state transitions cannot create duplicate releases or mutate
  reviewed content.
- The base My Matters portal continues working when the add-on is absent,
  expired, disabled, or temporarily unavailable.
- A released packet can be traced from demand versions through generated bytes,
  attorney approval, recipients, downloads, and signature evidence.
- Customer-facing copy distinguishes drafts, attorney approval, release,
  receipt, and signature; none of those states are implied by another.
