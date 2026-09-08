# Chat and retrieval validation on September 7 2026

## Scope and evidence standard

Live browser validation used an authorized test tenant on production build
`13982e611e0830439d8ef003a1fbf9e5b1a27a05`. All newly uploaded records are
explicitly fictional. The fixture generator is
`scripts/create_rag_validation_fixtures.py`; it produces TXT, PDF, and DOCX
files plus a SHA-256 and extracted-text manifest. Queries intentionally omit
the expected answers. A completed answer alone is not a retrieval pass.

Distinguish three mechanisms: current-turn attachment text injection,
persisted matter-document retrieval, and public-authority search. Passing one
does not establish the others. These findings describe this build and account,
not every customer configuration.

## Fixture answer key

| Record | Facts absent from the test question |
| --- | --- |
| Kestrel intake TXT | Archive phrase `indigo otter lantern 7294`; custodian Mira Quill; original meeting October 14, 2026 |
| Kestrel inventory PDF | 37 amber and 12 violet folders; derived total 49; Cabinet Q7, second floor; marker `COPPER-FINCH-5831` |
| Kestrel amendment DOCX | Meeting superseded to October 21, 2026, 10:30 AM Central; approver Rowan Vale; marker `SILVER-BADGER-9062` |

No fixture establishes a settlement amount or bank account number. The PDF
was rendered and visually checked. DOCX text round-trip extraction passed;
local rendering lacked LibreOffice, so the synthetic DOCX was converted with
the existing isolated dev1 renderer and its page was inspected locally.

## Live results

| Test | Observation | Assessment |
| --- | --- | --- |
| Standard attachment | TXT upload accepted, but send blocked with “Attachments require a private route” and instruction to select Premium | Route gate works; disclosure comes after upload |
| Premium TXT | Exact phrase and custodian returned with the correct file download citation | Current-turn text extraction passes |
| Premium PDF | Both counts, total, location, and marker correct; total described as arithmetic rather than quoted fact | Current-turn PDF extraction passes |
| Premium DOCX | New date/time, approver, and marker correct; no invented settlement amount | Current-turn DOCX extraction and missing-fact restraint pass |
| DOCX cross-file reference | Could not recover original date from earlier TXT; only newest upload counted as a source | Cross-turn attachment availability needs separate validation |
| All-three follow-up without reattachment | Zero retrieved sources; repeated facts from earlier answers, missed the original date, rendered dead source markers, and claimed all files were available | Failed attachment continuity and source provenance |
| Matter upload of all three formats | Saved through OneDrive; subsequent provider-ID byte reads match all three original SHA-256 hashes | Provider storage and byte retention pass |
| Matter cloud sync | File listing grew from 10 to 13; no new `documents`/`chunks` index rows | Metadata sync is not content ingestion |
| Fresh linked matter query, no attachments | Three cloud sources retrieved; TXT correct; PDF and DOCX returned as raw object/ZIP bytes | Failed cloud PDF/DOCX extraction |
| Existing Atlas indexed-record control | Seeded attachment chunks exist, but new conversation retrieved 16 unrelated mailbox/cloud items rather than transaction documents | Failed intended matter scope/relevance; not a vector retrieval pass |
| All three files attached on one turn | Correct document-date chronology, original/amended meeting comparison, 49-folder total, custodian, both markers, and three valid source links | Cross-document reasoning passes when full relevant context is supplied |
| Generated draft inspection | Opened the document card; draft names the custodian, amended date/time and reconciliation action, stays under 80 words, and remains unsent | Draft substance passes; raw internal source markers remain visible in artifact text |

## Confirmed findings and remediation tracking

1. **Route and UI expectations disagree.** Standard is described as “Everyday
   research and drafting”; the empty state asks users to attach documents and
   link matters, and the context banner says “Using your profile.” In this
   account Standard is public/general and cannot process private attachments.
   Earlier output called the signed-in workspace a “public AI forum.” The
   interface should disclose actual context capabilities before work starts.
2. **Public profile isolation has an indirect exception.** Both chat paths
   clear private LLM context, but derive the public search default jurisdiction
   from the user's profile. Remove that default on public/general routes and
   test both streaming and nonstreaming paths.
