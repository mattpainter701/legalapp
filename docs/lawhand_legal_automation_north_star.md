# LawHand Legal Automation North Star

**Status:** Architecture and release contract
**Companion design:** `docs/matter_automation_workspace_mcp.md`

## Product outcome

LawHand is the legal workspace, policy engine, and system of record. The
connected model is a replaceable research, reasoning, and drafting harness.
LawHand Chat, Codex/ChatGPT, Claude, OpenCode, normal LawHand UI/API clients,
and later scheduled automations must all use the same tenant-scoped capability
layer.

A user should be able to ask:

> Pull matter XYZ, review its history and current posture, research the
> governing process, recommend the next case steps, and draft document Y for my
> paralegal and attorney to review.

The resulting artifact, review tasks, approvals, matter documents, and delivery
records must be identical regardless of which authorized client initiated the
request.

## Canonical architecture

```text
LawHand Chat ---------+
Workspace MCP --------+       +--> private legal RAG service
LawHand UI / REST ----+------>+--> canonical application capabilities
Scheduled workflows --+       |       |
                              |       +--> matter/task/document/template services
                              |       +--> work artifact + exact revision
                              |       +--> review requirements + approvals
                              |       +--> deterministic file/email workers
                              |
                              +--> actor, tenant, scopes, RLS, audit, idempotency
```

MCP is an adapter, not a second implementation and not a database back door.
The direct-table platform MCP prototype must be replaced by an adapter over
`backend/app/services/automation_capabilities.py` and canonical application
services.

The existing research MCP and workspace MCP have distinct identities:

- Research MCP accepts research-product credentials and cannot access LawHand
  matters, contacts, tasks, documents, templates, or firm configuration.
- Workspace MCP uses OAuth 2.1 authorization code + PKCE for human users and
  carries a real `tenant_id`, `user_id`, client/grant, scopes, expiry, and
  revocable token family.
- Named service credentials are an advanced option for unattended workflows.
  They never impersonate a human and receive narrower grants.

The workspace endpoint is a public Streamable HTTP MCP resource. Official
OpenAI documentation supports remote MCP `server_url` configuration, OAuth
access tokens, tool allowlists, and explicit approval policies:
<https://developers.openai.com/api/docs/guides/tools-connectors-mcp>.

## End-to-end workflow

1. Resolve the requested matter inside the credential-derived tenant and the
   actor's matter visibility. A model-supplied id can never choose a tenant.
2. Assemble a bounded matter snapshot: posture, parties, team, timeline, open
   tasks, deadlines, document index, and relevant matter/firm instructions.
3. Search private matter evidence and public legal authority separately. Keep
   source ids, links, dates, jurisdiction, freshness, and uncertainty.
4. Propose next steps. Separate known facts, retrieved authority, assumptions,
   missing information, and decisions requiring an attorney. Never silently
   create or change a legal deadline from model inference.
5. Resolve the requested document template server-side and create a versioned
   work artifact with immutable source/template provenance.
6. Materialize the generated DOCX immediately into the tenant's configured
   cloud share. LawHand stores the workflow/document binding, provider
   metadata, hashes, and audit evidence; it does not become the byte store.
7. Show the same cloud-backed working copy in chat and on a matter Review task.
8. Route the exact cloud-backed revision through the firm's staff and attorney
   review policy.
9. If an authorized user adopts an external Word, LibreOffice/OpenOffice, or
   compatible cloud-editor change, read the current cloud object, validate the
   bounded DOCX package, preserve its exact bytes as a new verified snapshot,
   create a new artifact revision, supersede the prior working copy, and reset
   review requirements.
10. Approval verifies one exact revision and hash for its stated purpose.
    Filing and delivery remain separate, explicitly approved actions.
11. Immediately before delivery, re-resolve current matter-party recipients and
   send through a deterministic durable worker.
12. Record the provider result honestly. An ambiguous timeout is
    `outcome_unknown`, is not described as sent, and is never auto-retried.

## Cloud-first document invariant

A generated draft is not complete when chat text is produced. It is complete
only after a tenant-cloud working copy has been created and read-back verified,
or after a durable provider operation has explicitly recorded an unresolved
outcome. The working copy remains in the tenant's connected provider. LawHand
retains metadata, provider object/version/ETag, content hashes, revision
bindings, and audit evidence—not document bytes.

