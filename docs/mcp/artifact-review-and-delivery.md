# Artifact review and approved document delivery

This document owns the review and delivery contract for assistant-generated
documents. Workspace MCP and LawHand Chat share the same proposal handlers.
The [BYO-harness channel contract](byo-harness-channel-contract.md) owns channel
positioning, platform AI metering, and connection budgets.

## Human review

New document proposals create a `GeneratedArtifact`, immutable revision, verified
tenant-cloud working copy, Review task, and explicit review requirements.
`work_artifact_review_requirement` records the assigned reviewer, stage, round,
and exact revision. `work_artifact_approval` records each decision, actor, reason,
text SHA-256, cloud-file SHA-256, document identity, and task version.

The default firm policy is staff then attorney. A firm administrator who also
has `approve_legal_work` can choose attorney-only review in Workflow Settings.
The policy applies to new proposals; existing requirements keep their reviewers.
Policy changes are audited and use optimistic concurrency protection.

The assigned staff reviewer can approve or request changes. Only the assigned
attorney with live `approve_legal_work` authority can complete attorney review.
An attorney override of a pending staff stage requires a reason and creates a
separate skip decision preserving the bypassed reviewer. Both review stages
verify the current cloud bytes before recording an approval. These controls are
available in the work board and document cards in LawHand Chat.

Edits create a new immutable revision and supersede the old requirements.
Requesting changes ends the review round; a later review creates another round.
Old decisions remain visible in the document's review history. Database guards
reject stale hashes, duplicate decisions, out-of-order approvals, reviewer
substitution, and updates or deletion of decision evidence. Tenant RLS and
composite foreign keys apply to requirements, approvals, and delivery receipts.

Existing queued document tasks without review-spine rows retain the prior
immutable task-snapshot execution path. A pre-migration staff decision adopted
into the spine is explicitly labelled `legacy_task` with its original timestamp.
Once an artifact has spine requirements, execution requires its current exact
attorney approval. Legacy compatibility is not accepted for a document attachment.

## Separate delivery approval

`propose_client_email` accepts an optional `artifact_id`. LawHand resolves the
current approved revision, attorney decision, document, filename, and byte hash
on the same matter. The assistant cannot provide a storage path, substitute
attachment bytes, or invent recipient addresses. Recipients remain server-resolved
matter parties. The resulting email task shows the attachment and requires its
own human approval before sending.

Approval and the worker both revalidate the immutable attachment binding. The
worker locks the artifact, reads its tenant-cloud copy, verifies the exact hash,
and sends those in-memory bytes. No later provider read can replace the verified
attachment. The single-document limit is 2 MiB; larger or unavailable documents
stop before any send. Microsoft Graph, Gmail MIME, and configured SMTP all carry
the same verified attachment. Transport errors never cause a fallback send
through a second provider.

`work_artifact_delivery` appends receipts bound to the immutable outbound attempt,
attorney approval, revision, resolved recipients, and document hash. A queued
receipt commits with the email approval and durable job. Terminal results retain
provider evidence. An interrupted or ambiguous send records `outcome_unknown`
and is never automatically replayed. Late uncertainty remains recordable even
after the source artifact changes. Document approval itself is not a delivery.

MCP continues to expose reads and proposals. No MCP review, approve, send, or
execute bypass is introduced. A harness calling a separately metered LawHand
service still incurs that service's normal admission and usage accounting.

## Verification and operational impact

Migration `165_artifact_review_spine` follows
`164_word_derived_source_evidence`. CI runs
`scripts/rehearse_artifact_reviews.py` against the migrated disposable PostgreSQL
database with a non-superuser, non-bypass-RLS role, including stale evidence,
review ordering, immutable rows, revision supersession, attempt binding, and
cross-tenant reads. Focused service and UI tests cover human decisions and actual
attachment encoding across all three email transports.

The new tables are excluded from demo cloning and included in authorized demo
purging. Downgrading removes this evidence and is therefore a rollback operation,
not a routine way to disable the feature. Disable proposal access through the
existing tenant and grant controls instead.

Provider references: [Microsoft Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)
and [Gmail message and attachment encoding](https://developers.google.com/workspace/gmail/api/guides/sending).
