# Brief Check

Brief Check is a review-first quality-control workflow for a matter brief. It accepts an uploaded DOCX/PDF and, optionally, an opposing brief. The service extracts text within bounded limits, normalizes recognizable case/statutory citations, and records each finding with its document location, source identity/tier, retrieval time or corpus version when available, confidence, ambiguity, and limitations.

Results are evidence states—not legal conclusions. A missing or ambiguous source is surfaced for attorney follow-up. Quotation checks are exact when accessible source text contains the quoted text; otherwise the result is unknown or a mismatch with context. Treatment and currentness remain unknown unless a timestamped, accessible signal is available. Candidate supporting or contrary authorities are citation-led and bounded; they are not comprehensive research and do not establish good-law status.

Each run is tenant/matter scoped and content-hash idempotent. Attorney decisions are recorded in an append-only audit table. Reviewers can export a linked DOCX review report and a table-of-authorities draft. Provider, storage, or corpus failures are surfaced as unavailable/unknown states rather than silently replaced with an inference. Documents are not sent to an external provider by this workflow.

The processing limit is 15 MB and 300 PDF pages / 1.5 million extracted characters. Convert legacy `.doc` files to DOCX or PDF first. Reviewers remain responsible for checking the original authority, pin cites, quotation context, currentness, and the scope of research.