The mutable cloud working copy and the immutable LawHand evidence snapshot are
different concepts. Approval binds the exact verified snapshot. A later cloud
edit never silently changes an approved or pending review revision: an
authorized refresh must adopt it as a new revision and restart review.

DOCX is the current interoperability boundary for Word, Word Online,
LibreOffice/OpenOffice, and Google Drive file storage. Native Google Docs
conversion/edit/export requires an explicit provider-specific policy and
fidelity tests; storing a DOCX in Google Drive is not the same as a native
Google Doc.

## Capability catalog

Capabilities are business operations, not one-for-one copies of REST routes.
All ids and result sizes are bounded and revalidated server-side.

| Group | Initial capabilities | Effect |
|---|---|---|
| Matter resolution | `find_matter`, `get_matter_snapshot` | Read |
| Matter evidence | `list_matter_documents`, `get_matter_document_text`, `search_matter_corpus` | Read |
| Work state | `list_matter_tasks`, `list_matter_recipients`, `list_eligible_reviewers` | Read |
| Templates | `list_document_templates`, `get_template_requirements` | Read |
| Public research | existing case-law and authority tools through private RAG | Read |
| Planning | `propose_case_plan`, `propose_task` | Propose |
| Drafting | `draft_matter_artifact`, `revise_matter_artifact` | Propose |
| Correspondence | `propose_client_delivery` | Propose |

The general model-facing catalog does not expose free-standing `send`, `file`,
`approve`, delete, arbitrary SQL, arbitrary HTTP, or generic CRUD tools.
Proposal capabilities create reviewable work and halt at the LawHand approval
boundary.

A later MCP review action is a human control, not agent autonomy. It must
require an authenticated reviewer role, an exact artifact revision and content
hash, an expected version, a fresh confirmation, and the same policy enforced
by the LawHand review UI.

## First-class work artifacts

`Task.pending_action` remains a compatibility envelope during migration, but a
task cannot be the canonical document or approval record.

```text
work_artifact
  id, tenant_id, matter_id, kind, title, status
  current_revision_id, review_policy_id
  created_by_user_id, created_via, source_conversation_id

work_artifact_revision
  artifact_id, version, content/body, file metadata
  template id/version/hash, resolved variables, source bindings
  content hash, created_by_user_id, created_via

work_artifact_review_requirement
  artifact_id, sequence, reviewer_role, reviewer_user_id
  required, status, superseded_at

work_artifact_approval
  artifact_id, revision_id, requirement_id, reviewer_user_id
  decision, override reason, timestamp, content hash, stamp metadata

work_artifact_delivery
  artifact_id, approved_revision_id, channel, recipient bindings
  provider/message id, certainty, queued/sent/opened timestamps
```

Chat and MCP return the same `artifact_id` and LawHand deep link. Every edit
creates a new revision. Approval records are immutable and bind one exact
revision and content hash.

## Review and approval policy

The default policy has two stages:

1. paralegal or legal-secretary review;
2. attorney approval.

Firm configuration may select attorney-only review for solo practices or
defined low-risk work. An attorney with the required capability may override
an unfinished staff stage, but LawHand records the skipped requirement,
attorney, timestamp, reason, revision id, and content hash. Any edit invalidates
the approval state for the old revision.

Approval means the revision is approved for its stated purpose. It never
silently means “email this client.” Filing and delivery are separate durable
events, with separate confirmation and audit evidence.

## Template resolution

Template selection is deterministic and server-side:

1. an explicit active template selected by the user;
2. the matter's configured template for the document type;
3. the firm's matter-type/document-type default;
4. the firm's active general document template;
5. a neutral LawHand DOCX fallback.

The revision records the chosen template id, immutable template version/hash,
resolved variables, output mode, and unresolved fields. The model can recommend
a template and draft missing prose, but it cannot activate an unreviewed
template or invent required firm data.

## Durable orchestration

The “OpenClaw/Hermes for legal” behavior comes from a durable, bounded workflow
runtime above individual tools—not from an unbounded MCP catalog.

A workflow run stores the user's objective, authenticated actor, matter,
allowlisted plan, source bindings, capability results, checkpoints, approvals,
and final outcomes. It can pause for missing information or human review and
resume without repeating completed side effects. Scheduled/event-driven runs
use service identities with narrow grants and still produce reviewable work;
they do not impersonate an attorney.

## Security invariants

1. Every workspace call has a verified tenant and human actor or named service
   identity.
2. Tenant comes only from the credential; no argument, model text, document, or
   header may override it.
