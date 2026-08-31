# Citator control plane

LawHand’s citator control plane is a review-first, source-bound layer over a
promoted public-authority corpus. It is not a claim that LawHand supplies
complete commercial-citator coverage or that any authority is “good law.”

## Evidence model

Every citator record is tied to a promoted `authority_corpus_versions` release,
the release’s as-of time, a named reviewed source, and a source version. The
control plane admits only sources classified as `official`, `open`, or
`licensed`, with a completed source review, catalog implementation evidence,
and an active, reviewed entry in the citator's explicit public
catalog/manifest admission list. Custom, tenant, firm, private-shaped,
unreviewed, and prohibited source keys fail closed; a public URL or metadata
on an arbitrary source row never establishes public-authority eligibility.
The same exact source/manifest lineage is enforced by ingestion, serving,
coverage, audit, treatment, watch, and alert paths; revoking either endpoint of
a citation suppresses the edge and every derived assessment or alert.

`authority_records`, `authority_history_facts`, and
`authority_citation_facts` contain deterministic source facts. They preserve
direct/later history, amendment/repeal/status records where the permitted
source provides them, citation depth, issue/context text, source links, spans
or locators, source hashes, and observation timestamps. Promoted snapshot facts
are immutable.

Citator materialization is a candidate build step and accepts only a reviewed
staged/canary release. The resulting records and facts are included in the
release state digest before audits and promotion. Startup performs at most one
bounded legacy conversion; it cannot append facts to a normal promoted release,
and the unaudited legacy bootstrap remains suppressed until a separately
reviewed successor is staged and promoted.

`authority_treatment_assessments` is separate. It records an explicitly
provisional machine interpretation, confidence, abstention, model/policy
versions, and linked source-fact evidence. A non-abstaining assessment cannot
be saved without source-fact IDs that resolve to the same authority and corpus
version; its URL, span, locator, and hash are copied from those stored facts,
not accepted from caller JSON. `authority_treatment_reviews` appends an
attorney acceptance, rejection, request for more evidence, or override; it
never overwrites the machine record. Review requires an active,
authorization-basis-backed reviewer principal provisioned by a single-use,
short-lived, HMAC-signed backend command tied to an authenticated administrator
credential, canonical registration body, and durable nonce. An arbitrary
display name or conventional "internal" caller cannot be treated as an
attorney. Only accepted or overridden, non-stale assessments
have an effective treatment label; rejected, needs-more-evidence, pending,
stale, and superseded assessments remain effectively `unknown`. A new promoted
snapshot deliberately does not inherit derived treatment—the assessment must
be recomputed and reviewed against its new as-of state.

## Customer and API behavior

`get_authority_treatment` shows deterministic facts separately from machine
interpretation, includes source/version/as-of/currentness data, and marks a
result `incomplete` or `unavailable` when source coverage, currentness, or
evidence is not established. `get_citator_status` returns the promoted release
and known gaps. Neither endpoint emits a good-law conclusion; an absent negative
record, citation count, or `no_decision` label is never treated as a favorable
status.

The Research MCP settings page describes these two read contracts. Existing
Brief Check results remain bounded citation/quotation review evidence; this
control plane does not close Brief Check’s provider-backed resolution or
rehearsal gaps.

## Watches and alerts

Saved watches are tenant-and-matter scoped and contain no authority text.
They require explicit consent, at least one delivery channel, and a short-lived
backend-signed assertion that binds the tenant, canonical matter, and creating
principal **plus the exact save action, authority, delivery channels, and quiet
hours**. The nonce is atomically consumed, so replay, action substitution, and
mutation-body tampering fail closed. The backend checks the canonical LawHand `matters` table before
minting that assertion; the public-authority database does **not** claim to
have a matter foreign key or independently prove matter ownership. Database RLS
requires `app.current_tenant_id` for reads and writes; callers cannot list,
revoke, or enqueue another tenant’s watch. Alert events are idempotent by watch
and event fingerprint. Enqueue and delivery persistence lock the active watch
row through their consent/state recheck, so a queued revocation cannot commit
between that check and the database write. Delivery attempts can be queued,
quiet-hour/no-consent suppressed, failed, sent, or revoked. An attempt must
also name one of the watch's consented channels. Revocation removes consent
before later enqueueing and appends a durable watch audit row.

An alert's customer-visible source URL, evidence span/locator/hash, and payload
are constructed from one stored history or citation fact for the same promoted
authority/version. Callers cannot supply an alert URL or free-form payload.

This release contains no production notification delivery. A production sender
must honor quiet-hour configuration, record every delivery attempt, recheck
revocation immediately before the external send, and use a separately
authorized customer/staff channel. The database lock serializes audit state; it
cannot make an external provider send atomic with revocation.

## Evaluation and release gate

The control plane supports sanitized evaluation fixtures with positive,
negative, distinguished, no-decision, and statute/regulation change examples.
Before any claim of authoritative citator completeness, the operator must use a
licensed partner or attorney-reviewed benchmark and publish precision, recall,
abstention, and attorney edit/override measurements with regression thresholds.
Until then, all coverage/currentness status remains bounded and provisional.
