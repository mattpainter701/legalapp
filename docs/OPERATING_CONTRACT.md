# LawHand customer operating contract

Version `2026-08-29.1` is the canonical customer-trust contract. Its
machine-readable source is `GET /api/public/operating-contract`; the same
version drives the public objectives, support policy, subprocessor registry,
assurance roadmap, and downloadable security-review packet.

## What each claim state means

- **Implemented** means the current product contains the described bounded
  workflow and current tests exercise it.
- **Verified** means a release or production rehearsal produced time-bound
  evidence. It is not a guarantee of future performance.
- **Policy-committed** defines an operating practice but is not an SLA,
  certification, legal conclusion, or customer remedy.
- **Provider-dependent** requires the selected provider and customer
  configuration to be confirmed.
- **Planned** and **unavailable** are not capabilities or attainment claims.

Public responses contain opaque evidence identifiers, never repository paths,
credentials, customer records, internal hostnames, or exploitable topology
details. The downloadable packet is a first-party snapshot, not an audit
opinion.

## Supported topology and objectives

The supported production shape is a version-identified single hosted
application deployment with encrypted off-host backups, customer-authorized
cloud storage, and a controlled research gateway. Multi-region or active-active
availability is not promised.

The published objectives measure the ten-minute production health probe,
backup freshness, isolated restore readiness, and incident-update lifecycle.
They explicitly exclude uptime percentages, RPO/RTO warranties, service
credits, damages remedies, customer-controlled systems, and uncommitted
provider behavior. Current backup and restore procedures remain in
`docs/BACKUP_DISASTER_RECOVERY.md` and
`docs/FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md`.

## Support and incident communication

Standard support coverage is Monday through Friday, 08:00–17:00
America/Chicago. S1 security, destructive integrity, or production-wide
availability reports use the emergency channel named in the customer order
form outside those hours. The public policy defines S1–S4 impact, initial
owner, acknowledgement objective, and missed-objective escalation. Unless
signed customer terms incorporate them, these targets are operating objectives
and not an SLA.

Tenant administrators create tenant-scoped support requests. Operators move
them through `open`, `acknowledged`, `mitigated`, and `resolved`; invalid state
changes fail closed and operator actions are audited. Shared-service incidents
use a separate public-safe ledger with append-only `investigating`,
`identified`, `monitoring`, and `resolved` updates. `GET /api/public/status`
shows active and recent published incidents without tenant detail.

## Onboarding, migration, export, and offboarding

Onboarding reuses the immutable agreement-acceptance ledger. An authorized
tenant administrator signs a receipt that binds the accepted agreement IDs,
contract version, scope, signer identity and title, outcome, timestamp, and
SHA-256 receipt hash.

Migration reuses the BK28 external import run. A receipt can be accepted only
for a tenant-owned import that reached an acceptance-ready state with no
unresolved errors. Expected counts come from the stored source manifest; the
administrator supplies actual promoted or accepted counts, and every category
must match. Warnings remain visible in the accepted scope rather than being
discarded.

Tenant export evidence dynamically inventories every current database table
carrying `tenant_id`, plus recorded local and customer-cloud file-provider
classes, and then reuses existing product/provider export paths. A new tenant
table therefore becomes a required category without waiting for a copied list
to be updated. The inventory response includes a one-hour, tenant-bound signed
snapshot so audit records created while the artifact is assembled cannot move
the reconciliation boundary. The final exported artifact and snapshot token
are not stored in the receipt. Instead, the administrator records an opaque
artifact reference and SHA-256, exact category counts, connected-provider
inventory, snapshot hash and timestamp, explicit scope, signer, and outcome.
Missing, extra, or mismatched categories—including zero-count categories—or an
invalid, expired, or cross-tenant snapshot produce no completed receipt.
Credential stores export bounded security metadata without secret values, and
immutable contractual/audit categories use evidence summaries rather than
bypassing retention protections.

Offboarding is non-destructive by design:

1. A tenant administrator selects return and deletion categories and signs the
   request; current inventory and legal-hold state are snapshotted.
2. A legal hold records a blocked receipt and prevents approval or completion.
3. Two distinct authenticated platform operators must approve the same scope.
4. Separately authorized data-store and provider actions occur outside the
   evidence API.
5. Completion records zero-count reconciliation for deletion categories,
   provider disposition, backup-expiry evidence, both approvals, and a
   content-addressed proof artifact.

The completion endpoint records evidence only and never deletes data. Agreement
and contractual evidence can remain retained under the applicable legal basis.
The existing retention execution endpoint remains limited to expired,
non-matter chat attachments and does not become a tenant-deletion shortcut.

## Privacy, subprocessors, and assurance

The named subprocessor registry records purpose, data categories, region
boundary, configuration status, terms state, and DPA/BAA evidence state for each
identified provider path. A provider listed as optional or customer-enabled is
not represented as active for every tenant.

A DPA is evaluated when LawHand acts as a processor of customer personal data.
A BAA is evaluated only for a proposed regulated-health-data use where every
required service is eligible. No universal DPA, BAA, HIPAA-ready deployment, or
legal conclusion is claimed.

The maintained assurance record sets a target annual external penetration-test
cadence and security-lead ownership, while truthfully recording the current
state as `planned-not-attained`, with no scheduled window or completed report.
SOC 2, ISO 27001, and a BAA-supported offering have explicit next gates and
false attainment flags; none is represented as achieved.

## Public review endpoints

- `GET /api/public/operating-contract`
- `GET /api/public/service-objectives`
- `GET /api/public/support-policy`
- `GET /api/public/subprocessors`
- `GET /api/public/assurance-roadmap`
- `GET /api/public/security-review-packet`
- `GET /api/public/status`

The security packet has deterministic JSON content, a SHA-256 identity, ETag,
and download filename. Reviewers must still confirm dated provider contracts,
customer configuration, and time-bound production evidence separately.
