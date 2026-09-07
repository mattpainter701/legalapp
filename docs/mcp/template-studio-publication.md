# Workspace template publication and reviewed facts

Workspace document proposals resolve a versioned active template through the same immutable published snapshot as Studio generation. An authoring draft or failed draft test must not replace the approved wording in an automation proposal. Existing unversioned legacy approved templates retain their previous compatibility behavior; versioned templates fail closed if their publication or original source is unavailable. Tenant, matter, module, stage and jurisdiction checks continue to apply.

A named scenario additionally checks the current non-sensitive custom detail, independently of caller-supplied replacement values. Missing/unmatched facts prevent the proposal. No arbitrary evaluation or inferred child records are introduced.

For the canonical API, acceptance, evidence, sensitive-data exclusions and extraction limits, see [Template Studio reviewed details and live drafts](../template-studio-launch-readiness.md). The browser fact-review endpoints do not add an MCP tool or authorize automatic fact acceptance. Workspace proposals still require the existing human review/release workflow.

Wiki handoff: teach the difference between editing the next draft, generating from a publication, and explicitly accepting a source-backed matter fact. State the limited label/value extractor and unsupported repeated children/assets clearly.

New Word masters additionally require [source review before publication](../template-studio-word-source-review.md). Review decisions concern retained template wording and blanks, not accepted matter facts. Workspace generation continues to resolve the published snapshot, including explicit same-value links and source-backed choice groups. No new MCP tool, automatic publication or fact acceptance is added.

Studio's Field Library uses the same built-in bindings and eligible firm custom definitions. Its read-only browser endpoints expose current authoring-template usage, independently of older published snapshots. Teach explicit Word placeholders and **Fills from** mapping together; the library does not infer facts, copy values between matters, or modify publications. Endpoint permissions, response boundaries and pagination are documented in the Word source-review guide above.
