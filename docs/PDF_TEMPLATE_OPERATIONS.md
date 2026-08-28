# PDF template operations

This is the customer-support and operator guide for source-backed PDF
generation. The Prepare Form workspace preserves the uploaded page design,
detects existing AcroForm fields, proposes fields from printed or handwritten
samples, and lets an operator correct the result directly on each page before
creating the template.

## Supported input

A source can be PDF, DOCX, TXT, PNG, JPEG, TIFF, or WebP. Image sources are
normalized to PDF so the reviewed page geometry remains stable. A
generation-ready PDF must meet all of these conditions:

- valid, unencrypted PDF with no password;
- at most 250 pages and the configured upload limit (50 MB by default);
- no more than 200 reviewed fields;
- supported text, checkbox, radio, choice, or signature fields;
- no JavaScript, automatic actions, XFA, embedded files, launch/external
  actions, rich media, or unsupported rotated widget appearance.

Static PDFs and scans are supported through local OCR and editable overlay
fields. The reviewer can move, resize, rename, exclude, or add text, paragraph,
date, checkbox, and signature fields. XFA remains unsupported. OCR is an
assistive first pass, not an approval decision: handwriting and low-confidence
regions must be compared with the source before the template is activated.

## Customer workflow

1. Sign in as a user with document-management permission and open
   **Document Automation > Templates**.
2. Choose **Upload Sample**, then drag a completed source document or select it
   from the file picker. The analysis can use existing PDF fields, printed text,
   handwriting, or a mixture of them.
3. Work through every page in **Prepare Form**. Review highlighted confidence,
   drag or resize proposed overlays, add any missed fields, remove false
   positives, and give every included field a unique automation key. Use
   **Test** mode to type representative values into the page itself.
4. Confirm that the completed source was compared with the reviewed field map,
   then choose **Create reviewed template**. New upload-based templates are
   intentionally inactive.
5. Use **Preview draft** for blank or partial diagnostic renders; it never
   records activation readiness. Enter representative values in every
   non-signature field (including long names and addresses), choose **Record
   activation preview**, and inspect every page. Only that representative,
   flattened action records value-bound activation evidence for the current
   user and unchanged template.
6. If an overlay field is too small or has the wrong type, correct it in
   Prepare Form before creation. If an existing AcroForm widget itself is
   defective, correct the source PDF and recreate the template.
7. Activate the template only after the preview has been reviewed. The API
   rejects activation until a successful preview exists and records the
   approving user and time. Changing a PDF body or field map clears that test
   and approval state and makes the template inactive until it is previewed
   again.
8. To create a customer document, choose the active template, select a matter,
   review smart-fill suggestions and their provenance, explicitly review every
   field, and preview the exact values. Inspect every page, then save without
   changing the matter, fields, or output mode. Generation preview evidence is
   valid for 30 minutes; activation evidence is valid for 24 hours.
9. Download the stored matter document and inspect it before use or signature.

Smart-fill is a suggestion system. It can populate deterministic matter,
contact, firm, and user values when available, but every suggested value is
marked for review. It does not make a legal or factual approval decision.

## OCR provider and privileged documents

`TEMPLATE_OCR_PROVIDER=local` is the production-safe default and keeps source
bytes on the application host. Local recognition uses RapidOCR and works well
for clean printed forms; handwriting accuracy varies with scan quality and
writing style.

`TEMPLATE_OCR_LOCAL_CONCURRENCY` controls the bounded local model pool (1-4,
default 2). Each slot owns an independent RapidOCR session and its model memory;
use 1 on a memory-constrained host and raise it only after observing real intake
latency and memory headroom. Changing it requires an API restart.

For firms that approve external processing, `TEMPLATE_OCR_PROVIDER=azure`
enables Azure Document Intelligence Read. Configure the HTTPS resource endpoint
and key with `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` and
`AZURE_DOCUMENT_INTELLIGENCE_KEY`. The backend validates the endpoint, follows
only same-host HTTPS operation URLs, bounds polling, and does not return provider
responses or document text in customer-facing errors. Changing the provider
requires an API restart because settings are loaded at process startup.

Regardless of provider, the analysis response identifies the OCR provider and
confidence source. Do not describe OCR-created templates as fully accurate
until a person has reviewed every page and recorded a representative preview.

## Rendering and editing stack

