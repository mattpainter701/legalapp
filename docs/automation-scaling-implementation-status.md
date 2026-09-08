# Automation scaling implementation

The owner requested completion of W1–W5. Individual merged PRs are delivery
checkpoints; they do not close the overall implementation.

| Workstream | Implemented | Remaining |
| --- | --- | --- |
| W1 | Eight lifecycle events, transactional source capture, bounded due scans, immutable dispatch evidence, review-only worker integration and rule editor. Merged PR #382, migration 166. | Final cross-channel validation. |
| W2 | Exact artifact requirements/decisions/delivery receipts, assigned staged or attorney-only review, revision supersession, reviewed email attachments. Merged PR #381, migration 165. | Validate as part of the final cross-channel scenario. |
| W3 | Native and Clio/Tabs3 history synthesis, immutable evidence, existing approval gates, rejection suppression, drift amendments, mapped CSV import and onboarding integration. Merged PR #384, migration 167. | Final cross-channel validation. |
| W4 | Bounded capability plans, encrypted payloads, checkpoints, input/review continuation, handler registry, firm/matter run views and cloud-write recovery. Merged PR #386, migration 168. | Final cross-channel validation. |
| W5 | Broad BYO-harness channel contract; per-grant call and per-result byte limits (PR #378); firm activity/setup and aggregate read budgets (PR #387); named noninteractive service identities, hash-bound completed-run plans, fixed schedules, immutable occurrence evidence, and legal-work approval gates (migration 169, pending review). | PostgreSQL/recovery rehearsal, production TLS interoperability, and final cross-channel scenario. |

Workspace access uses `mcp.getlawhand.com`; research uses
`research.getlawhand.com`. Customer harness inference and separately metered
LawHand services remain distinct. A harness does not bypass platform AI charges,
tenant policy, human legal review, or deterministic execution.
