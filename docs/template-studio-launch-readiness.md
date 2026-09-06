# Template Studio: reviewed details and live drafts

Start by uploading an existing filled PDF or Word template. Review detected details against the original, remove non-variable text, and create the draft. In the visual workspace, select Word text or place PDF fields to add missing details. Internal names are generated automatically; staff edit the label and “Fills from” source. The setup checklist leads to scenario selection, a representative test and explicit publication.

## Published wording stays available

The mutable row is the authoring draft. Generation and generation previews use the immutable `published_version_no` snapshot, including its field map and source hash. Draft test failure never invalidates that publication. The Test tab always opens the authoring draft. Pause still prevents generation. Replacing original source bytes requires a fresh template, preserving the old release's source. A new publication or pause during a PDF save invalidates the in-flight release contract; a draft edit alone does not.

## Custom facts and review

Non-sensitive scalar custom matter and client fields appear in the field source catalogue as `custom.matter.<definition UUID>` and `custom.contact.<definition UUID>`. Definition type/options come from the server. Sensitive, inactive and unsupported collection/contact-link definitions are unavailable. Existing values are record data, not a claim that they are accurate. Smart Fill keeps already-entered values and displays missing/source status.

For a linked matter detail, choose “Review a detail from a matter document” during generation or testing, select an attached PDF/Word/text source, then read it. This bounded extractor recognizes exact `Label: value` lines, with strict existing custom-field type validation. It does not infer unlabeled narrative facts, OCR images, parse tables or create children/asset arrays. Missing/unsupported/conflicting lines remain visible. The reviewer can correct a value after reading the original and must explicitly accept it. Replacing a saved value needs a separate checkbox. Extraction never writes a fact.

The proposal/accept endpoints under `/api/templates/fact-review/{matter_id}/{document_id}/{field_id}` require both `manage_documents` and `manage_matters`. Proposals expire after 30 minutes and bind tenant, actor, matter/document/field identities, definition version, source SHA-256/provider version, and previous value HMAC/update stamp. Acceptance rereads the source before final database locks (OAuth refresh may commit), restores tenant context, then compares fresh locked metadata/current value. Stale evidence is rejected. Documents are capped at 10 MB and 100 PDF pages/200,000 extracted characters. Word packages pass the existing expansion/member/XML safety validator before parsing. Source content is data; no source instructions are evaluated.

Accepted details use existing typed custom values. A `template_fact_reviewed` matter event records source/version pointers, reviewer/time and accepted HMAC, without raw field value or excerpt. Smart Fill attaches that evidence only while the stored value still matches the reviewed update. Automation should read the existing custom-field models and respect their current sensitivity/definition state. No new schema is introduced.

## Scenario example

Configure a non-sensitive boolean matter detail “Has children”. Bind a template field to it. Under “When to use this template”, name the scenario “Divorce with children”, select that linked detail and Yes. The separate “Divorce without children” template requires No. A missing answer never counts as No, and generation evaluates the saved detail rather than an arbitrary entered field override. Number/date/single-select answers use typed controls. This is a single equality rule, not legal advice or automatic selection of the correct legal form. Repeating child/asset records are not implemented by this change; party repeats retain their existing typed source.

## Release acceptance still required

Local unit/component tests do not prove customer usability, live provider access, migration/RLS integration, or exact rendered-page correctness. Rehearse PDF and Word generation, source changes, pause/publication races, zero/one/several-child scenarios and conflicting intake answers against the release candidate. Two representative office staff should import, map, test and publish real templates without developer intervention. Keep scans/tables/unlabeled extraction and arbitrary repeated children/assets explicitly outside the supported first increment.

### Local verification for the initial review

119 focused backend tests passed, including real Word text replacement from a selected span, actual PDF label extraction, source/actor/value/definition rejection, Workspace published-snapshot reuse and custom binding provenance. 66 component tests passed; production frontend build and focused ESLint/ruff checks passed. Synthetic Chromium renders at 1440 and 390 pixels had no page errors or horizontal overflow; computed heading color is white and the Edit label is visibly readable. These are development fixtures, not customer acceptance. CI PostgreSQL/RLS coverage and final-head merge gates remain required.