3. **A built-in prompt bypasses legal-research classification.** “Compare the
   governing standards” does not match the legal-research classifiers on the
   tested build. Add narrow governing/legal-standards coverage and regression
   tests. A prior response claimed knowledge of practice areas and earlier
   matter discussions without a source. That claim is unsupported; the audit
   has not demonstrated a history leak.
4. **Retrieval outage is hidden after completion.** The stream reported public
   search “Service unavailable”, then displayed “Response complete”. Preserve
   public retrieval status with the saved response and show its coverage limit.
5. **Source counts and origins disagree.** The same answer displayed “0 cited”
   above and “1 cited” below a valid inline citation. Earlier attachment origins
   changed from “Uploads” to “Firm/cloud” after the next turn. The Sources rail
   remained zero with three uploaded records. Audit source normalization and
   whether the rail is meant to include attachments.
6. **Conversation history needs responsive sizing.** The user's narrow-view
   screenshot shows a wide conversation rail crowding the message pane. Test
   mobile/drawer behavior and support a clear collapse/resize control at
   intermediate widths. Static mobile support alone is not visual acceptance.
7. **Explicitly created conversations stay generically titled.** Multiple
   populated conversations remain “New Conversation”; verify title generation
   for the New conversation button path.
8. **Attachments disappear from subsequent retrieval.** Both chat paths pass
   only current request attachment IDs to the extraction helper. Durable
   conversation attachments are not reloaded when a later turn omits them.
   Restore only eligible same-tenant, same-conversation records, retain explicit
   subset semantics, and enforce the route's private-context policy.
9. **Connected cloud files use binary decoding instead of extraction.** The
   Google Drive and OneDrive content paths use `resp.text` on downloaded file
   bytes. Reuse bounded format-aware extraction and test real PDF/DOCX files.
   Direct chat attachment extraction already succeeds on these exact files.
10. **Stored, attached, and indexed are different availability states.** Atlas
    fixture chunks have a conversation ID. Private RAG intentionally excludes
    conversation attachments; do not remove this scope boundary just to make a
    test pass. The UI needs an explicit path to promote appropriate documents
    into matter retrieval and an accurate indication of which records are
    available. An existing document row/chunk count does not prove eligibility.
11. **Connected retrieval can return unrelated mailbox items for a document
    review.** The Atlas request returned unrelated emails and even cited an
    irrelevant notification while explaining the coverage gap. Investigate
    source planning, empty-keyword fallbacks, relevance filtering, and missing
    matter-folder behavior. Do not treat arbitrary provider rank as relevance.
12. **Planner route policy needs its own admission check.** The planner resolves
    Standard and includes the question, tenant name and matter context in its
    prompt even when the main answer uses Premium. A main-answer admission
    check alone does not establish that every preparatory model call uses an
    approved route. Propagate and validate the selected approved route or use an
    independently approved planner profile. Do not silently widen Standard's
    policy or background access to solve this.
13. **Source syntax is not consistently rendered in artifacts.** A combined
    `[verify, source: document:...]` marker appeared literally in an otherwise
    correctly cited answer. The draft document also showed raw internal source
    markers. Source rendering/export should preserve usable references.
14. **Attachment excerpts have an undocumented coverage limit.** Live testing
    confirmed the 4,000-character cap: a 9,393-character TXT returned its opening
    checkpoint correctly but could not answer either fact from the final
    addendum at character 9,289. The answer appropriately declined to invent
    values, while the source locator still said “Full attached document”.
    Expose truncation and separately implement/test long-document retrieval.

## Long-document control

`scripts/create_rag_tail_fixture.py` creates
`CSA_RAG_20260907_Long_Record.txt`, SHA-256
`d314c61a3b26efebd1b4758b0842ddad765f600a5c5d7af6c730fbbc125a8c18`.
Its opening label is `SCARLET-HERON-3148`; the final signed addendum contains
`TEAL-LYNX-8625` and reviewer Anika Frost. These values were absent from the
question. In fresh Premium conversation
`43709bc9-ab95-4e76-bb52-428f7ec484cf`, uploaded document
`db2c72e8-b8a8-44b4-918d-1231a0778b31` produced the opening label with a correct
file citation and explicitly declined both unseen tail facts. This is a pass
for abstention and a failure of full-document coverage, not a successful
long-document retrieval test. The model saw only roughly the first fifty of
120 archive entries and asked the user to provide remaining sections despite
the complete original file already being uploaded.