3. Every database transaction establishes tenant RLS context and also uses an
   explicit tenant predicate.
4. Role/capability and matter visibility are enforced inside the tenant.
5. Model-authored recipients, reviewers, and template activation state are
   never trusted; LawHand resolves eligible records server-side.
6. Prompt-injected documents cannot cause a final side effect.
7. Approval binds an exact revision, content hash, reviewer, and expected
   version.
8. Delivery is idempotent, audited, and preserves uncertain provider outcomes.
9. Generated files are created in verified tenant cloud before review; approval
   never performs a late or fallback upload.
10. A provider-side edit is never silently substituted for the reviewed bytes;
   adoption creates a new revision and resets review.
11. Chat, MCP, REST, UI, and scheduled workflows cannot implement competing
   business rules.
12. Audit/diagnostic events retain ids, shapes, hashes, timings, and outcomes by
    default—not full privileged documents or raw prompts.

## Delivery sequence

### 1. Shared application layer

- Keep `automation_capabilities.py` as the single public contract.
- Extract remaining matter, document, template, and task operations from
  routers into transaction-friendly application services.
- Add bounded matter snapshot, evidence, reviewer, and template reads.

### 2. Artifact and staged review

- Add artifact, revision, review-requirement, approval, and delivery models.
- Dual-read existing `Task.pending_action` during migration.
- Link Review tasks and chat cards to the same artifact.
- Implement staff→attorney review, attorney override, and invalidation on edit.

### 3. Template-backed drafting

- Implement the resolution chain and immutable template provenance.
- Reuse the current PDF/DOCX template engines and preview evidence.
- Preserve a fresh-document fallback when no firm template applies.

### 4. Workspace MCP

- Add OAuth 2.1/PKCE, protected-resource and authorization-server metadata,
  client registration compatibility, consent, refresh rotation, and revocation.
- Add a separate Streamable HTTP workspace endpoint over the shared catalog.
- Keep service credentials separate from human grants.

### 5. Shared assistant orchestration

- Make LawHand Chat and external MCP runs produce the same workflow-run and
  artifact records.
- Add pause/resume, missing-input checkpoints, and source-bound case plans.
- Add scheduled/event-driven proposals only after interactive workflows pass.

### 6. Client and production verification

- Verify Codex/ChatGPT, Claude, and OpenCode-style clients through production
  TLS and current auth flows.
- Test two tenants, multiple roles, matter visibility, revoked grants, injected
  instructions, stale revisions, duplicate proposals, and ambiguous delivery.
- Keep production flags fail-closed until the complete gate passes.

## End-to-end release gate

The first release is complete only when the same fixture succeeds through
LawHand Chat and at least one external MCP client:

1. Find “Matter XYZ” without exposing another tenant's similarly named matter.
2. Load its bounded snapshot, documents, open tasks, and timeline.
3. Search matter evidence and public authority with retained citations.
4. Propose next steps while identifying assumptions and attorney decisions.
5. Draft the requested document through the template-resolution chain.
6. Materialize and read-back verify one DOCX in the tenant's cloud before it is
   shown in chat and on the matter Review task.
7. Edit it through Word/Word Online or LibreOffice/OpenOffice, explicitly adopt
   the exact changed bytes as a new snapshot, and prove staged review resets.
8. Record staff review and attorney approval; separately test attorney override
   and cloud drift rejection.
9. With separate approval, deliver the exact approved revision to the current
   client email on file, and record the provider result honestly.
10. Revoke the MCP grant and prove subsequent MCP calls fail without disrupting
    the user's normal LawHand web session.

Production enablement also requires all of these gates:

- a committed provider-write outbox/state machine plus reconciliation worker
  for process crashes and ambiguous provider writes;
- complete OAuth 2.1/PKCE client registration, refresh, disconnect, revocation,
  and key-rotation lifecycle;
- real-tenant Google Drive, Microsoft Graph/SharePoint/OneDrive, Word Online,
  exact download, and LibreOffice/OpenOffice end-to-end tests;
- deterministic firm-template resolution and DOCX/PDF rendering fidelity tests;
- explicit native Google Docs conversion/export policy and fidelity tests;
- externally anchored or WORM-retained integrity-chain checkpoints;
- full PostgreSQL/RLS cross-tenant, ethical-wall, role, concurrency, and crash
  recovery tests; and
- adversarial exact-byte DOCX adoption coverage for hostile packages, drift,
  stale versions, and review invalidation.