- [PDF.js](https://github.com/mozilla/pdf.js) renders source pages locally in
  the browser; source bytes are not sent to a PDF-viewer vendor.
- [react-rnd](https://github.com/bokuweb/react-rnd) provides bounded field
  movement and resizing over the rendered page.
- pypdf, ReportLab, and PDFium handle server-side inspection, safe overlays,
  cleanup, and output rendering.
- RapidOCR is the private/local recognition path. Azure Document Intelligence
  Read is the explicit opt-in handwriting tier.

This stack covers the Prepare Form workflow without a proprietary viewer
license. Re-evaluate a commercial SDK such as Foxit, Apryse, or Nutrient only
if the product later requires full PDF authoring, advanced annotation editing,
or provider-managed signing features beyond this reviewed-template workflow.

## Preview versus final output

| Behavior | Draft preview | Activation preview | Save to matter |
|---|---|---|---|
| Requires active template | No | No | Yes |
| Enforces required fields | No | Yes | Yes |
| Requires every mapped field to be reviewed | No | Yes; representative values | Yes; blank/false must be explicit |
| Records activation evidence | No | Yes | No |
| Creates a matter document | No | No | Yes |
| Default PDF mode | Flattened | Flattened | Flattened |
| Signature field | Must remain blank | Must remain blank | Must remain blank for the signing workflow |

For PDFs, **Save to matter** also requires a current generation-preview ID
bound to the same tenant, user, template contract, matter, flattening mode, and
exact field values. The database never retains raw preview field values. Normal
evidence contains field names, counts, integrity hashes, and a server-keyed HMAC
of the values. A reconciliation-blocked row may additionally contain bounded
provider item/drive IDs, an output filename/hash, intended document ID, or a
tenant-scoped local path. That protected operational metadata exists only to
reconcile a storage/database divergence and remains tenant-isolated by FORCE
RLS.
The final render must also have the exact SHA-256 recorded for the reviewed
preview. Generation evidence is consumed atomically with its MatterDocument and
MatterEvent. A lost or retried response returns that existing document
idempotently; it never creates a second file from the same preview.

If two in-flight saves race and deletion of the losing staged object fails, the
successful consumed evidence remains intact while a separate unconsumed
reconciliation record identifies that exact duplicate object for operators.

Consumed evidence follows the saved document's records lifecycle and survives
template deletion (its template reference becomes null). Recent generation
attempts also survive template deletion so a failed save can still persist its
reconciliation block after rollback releases row locks. Generation trimming
removes only non-terminal evidence expired beyond a one-hour safety grace;
draft/activation attempts remain capped newest-first. Cleanup never removes
consumed or reconciliation evidence.

Flattened output removes form widgets and paints reviewed values into the page,
which avoids dependence on a recipient's PDF viewer. An editable output can be
requested through the API, but flattened output is the supported customer
default for a matter-ready artifact.

When saved to a matter, the service:

- verifies the retained source against its SHA-256 digest;
- creates a unique output filename;
- routes the binary through the matter-file store (configured cloud provider
  or local fallback);
- creates the `MatterDocument`; and
- appends a matter event containing the template/source identity, output hash,
  renderer version, reviewed field names, and flattening mode.

The output event is provenance, not an electronic-signature certificate or an
approval record.

## Common errors and recovery

| Message or symptom | Cause | Recovery |
|---|---|---|
| No fields were detected | Blank, unusual, low-resolution, or handwriting-heavy source | Add fields manually in Prepare Form, or rescan at higher quality and analyze again. A zero-detection result is recoverable. |
| Handwriting was assigned to the wrong label | OCR reading order or proximity was ambiguous | Compare the highlighted source region, rename or remove the proposal, and add the correct field manually. |
| OCR service is unavailable | The configured local engine or opted-in Azure resource failed or timed out | Keep the source, retry after the service is healthy, or switch back to local OCR. Never bypass the review confirmation. |
| `Password-protected PDFs are not supported` | Encrypted source | Remove the password in an authorized workflow and upload the unencrypted copy. Do not send the password to support. |
| `Active PDF content ... is not allowed` | Script, action, attachment, XFA, or external behavior | Produce a clean, inert AcroForm copy. Do not bypass the validator. |
| `The original template file is unavailable` | Legacy PDF row or lost uploads volume | Restore the uploads backup when it matches the database; otherwise recreate from the authorized original. |
| `The original template failed its integrity check` | Retained file differs from recorded hash | Stop generation, preserve logs, investigate storage integrity, and restore/recreate from a known source. |
| `Value ... does not fit` | Widget is too small for reviewed text | Shorten the value only if factually correct, or enlarge the source field and recreate the template. |
| Unsupported character/script error | Flattening font cannot safely render the value | Use a source/field/font compatible with that language and retest. Never accept missing or substituted glyphs. |
| Required field empty/unchecked | Final matter render enforces the source/schema requirement | Supply the reviewed value or correct the requirement in the source and recreate. |
| Invalid choice/radio option | Value is not one of the source field's allowed export values | Select an advertised option; correct the source field if its options are wrong. |
| `Source missing - recreate this PDF template` in the UI | Template predates retained-source migration | Recreate from the original source, preview, activate the replacement, then retire the old row. |
| `Run a representative flattened PDF preview` | The last preview was blank/partial, expired, belonged to another user, or no longer matches the source/field map | Enter representative values for every non-signature field, preview and inspect every page, then activate within 24 hours. |
| `The PDF preview expired ... or field values changed` | Matter, values, flattening mode, template contract, user, or the 30-minute evidence window no longer matches | Preview the exact current values again, inspect the result, and save without further edits. |
| `blocked pending storage reconciliation` | Database finalization and staged storage cleanup could not be proven consistent | Do not retry that preview. An operator must reconcile the exact staged object and database outcome. Require a fresh preview unless the original MatterDocument is proven committed. |
| `Template creation commit outcome could not be verified` or retained-source cleanup failed | The source file was staged, but the template-row commit or confirmed-rollback cleanup could not be proven | Do not retry the upload. This path has no preview-evidence row. An operator must use the logged tenant/template IDs to reconcile the exact scoped source directory against the template row and its source SHA-256. |
| Storage warning after generation | Cloud write failed and local fallback was used, or provider metadata is incomplete | Confirm the matter document's reported storage backend, repair the cloud integration, and move/regenerate under the firm's records policy. |

## Operator triage

Do not ask the customer to email a privileged, unredacted legal form unless the
approved support process permits it. Start with the template ID, UTC time,
request ID if shown, source filename, browser, and exact error text.

On the production host:

```bash
docker compose --env-file .env -f docker-compose.hypervisor.yml ps backend frontend nginx
docker compose --env-file .env -f docker-compose.hypervisor.yml logs \
  --since=30m --tail=300 backend
```

For the base-plus-production VPS topology:

```bash
docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.prod.yml \
  logs --since=30m --tail=300 backend
```

Before copying logs into a ticket, remove document text, field values, tokens,
cookies, provider responses, and tenant identifiers not required by the support
case.

For reconciliation-blocked evidence, use only its bounded audit metadata:
backend, provider item/drive ID or tenant-scoped local path, intended document
ID, filename, and output SHA-256. Never follow a display URL. If the atomic
MatterDocument transaction committed, verify the stored object hash, then mark
reconciliation resolved and link consumption to that document. If it did not
commit, delete the exact staged object first, then mark reconciliation
resolved. An unconsumed resolved preview remains retired; require a fresh
preview. Never clear a block merely to make retry possible. If the database is
unavailable, preserve bytes until the outcome can be proved.

Template-create ambiguity is separate: no preview-evidence row exists yet. Use
the logged tenant and template UUIDs to derive only that template's retained
source directory under the configured uploads root. If the template row exists,
verify the file against `source_sha256` and preserve it. If the row is confirmed
absent, remove only the regular, non-symlink source inside that exact scoped
directory. On any query, path-scope, or hash uncertainty, preserve the source
and escalate; do not retry the upload or delete by filename search.

Useful automated coverage:

```bash
docker compose exec -T backend \
  pytest tests/test_document_templates.py tests/test_template_analysis_token.py \
    tests/test_template_intake_images.py tests/test_template_ocr_azure.py \
    tests/test_matter_file_store.py tests/test_document_template_preview_rls.py -q

cd frontend
npm test -- src/pages/TemplatesPage.test.jsx
```

## Release acceptance for PDF templates

Before telling a customer the issue is resolved, test at least:

- one text field, multiline field, checkbox, radio group, and choice field;
- one static/scanned PDF and one standalone image source;
- one clearly handwritten completed form, with every proposed source region
  manually compared and corrected where necessary;
- zero detected fields followed by successful manual field placement;
- a multi-page source with page navigation and field placement on later pages;
- required fields and an intentionally blank optional field;
- a signature field remaining blank;
- a long value that must shrink/wrap and an overlong value that must fail;
- binary preview in the production browser through nginx/CSP;
- flattened matter output download;
- cloud storage success or clearly reported local fallback;
- source download and SHA-256 integrity behavior; and
- an old source-less PDF row showing the recreate path rather than a broken
  render action.

The retained source and generated matter files live under the uploads
persistence boundary and must be included in off-host backup/restore evidence.
