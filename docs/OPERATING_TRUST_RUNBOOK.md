# Operating trust runbook

This runbook operates the versioned customer contract without turning an
objective, planned program, or provider dependency into an unsupported promise.
It supplements—not replaces—the production, backup/restore, BK28 import,
incident-response, and retention runbooks.

## Before customer onboarding

1. Confirm the runtime version/commit and supported production topology.
2. Export the current public security-review packet and record its version and
   SHA-256 in the customer review. Do not edit the packet into a bespoke claim.
3. Confirm the selected named providers, region/configuration, current terms,
   DPA applicability, and whether regulated health data is out of scope. The
   registry itself proves none of those customer-specific facts.
4. Publish only counsel-approved agreement definitions. The tenant
   administrator accepts the exact version and content hash through the
   existing agreement ledger.
5. Create the onboarding receipt only after current acceptance evidence exists.

## BK28 migration acceptance

Use the existing external connection and import-run endpoints. Do not upload or
promote data through this contract workflow.

1. Stage the source bundle and retain its manifest, row counts, checksum
   summary, warnings, and errors.
2. Resolve every import error. Warnings must be included in the accepted scope.
3. Reconcile actual accepted counts against every stored manifest category.
4. Have an authorized tenant administrator sign a `migration` receipt naming
   the import run and accepted scope.
5. Treat a blocked receipt as failed acceptance; never correct the immutable
   row. Resolve the discrepancy and issue a new receipt.

## Tenant export

1. Capture `GET /api/compliance/operating/export-inventory`. It enumerates
   every current tenant-id database table and recorded file provider; do not
   substitute the narrower retention summary.
2. Use existing product and customer-authorized provider export paths to build
   the artifact; do not place customer data in receipt metadata.
3. Hash the final artifact, count every declared category, and issue a
   `tenant_export` receipt with an opaque artifact reference.
4. Deliver the artifact through an approved customer channel. The receipt is
   evidence of scope/count/hash reconciliation, not the artifact itself.
5. A missing, extra, or mismatched category is blocked and must not be described
   as a complete export.

## Support and incidents

Classify incoming support against the public S1–S4 definitions. Tenant requests
must not contain credentials or unnecessary client content. Follow the stored
policy version and due time; escalate missed objectives according to the
published policy.

For shared-service impact, create a sanitized public incident with confirmed
affected services and an `investigating` update. Append material changes as
`identified`, `monitoring`, or `resolved`. Never publish customer names, record
content, credentials, internal addresses, filesystem paths, or speculative root
cause. Tenant-specific communication remains in the support case.

## Offboarding and deletion evidence

The API never performs tenant deletion.

1. Confirm the tenant administrator's signed return/deletion scope and current
   inventory snapshot.
2. Stop if any legal hold is active. Preserve the blocked receipt.
3. Obtain approvals from two distinct platform operator identities. A repeated
   approval by one identity does not count.
4. Execute any authorized data-store, customer-cloud, integration-provider, and
   account actions through their existing controlled runbooks. Preserve legal
   and agreement evidence as required.
5. Record each provider as deleted, returned, customer-controlled, or not
   applicable with an opaque proof reference. Record each backup class and its
   evidence-backed expiry.
6. Reconcile every requested deletion category to zero before recording
   completion. The final proof is append-only and content-addressed.

An evidence receipt cannot authorize work that was not separately approved.
Never use this rehearsal against a production tenant or perform destructive
deletion without explicit customer and operator authorization.

## Assurance maintenance

- Review the named subprocessor record whenever a provider path, data category,
  region boundary, or applicable term changes.
- Review the penetration-test record at least quarterly. A target annual cadence
  remains planned until an approved window, scope, owner, and completed report
  are recorded.
- Update certification next gates only from current evidence. `attained` stays
  false until an authoritative external result proves otherwise.
- Re-export the security packet after any contract version change and preserve
  the prior packet hash with the customer review.

## Acceptance rehearsal

The backend operating-trust test performs a production-shaped, non-destructive
customer lifecycle: agreement acceptance and onboarding receipt; BK28 staged
import reconciliation; tenant export receipt; S1 support escalation; public
incident resolution; offboarding request; duplicate-operator rejection; two
distinct approvals; deletion-proof reconciliation; and legal-hold blocking.
Migration safety, RLS coverage, immutable-ledger migration text, frontend API
bindings, public packet truth boundaries, and the production frontend build are
validated separately in CI.
