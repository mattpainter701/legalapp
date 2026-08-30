# Clio transition sync research and product direction

**Date:** 2026-08-30
**Decision:** Build a source-neutral onboarding layer. Support a staged
Clio-to-LawHand transition with continuous sync, while accepting CSV/XLSX
exports from PracticeMaster, Case Builder, and other legal systems. Do not
begin with unrestricted bidirectional writes.

## Customer outcome

A firm can connect Clio, see its contacts and matters in LawHand, and continue
operating in Clio while it moves work deliberately. During the transition,
LawHand shows what came from Clio, when it was last refreshed, and whether the
firm has cut that record over. CSV import provides a non-OAuth route for an
initial snapshot, historical data, or a firm that cannot authorize a
connection.

## Pilot-account constraint

LawHand will not buy or maintain its own paid Clio account. The first pilot will
use access granted by a participating customer. Clio has no separate sandbox;
its documentation advises developers to avoid using customer accounts for
testing. Treat this pilot as a customer-approved production-data exercise, not
as a substitute for a test environment.

For Clio Manage, a private app is created in a paid Clio account and is limited
to that one firm. It is suitable for this customer's pilot but cannot become
LawHand's reusable multi-customer integration. The customer administrator
should create or supervise creation of the private app, retain account control,
and authorize only the least-privilege read scopes. LawHand must never collect
or retain the customer's Clio password.

Before running the pilot, obtain written approval for the named tenant, exact
data types, authorizing administrator, callback URL, data-retention period, and
support contacts. Start with a purpose-created test contact and matter, then a
small, customer-selected cohort. Use read-only access, record every API call and
import result, and provide an immediate disconnect/revocation path.

For a reusable connector, apply for Clio's free developer-account route after
the trial process and pursue the public-app review when the pilot has proven
the data mapping and user experience. This meets the no-paid-LawHand-account
constraint, but it is a separate launch track from the single-firm private app.

## Source-neutral onboarding layer

The customer outcome is not a collection of vendor-specific import buttons; it
is a reliable way to move a firm's clients and matters into LawHand. Build the
following shared foundation before multiplying adapters:

- an `external_sources` connection record, including vendor, tenant, owner,
  authorization state, and retention policy;
- an immutable `external_records` map from source identifier to LawHand record,
  with source timestamps and raw normalized payload version;
- import/sync jobs with row-level outcomes, warnings, rollback boundaries, and
  downloadable audit reports;
- an explicit mapping layer from vendor fields to LawHand's canonical contact,
  organization, matter, relationship, note, task, calendar, and financial
  staging types; and
- a review queue for duplicates, missing parent relationships, unsupported
  field types, and source-data changes.

All adapters must use that foundation. A CSV/XLSX upload, an OAuth pull, and a
customer-approved database export should differ only in acquisition and field
mapping—not in deduplication, auditability, or cutover behavior.

### First adapter tracks

| Source | Acquisition for initial release | Ongoing behavior |
|---|---|---|
| Clio Manage | Customer-authorized private-app pilot; CSV fallback | Read sync and webhooks while Clio is authoritative. |
| PracticeMaster / Tabs3 | Customer-assisted CSV/XLSX export | Re-import on demand; evaluate live access only after customer demand and vendor support are proven. |
| Case Builder | Confirm the exact product and collect a redacted sample export | Export-led import unless the confirmed product provides a supported API. |
| Other legal systems | Source-discovery questionnaire and redacted sample export | Start export-led; promote to a sync adapter only where recurring update value justifies it. |

PracticeMaster's practical export path can provide matters (`CMCLIENT`),
contacts (`CMRELATE`), journals (`CMJRNL`), calendar (`CMCAL`), costs
(`CMCOST`), and fees (`CMFEES`) as spreadsheet data. Treat the product's files
as vendor-specific input, never as LawHand's internal schema. Case Builder is
an ambiguous product name: DISCO's Case Builder documents XLSX export for
witness lists, but that does not establish a full matter/contact export. Confirm
the exact vendor, edition, and available export before estimating the adapter.

For every non-Clio source, collect a source inventory, redacted sample files,
field definitions, relationship keys, document-volume estimate, and a signed
customer authorization before implementation. Do not connect to a customer's
database directly or infer a vendor API from UI behavior.

