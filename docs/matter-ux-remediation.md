# Matter workspace remediation

Implementation companion to the additive planning PR [#395](https://github.com/mattpainter701/legalapp/pull/395). The original plan remains unchanged and open for collaboration.

## Attorney workflow

1. Create **Jane Doe divorce**, from intake or a prospective lead. Open Documents.
2. Use **Attach template** to choose an active Template Studio library item. Populate its fields, inspect the generated output, and save it into the current matter and folder. A matter-local render cannot be redirected to another matter by the picker.
3. Prepare and review the fee agreement as the attorney. Open **Client paperwork**, select the agreement already saved in the matter (or upload the reviewed PDF), choose additional forms, and enter a checklist of requested client uploads. The questionnaire is optional. Select email and/or SMS using the existing consent controls.
4. Send one initial message with a secure paperwork link. Each selected signing PDF has its own signature request and status. Non-signing forms can be downloaded and returned. Staff verify returned forms separately.
5. Completing the fee agreement queues the portal welcome on the selected channels and creates an assigned follow-up due **24 elapsed hours** after completion. Other outstanding forms do not delay this milestone. The existing delivery certainty, retry, consent, and SMS quiet-hour behavior applies.
6. Client uploads use the existing physical `client_uploads` storage routing and system folder. Staff review each submitted item before marking the requirement complete. All required paperwork completed creates the existing separate scheduling follow-up.

Internal portal signing remains a document acknowledgment with completion evidence. Positioned signature fields require the existing external signing workflow; intake rejects such documents rather than silently dropping their positions. This change does not add a new e-sign provider.

## Matter workspace

- The gear stores personal field and section visibility per tenant/user in this browser. Edit Details retains all values; hiding never clears data.
- Overview, Activity, Documents, Client Portal, and Billing are the primary destinations. Team and Workflow are reachable under Matter settings, subject to tenant presentation policy.
- Platform tenant settings can hide optional matter panels. Overview and settings remain available. This is presentation policy, separate from existing enabled modules and server authorization. Sessions receive updated policy through user-info refresh/sign-in.
- Documents offers **Folder** and **Detailed** views. View preference is stored per matter in this browser. Folder view navigates direct children; Detailed retains the existing searchable listing. Secondary import, portal-link, and cloud tools are collapsed under Document tools.
- **Move** files within the matter explorer using existing logical filing. Existing cloud objects keep their original storage paths. **Copy** reads the source bytes and saves a new private object to the destination's storage route, with a unique name and a retained request ID for retries. A copy never inherits client visibility or signatures and never deletes the source.
- A failed/ambiguous copy commit retains the new uniquely named object for reconciliation; automatic deletion could destroy a successfully committed copy. The server logs the copy ID for investigation. Durable background physical-move/cleanup operations remain a separate follow-up from the plan.

## Templates and email

Studio's Draw field tool accepts a rectangle and a variable name. Whiteout draws an independent cover region. Existing move/resize/delete/undo/redo/save behavior applies. Prepare Form also supports drawn named fields; its existing per-field source cover remains available. Whiteout is an authoring operation, not a general-purpose redaction guarantee.

Email Client supports matter files, uploads, and generated templates. Every attachment is explicitly reviewed. The send manifest includes the reviewed SHA-256 digest; changed bytes return 412 and require a new review. The server loads tenant/matter-scoped bytes and preserves release-workflow restrictions. Gmail, Microsoft, and SMTP retain separate attachment parts. Limits remain ten files and 2 MiB total; the preview response is not cacheable. Attachment previews use the shared PDF canvas.

## Access and delivery contracts

New UI-created intake packets set `portal_after_signing=true`. Legacy API callers default to the prior behavior for compatibility. The initial invite/session is restricted server-side to its own packet, selected documents/signatures, questionnaire, and upload endpoints until fee completion. General messages, billing, arbitrary downloads, and unrelated signing requests are denied. Full portal access uses the same invite after verified fee completion, followed by the queued welcome message. Existing invite expiry/revocation still applies.

All selected document/folder queries constrain tenant and matter. Fee and extra signatures require stored completion-artifact evidence before automatic completion. Staff-verification receipts remain an explicit alternative. Repeated milestone reconciliation creates one task and one queued delivery per channel. Repeated upload submissions for the same requirement/document do not duplicate events.

## Validation and remaining plan work

- PostgreSQL Jane Doe scenario covers selected forms, distinct signature requests, the fee milestone before other forms complete, a DST boundary, client-upload review, scheduling, and replay behavior.
- Tests cover scoped paperwork endpoints, stale attachment digests, provider MIME parts, copy failure/replay/source preservation, template field/whiteout persistence, personal/tenant view preferences, and browser navigation through the matter workspace.
- Browser tests use synthetic local API fixtures; they do not send email/SMS or exercise live cloud providers. Existing provider tests use controlled adapters. Production deployment is outside this PR.
- Further work retained from the collaborative plan: durable physical cloud Move operations and cleanup queue; bulk file actions; preference sync across devices; full live-provider acceptance of the complete onboarding journey. These are not represented as delivered by logical filing or fixture tests.

## Mission-critical acceptance pass

The follow-up acceptance pass adds real HTTP/PostgreSQL coverage for both manual matter creation and lead conversion, plus a browser journey against the actual FastAPI application and disposable PostgreSQL database. Only outbound delivery is captured; browser API responses and document storage are real. The browser host is guarded by DEV_MODE, E2E_TEST, a database name containing e2e, and explicit E2E_ONBOARDING_ACCEPTANCE. Production startup never imports it.

Client signing now includes a PDF preview/download and explicit source review. Failed evidence storage cannot complete signing. Storage token refresh uses a separate database session so it cannot commit a partial signer action. Successful signing commits the evidence, portal-access milestone, and assigned 24-hour task together; the existing worker delivers the queued welcome. The intake panel loads the selected client's email and labels the initial send as paperwork.

Acceptance assertions include no paperwork sent merely by opening a matter, replay without duplicate packets, restricted pre-sign access, consent required, recoverable storage failure, the fee milestone while other forms remain outstanding, exactly one follow-up, retained certificate bytes, questionnaire submission, client_uploads routing, staff review and the distinct scheduling task. Live Microsoft/Google/SMS provider acceptance remains separate from these captured-delivery checks.
