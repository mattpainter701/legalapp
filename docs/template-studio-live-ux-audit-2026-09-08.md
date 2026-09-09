# Template Studio live UX audit — 2026-09-08

Audited the authenticated deployment at getlawhand.com. The displayed build was
`9f96a0048c02c0321b15ff8c0a48a20ad191a2f1`. These are observed UI results,
not a claim that all render infrastructure is enabled or all publication paths
have passed acceptance.

## Existing draft journey

- Library, document editor, field library, test values, version history, and
  DOCX/PDF diagnostic previews were exercised.
- Both diagnostic formats returned output. Word output offered a download;
  PDF offered an embedded viewer. Exact generated pagination was not verified.
- Preview also changes the separate test button to “Testing…”.
- Diagnostic preview is reported as “Generate output Passed”, while reopening
  Test correctly reports the version as not tested. The result must distinguish
  preview generation from recorded publication testing.
- PDF guidance names “Record activation preview”, but the action is called
  “Test this draft”. Activation, publication, and active/inactive terminology
  are mixed across the journey.
- In the narrow browser window, field properties appear below the document.
  The review count has no direct action to reach the next field needing review.
- With only drafts available, Generate is disabled without a direct action to
  resume testing and publication.

## Shared Parenting Plan sample import

The user-authorized `Shared Parenting Plan Template.docx` was selected through
Upload Sample. The UI reported a 49 KB Word source and rendered 13 pages.
No sample contents are included in this audit report.

Observed defects:

1. Detection reports 22 fields from 40 underscore areas, with 18 unmapped.
   Unmapped areas have a count-only warning, without a navigable review queue.
2. Labels include long sentence fragments and context-free labels such as
   “And”, “Shall Pay To”, and “By 2”. These require manual naming before reuse.
3. The same screen reports “22 need review” and “Needs verification: 0”.
4. A literal personal name in the source was not represented by a detected
   field. Blank detection must not imply that all variable source content has
   been identified or removed.
5. The warning recommends Fields for locations beyond the source-outline
   limit, but that text view truncates at 20,000 characters. Selecting the last
   detected field only changes the property panel; it does not reveal its
   source context or navigate the document to the corresponding page.
6. Manual page navigation remains functional: opening Pages and selecting
   page 13 displays the last source page. This is a recovery path, but users
   must discover the field's location themselves.

The import remained at source review. The source-comparison attestation was
not checked: the unmapped regions and literal sample values have not all been
resolved. No parenting-plan draft was published or saved to a matter, and no
premium-AI proposal was requested. Generated output for this sample remains
untested until field/source review can be completed accurately.

## Follow-through

### Improvements in this PR

- Search detected labels, keys, and source text; filter uncertain fields and cycle through them without certifying review.
- Read the selected field's original paragraph in context; show an explicit explanation when complete context is unavailable.
- Find selected fields across the rendered PDF using the same conservative matching as page highlights. Jump to a unique match, list multiple matching pages, and report missing/failed searches without guessing. Page text is cached only for the mounted source; obsolete searches are discarded.
- Jump directly to a page and move between the document and review panel on small screens.
- Give complete Word navigation context its own 100,000-character cap while retaining the 20,000-character editor limit. Truncated or oversized outlines remain excluded because partial text cannot establish uniqueness.
- Distinguish Preview ready from Passed; show progress on the requested action and consistent review counts. Guide an empty generation library back to template setup.

Re-analysis of the supplied parenting-plan file using this branch retained 205 source paragraphs (22,950 characters), all 22 detected fields, and the final field's source paragraph. The former outline-limit warning is gone. This does not certify the remaining unmapped blanks or literal sample values.

Browser verification used the actual components with a local synthetic 13-page PDF. The review queue selected the final signature, Find selected field navigated to page 13, and the intended placeholder was highlighted. No sample contents are included in fixtures or this report.

The preview route remains synchronous in this PR. Durable render-job integration is a separate implementation item, not a claim made by the progress labels here.

## Follow-up: generated output and matter bindings

A separate QA cover sheet has explicit firm, matter, client and preparer bindings plus a required manual review note. Live Smart Fill supplied distinct Cedar Ridge and Apex demo values, preserved the saved firm name, and left an unconfigured firm email blank. Changing matters cleared the prior values and manual note. A representative publication test returned a PDF, but the browser-native PDF object remained blank in the embedded browser; publication and matter-save verification remain pending until the actual output can be inspected.

The follow-up replaces that native object with the existing PDF.js canvas renderer. The generated bytes now have page selection, previous/next navigation, fit-to-width, zoom, and visible loading/error states. Each output mounts a fresh viewer, and field edits still invalidate the preview and its review evidence. Download remains available; this change does not claim to repair host-browser download handling or provide a screen-reader text layer.

Local browser verification rendered a synthetic three-page PDF, selected the final page, changed zoom, and scrolled through its footer. Focused component and workflow tests cover the exact returned Blob, page boundaries, resizing, errors, and invalidation after field edits.

Track fixes on `fix/template-studio-ux-audit`, based on the deployed main SHA.
Include clear preview/test states, consistent publication terminology,
actionable field review, full-document location recovery, and durable render
job UI integration after checking the backend's actual supported capabilities.
The initial source review's proposed job integration is a design item, not a
live-verified backend availability claim.
