# Shared firm fields in templates

The Field Library distinguishes firm-wide values from client and matter data.
In Template Studio, select a field and choose **Fills from → Firm profile**:
firm name, address, phone, email, or website. Field names can be unique to each
template; the source mapping determines the value.

Firm administrators maintain these values under **Admin → Settings → Firm
Branding**. This is the same existing profile used by invoices and statements.
The Field Library shows the current value, whether it is configured, and which
saved templates explicitly use the source. Client and matter custom fields
continue to hold separate values for individual records.

Smart Fill reads the current firm's profile even without a matter selected. A
missing profile value stays missing and can be entered for this document or
corrected once by a firm administrator. Refreshing suggestions after a profile
update offers the updated value without overwriting a user's populated entry.
The fill screen labels these as saved profile values rather than AI confidence
estimates. Users should still review the document before using it.

Mappings remain references to the profile. They do not freeze a copy of the firm
value in each template. Saved generated documents retain their existing values;
updating the profile affects subsequent Smart Fill runs. Editing a single filled
document never writes back to the firm profile.

## Boundaries

- Values are scoped to the authenticated tenant; “shared” never means shared
  between different firms.
- The resolver reuses the existing branding name/address fallbacks. It does not
  introduce a second profile store or change branding permissions.
- Sources must be selected explicitly. Sample text, detected email/phone values,
  and unbound field names cannot update the profile or implicitly select it.
- The current profile represents one firm-wide contact set. Multiple named
  offices and arbitrary shared firm custom values are future work and need an
  explicit office choice/value model; they must not silently use a matter's
  address or overwrite the firm-wide contact set.
- This change does not add an embedded office editor, native DOC output, or a
  document-rescan engine. See [the document workflow direction](template-studio-document-workflow.md).
