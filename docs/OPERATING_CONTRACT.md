# LawHand customer operating contract

Version `2026-08-28.1` is the canonical claim registry for customer trust
discussions. The machine-readable copy is served at
`GET /api/public/operating-contract`; this document explains how to use it.

## Claim states

- **Implemented** — the product behavior exists and is covered by current
  repository evidence.
- **Verified** — an operational rehearsal or release gate has produced current
  evidence; evidence ages and must be refreshed.
- **Policy-committed** — an operating practice is defined, but it is not an
  uptime, response-time, RPO/RTO, or service-credit promise.
- **Provider-dependent** — availability and terms depend on the customer’s
  selected cloud, communications, payment, signing, or model provider.
- **Planned** — a roadmap item. It must never be described as attained.
- **Unavailable** — intentionally not offered in the current release.

The registry is the source of truth for public trust material, security-review
responses, support conversations, and onboarding receipts. The public endpoint
returns opaque evidence identifiers only; internal repository paths, secrets,
customer records, hostnames, and credentials are never included. The current
release intentionally leaves several acceptance outcomes planned because their
supporting workflows and evidence packets do not yet exist.

## Customer lifecycle

Onboarding uses the existing tenant agreement ledger and BK28 provisioning/import
path. The receipt records the agreed scope, selected providers, acceptance
criteria, version/hash of the applicable documents, and an authorized signer.
Migration safety remains governed by `scripts/rehearse_tenant_migration.py` and
the CI tenant-safety gate.

Offboarding is approval-based and tenant-scoped. Export is reconciled against
the requested record classes and connected-provider limits. Deletion is not
executed through a generic cascade: legal holds, matter records, agreement
evidence, provider-held data, and backup retention are preserved or separately
authorized. The existing retention endpoint only executes its documented,
narrow non-matter chat-attachment cleanup and emits audit evidence.

## Support and incidents

Support hours, severity definitions, escalation contacts, and any response
targets belong in the customer’s written order/onboarding record. The public
contract does not silently turn operational objectives into an SLA. Incident
updates state what is known, affected, mitigated, and still under investigation;
public health output is sanitized and does not disclose tenant or topology
details.

## Assurance boundary

DPA/BAA applicability, subprocessors, regions, provider retention, and security
terms are reviewed for the selected customer configuration. No universal DPA,
BAA, HIPAA, SOC 2, ISO 27001, penetration-test, or certification claim is made
unless a separately maintained current evidence record proves it. The security
review packet is an evidence snapshot, not an independent audit opinion.
