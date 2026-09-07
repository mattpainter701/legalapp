# Signing fields on generated documents

In Template Studio, choose a signature field and assign a signer role, such as
`client` or `attorney`. A signature field can request initials instead. Assign a
role to a date only when it should record the signing date; ordinary template
dates remain filled values. Role edits use the editor's normal save and undo.

Test and publish the map, then preview and save the document to its matter.
PDF templates retain their authored positions. Word templates reflow, so their
signature positions must be reviewed on the final generated PDF.

In the matter's E-Signature panel, select the saved document, choose Dropbox
Sign, and assign one signer to each field role. **Review PDF signing positions**
loads the saved PDF, verifies its digest, and lets the author add, move, resize,
or remove signature, initials, and date fields. This review uses the final PDF,
not the Word source preview. Sending is the explicit dispatch action.

## Contract and integrity

Matter documents expose `positioned_fields` and `signing_placement_required`.
Each placement carries a unique field ID, type, signer role, one-based page,
bottom-left PDF-point rectangle, page dimensions, and generated-source SHA-256.
Migration 163 stores these fields on matter documents and signature requests.

Signature creation inherits saved placements when omitted by the caller.
Documents requiring review cannot silently create an unpositioned request.
The request validates the loaded source bytes, page bounds and role mapping;
the provider revalidates the persisted manifest against the bytes sent.
The internal portal rejects positioned requests before request creation because
it records acknowledgement without placing fields into the source document.
Existing documents with no placements retain their previous flow.

Dropbox Sign receives a JSON-encoded flat `form_fields_per_document` array in
the multipart request, with `document_index: 0`. The PDF upload uses `files[]`;
canonical date fields map to `date_signed`, per the [send API contract](https://developers.hellosign.com/api/signature-request/send). Page positions use top-left
72-DPI coordinates; width and height use 80-DPI units, including the documented
two-unit width adjustment. See the provider's [field format](https://developers.hellosign.com/docs/sdks/open-api/form-fields-per-document/)
and [coordinate rules](https://help.dropbox.com/integrations/how-to-use-the-form-fields-per-document-parameter).

Supported placement pages are unrotated US Letter with matching zero-origin
MediaBox/CropBox and unit scale. Other layouts fail validation because the
provider documents inconsistent conversion for nonstandard sizes. This limit
applies to positioned dispatch; the source preview does not certify signing
coordinates. Provider credentials and webhook configuration remain required.

## Verification

Tests cover role mapping, page/digest validation, coordinate conversion,
ordinary-date compatibility, generated document serialization, the template
publication/generation/request path, and stubbed provider dispatch. No live
signature requests are sent by these tests.