## Public corpus observations

Read-only production checks found zero rows in the app's local `public_chunks`
fallback table and no configured separate VECTORDB fallback. Public research
uses the external CourtListener MCP service. Its health and tool catalog return
HTTP 200, while actual case-law and legal-authority searches each timed out at
35 seconds; corpus status and court coverage timed out at 20 seconds.

The external database is not empty. PostgreSQL statistics estimate roughly
3.98 million opinions, 17.15 million opinion chunks, 582 thousand legal
documents, and 12.54 million legal-document chunks. These are approximate
statistics, not an exact completeness or searchable-coverage claim. Source
metadata includes US Code, eCFR, North Dakota Century Code, Ohio rules, and IRS
materials. Loaded data and enabled/reviewed search scope are distinct: some
loaded sources are disabled.

Read-only activity inspection found search SQL still active after more than
41 minutes alongside an embedding update active for more than 42 minutes.
Client timeout does not establish cancellation of backend database work.
Do not repeatedly submit expensive searches, equate the health endpoint with
readiness, or restart/terminate unrelated ingestion to make a test pass.
The four diagnostic request queries were individually identified by PID and
query start time and cancelled after their client timeouts; unrelated embedding
work and pre-existing searches were left alone.

The small enabled CMS estate-recovery source was read directly to establish a
known corpus question independently of the broken search endpoint. That proves
record presence, not successful application retrieval. No legal answer was
validated merely from the model's general knowledge.

## Responsive observations

At 390 by 844, the conversation history opens as a roughly 340-pixel drawer
with a working close control, and the document has no page-wide horizontal
overflow. At 820 by 1180, the closed history drawer gives the answer the full
available width. Mobile support therefore exists; the user's concern is best
tracked as adjustable/collapsible history at other layout widths, plus sticky
context/legend overlap and the tall composer. The viewport override was reset.
At the default 1,244-pixel-wide viewport, the global navigation and conversation
rail together occupy about 595 pixels. The sticky context and review legend
obscure portions of the scrolled answer, and the source table requires an inner
horizontal scrollbar. This reproduces the user's wider-layout concern even
though the mobile drawer works.

## Outstanding validation and remaining risks

The first remediation patch addresses attachment continuity and excerpt labels,
cloud PDF/DOCX extraction with bounded downloads, empty planner queries, planner
route admission, profile-free public/general jurisdiction defaults, persistent
public-retrieval failure status, and citation-count/source-origin consistency.
It does not establish a live pass until the exact patch has been deployed and
the failed scenarios repeated. Premium planning now follows the selected
Premium chat route, so that preparatory call also uses the Premium model.

Follow-up remediation priorities:

1. Public MCP query deadlines and cancellation: review
   `mcp-server/mcp_server/database.py` and request/repository call paths. Bound
   connection and statement lifetime for request work, distinguish API sessions
   from ingestion, and prove a timed-out request stops its own database work.
   Replace request-time global coverage scans with bounded/precomputed coverage;
   verify query plans and indexes against the actually deployed schema before
   claiming an index fix. Liveness and searchable readiness need separate tests.
2. Document availability: provide an explicit authorized path from stored or
   conversation-only documents into matter retrieval, with processing state and
   source lineage. Preserve tenant, matter, conversation and retention boundaries.
3. Relevance and coverage: test narrow matter-document questions against unrelated
   mailbox content, query-aware long-document selection, and scanned-PDF handling.
   Rejecting malformed plans alone does not solve relevance for valid keywords.
4. Usability: repair generic conversation titles, clarify the Sources rail,
   improve history sizing at intermediate widths, and render/export usable
   citations in generated artifacts instead of raw internal markers.

- Repeat cross-turn attachment and reload tests after the patch is deployed.
- Repeat fresh linked-matter retrieval after cloud binary extraction is fixed.
- Verify unlinked/other-matter behavior and that citations open the actual file.
- Use known enabled corpus records to test public retrieval after diagnosing
  timeout/cancellation/readiness behavior.
- Verify long-document tail retrieval, scanned PDFs, and citation export.
- Complete live validation for Google consumer/Workspace and Shared Drives;
  OneDrive success and mocked provider tests do not establish those results.