## Recommended ownership model

| Stage | Authoritative system | LawHand behavior |
|---|---|---|
| Connected | Clio | Read and refresh data; preserve Clio IDs and source timestamps. |
| Preparing cutover | Clio | Queue LawHand changes for review; do not overwrite a newer Clio value. |
| Cut over | LawHand | Stop inbound writes for the selected record/workstream and make LawHand editable. |
| Archived | LawHand | Retain the source link and audit history; do not resume sync implicitly. |

This intentionally avoids the most dangerous transition failure: two systems
silently updating the same field. A future reverse sync should be enabled only
for explicitly supported fields, with an audit record and a per-field conflict
policy.

## V1: reliable read sync

1. A firm administrator authorizes the LawHand OAuth application with the
   smallest read-only Clio Manage scopes needed for contacts, matters, custom
   fields, and webhooks.
2. LawHand stores encrypted credentials, performs an initial cursor-paginated
   import, then subscribes to change notifications.
3. A periodic reconciliation job catches missed events and reports records
   that could not be read because of Clio visibility restrictions.
4. The import creates stable mappings from `(tenant, source, Clio record ID)`
   to LawHand records. Email/name matching may help suggest a merge but must
   never be the idempotency key.
5. The firm can mark a matter (and its associated client record) as cut over.
   That action is explicit, auditable, and reversible only through an
   administrator-led review.

### Initial data

Start with contacts, organizations, matters, matter-client links, related
contacts, tags, and useful custom fields. Add notes, tasks, calendar entries,
communications, documents, time, billing, and trust data only as separate,
reviewed modules. Financial and document imports require their own
reconciliation, retention, and access-control design.

## CSV alternative

Offer a guided upload for Clio's Contacts and Matters exports, with optional
related-contact and note files. The flow must validate headers and row formats,
preview creates/updates/skips, flag ambiguous matches, and generate a
downloadable outcome report. It uses the same source-mapping and audit tables
as OAuth import so a customer can later connect Clio without duplicate records.

## Technical constraints

- Clio Manage and Clio Grow use separate APIs. Treat Grow leads and intake as a
  separate optional connector, not a side effect of the Manage connection.
- Request every needed field explicitly: most Manage endpoints otherwise return
  only minimal defaults.
- Follow cursor pagination rather than assuming a single response. Manage lists
  are capped at 200 records per response.
- Respect Clio's current API limits, observe rate-limit headers, and retry `429`
  responses with backoff.
- Validate signed webhooks before accepting an event and reconcile periodically;
  events reduce polling but do not replace an audit trail.
- Represent unreadable/redacted data as such. The authorizing user's Clio
  visibility still limits what LawHand receives.

## Sources

- Clio bulk export inventory and administrator requirement: <https://help.clio.com/hc/en-150/articles/9813884849947-Understand-Data-Migration-Processes-in-Clio-Manage>
- Clio Manage OAuth authorization: <https://docs.developers.clio.com/api-docs/clio-manage/authorization/>
- Clio Manage permissions: <https://docs.developers.clio.com/api-docs/clio-manage/permissions/>
- Clio custom fields: <https://docs.developers.clio.com/guides/clio-manage/custom-fields/>
- Clio pagination: <https://docs.developers.clio.com/api-docs/clio-manage/paging/>
- Clio rate limits: <https://docs.developers.clio.com/api-docs/clio-manage/rate-limits/>
- Clio Manage webhooks: <https://docs.developers.clio.com/guides/clio-manage/>
- Clio Platform / Grow OAuth applications: <https://docs.developers.clio.com/api-docs/clio-platform/applications/>
- Clio developer accounts, trials, and lack of a sandbox: <https://docs.developers.clio.com/handbook/getting-started/get-a-developer-account/>
- Clio Manage private-app constraints: <https://docs.developers.clio.com/handbook/getting-started/building-private-apps/>
- Clio's testing guidance: <https://docs.developers.clio.com/handbook/build-your-app/>
- PracticeMaster/Tabs3 export workflow and file inventory: <https://supportcenter.mycase.com/en/articles/9370339-how-to-export-data-from-tabs3-with-practice-master>
- DISCO Case Builder witness export: <https://cbsupport.csdisco.com/hc/en-us/articles/23064763110157-Export-witnesses-from-Case-Builder>
