# Signing fields on generated documents

In Template Studio, choose a signature field and assign a signer role, such as
`client` or `attorney`. A signature field can request initials instead. Assign a
role to a date only when it should record the signing date; ordinary template
dates remain filled values. Role edits use the editor's normal save and undo.

Signatures, initials, and dates assigned to a signer are completed during signing.
They are excluded from Smart Fill and pre-signing completion requirements; ordinary
required dates still need values. The fill panel labels signer-linked dates as
completed during e-signing. Word generation keeps signing placeholders blank and
rejects supplied signing values. PDF generation excludes signing fields from its
value contract and clears existing signing-date AcroForm values.

Test and publish the map, then preview and save the document to its matter.
Supported PDF templates retain their authored positions. Missing roles or
unsupported signing page geometry leave the generated document usable and
require final review before signing. Word templates reflow, so their
signature positions must be reviewed on the final generated PDF.

In the matter's E-Signature panel, select the saved document and assign one
signer to each field role. **Review PDF signing positions** loads the saved
PDF, verifies its digest, and lets the author add, move, resize, or remove
signature, initials, and date fields. This review uses the final PDF, not the
Word source preview. Sending is the explicit dispatch action. Reviewed
placements are rendered in the client's in-document signing view and stamped
onto the executed copy. A document with no reviewed placements still gets a
signature placement: request creation uses the PDF's own signature widgets,
then printed signature lines it detects, then a signature block at the foot of
the last page.

Supported placement pages are unrotated, with matching zero-origin
MediaBox/CropBox and unit scale. Other layouts fail validation because the
provider documents inconsistent conversion for nonstandard sizes. This limit
applies to positioned dispatch; the source preview does not certify signing
coordinates. Provider credentials and webhook configuration remain required.

## Verification

Tests cover role mapping, page/digest validation, placement geometry,
ordinary-date compatibility, generated document serialization, the template
publication/generation/request path, and the portal signing manifest. No
signature emails are sent by these tests.
