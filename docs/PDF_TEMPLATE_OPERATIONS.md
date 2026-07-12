# PDF template operations

This is the customer-support and operator guide for source-backed PDF
generation. The renderer preserves the uploaded PDF's page design by filling
real AcroForm fields; it does not guess coordinates from ordinary page text.

## Supported input

A generation-ready PDF must meet all of these conditions:

- valid, unencrypted PDF with no password;
- at most 250 pages and the configured upload limit (50 MB by default);
- at least one AcroForm widget and at most 200 widgets;
- supported text, checkbox, radio, choice, or signature fields;
- no JavaScript, automatic actions, XFA, embedded files, launch/external
  actions, rich media, or unsupported rotated widget appearance.

Static PDFs, scans, and XFA-only government forms are not generation-ready.
The upload analysis may extract readable text from them, but creation is
blocked until an operator adds fillable AcroForm fields with a PDF authoring
tool and uploads the revised file. OCR is not part of the PDF template
renderer.

## Customer workflow

1. Sign in as a user with document-management permission and open
   **Document Automation > Templates**.
2. Choose **Upload Sample**, select the source PDF, and analyze it.
3. Review every detected field name, label, page, type, required marker, and
   choice/radio option. Resolve every warning before creating the template.
4. Choose **Create reviewed template**. New upload-based templates are
   intentionally inactive.
5. Use **Preview draft** for blank or partial diagnostic renders; it never
   records activation readiness. Enter representative values in every
   non-signature field (including long names and addresses), choose **Record
   activation preview**, and inspect every page. Only that representative,
   flattened action records value-bound activation evidence for the current
   user and unchanged template.
6. Correct the source PDF if a field is too small or has the wrong type. Source
   files and field maps cannot be safely replaced in place; recreate the
   template from the corrected PDF.
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
| `This PDF has no fillable AcroForm fields` | Static/scanned/XFA-only source | Add AcroForm fields in a PDF authoring tool, save as a standard PDF, then upload again. |
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
  pytest tests/test_document_templates.py tests/test_matter_file_store.py \
    tests/test_document_template_preview_rls.py -q

cd frontend
npm test -- src/pages/TemplatesPage.test.jsx
```

## Release acceptance for PDF templates

Before telling a customer the issue is resolved, test at least:

- one text field, multiline field, checkbox, radio group, and choice field;
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
