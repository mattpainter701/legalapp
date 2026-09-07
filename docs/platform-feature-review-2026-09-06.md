# Platform feature review — 2026-09-06

Baseline: `c52cb84b097b8cc545e54fe6d6aed840ab4b9765` on `origin/main`.
Scope: a source-level pass across the platform's feature families, followed by
localized fixes and regression tests. This is not exhaustive user acceptance,
live provider validation, a production load benchmark, or a security audit.

## Changes in this PR

| Area | Problem and resulting behavior | Regression evidence |
| --- | --- | --- |
| Shared navigation / Assistant | Ordinary work pages fetched conversations and documents that only the Assistant rail uses. Lists now load on Assistant entry, survive independent service failure, and ignore late results after navigation. Equivalent refreshed permission arrays no longer trigger reloads. | `AppShell.data.test.jsx` |
| Intake leads | Failed loads appeared to be an empty pipeline; stage failures were silent. The page now distinguishes errors, offers a load retry, and explains failed stage updates. | `IntakePage.test.jsx` |
| Communications | Slow pagination/filter requests could replace newer results. Filtering now starts at the first page and protects current results from stale completions. | `CommunicationsPage.test.jsx` |
| Client portal messages | Fixed-interval polling could overlap slow reads. Polls now serialize and skip hidden tabs. | `ClientPortalMatterPage.test.jsx` |
| Research workspaces | Selecting a new workspace could display the previous trail or accept its late response. Workspace data is cleared and stale completions are ignored. | `ResearchWorkspacePage.test.jsx` |
| Template Studio | Region edits used field-only undo snapshots. Undo and redo now restore fields and regions together. | `TemplateStudioEditor.test.jsx` |
| Workspace / Research MCP consent | Changing consent links could leave another application's details visible while submitting the current request ID. Each request now has isolated review state, including late decision handling. | `McpConsentNavigation.test.jsx` |
| Microsoft / Google directory sync | Sync queried users separately for every directory entry and repeatedly fetched the same tenant default. Existing users are loaded once per tenant; the default is fetched once only when creating users. | `test_user_sync_state.py`, focused sync unit tests |
| Chat attachments | Uploads were fully read before rejecting oversized files. Reads are bounded to the configured maximum plus one byte, with an additional early declared-size check. | Focused upload unit tests and `test_chat.py` |

The request savings above describe code paths, not measured production latency
or memory reductions. The changes add no runtime dependency, migration, worker,
service, or provider call.

## Coverage and remaining work

“No additional confirmed issue” means this pass did not establish a specific
defect in the inspected paths; it does not certify the whole feature.

| Feature family | Principal paths reviewed | Result / follow-up |
| --- | --- | --- |
| Authentication, navigation and mobile shell | `App.jsx`, `AppShell.jsx`, `Sidebar.jsx`, module access, release/version components | Assistant request waste fixed; existing session and route guards retained. |
| Caller intake / leads / call alerts | `IntakePage.jsx`, `IntakeDashboardPage.jsx`, `useCallFeedPolling.js` | Lead recovery fixed. Call feed already has visibility-aware polling. |
| Tasks and calendar | `TasksPage.jsx`, `CalendarPage.jsx` | Follow up with delayed-response tests for rapid filter/date changes; list loaders lack stale-request protection. |
| Matter / client / contact CRM | `MatterPortfolioPage.jsx`, `ClientDetailPage.jsx`, `ClientsPage.jsx`, `ContactsPage.jsx`, `ContactDetailPage.jsx` | Follow up on stale search/detail responses. Client detail loads activity and tasks per associated contact; batch or lazy-load after measuring representative client sizes. |
| Matter import, intake packets and workflows | `MatterWorkflowPanel.jsx`, `MatterIntakePanel.jsx`, intake/workflow services and recent #340/#342/#344 diffs | No additional confirmed issue in reviewed workflow boundaries. Provider delivery and real folder imports require separate acceptance. |
| Communications and portal | `CommunicationsPage.jsx`, `ClientPortalMatterPage.jsx` | Pagination races and overlapping polling addressed. |
| Time, expenses, invoices, payments, trust and reports | `TimeTrackingPage.jsx`, `InvoicesPage.jsx`, `InvoiceDetailPage.jsx`, `TrustAccountingPage.jsx`, `ReportsPage.jsx` | No additional confirmed issue in this pass. No payment or accounting behavior changed. |
| Specialized estate, domestic, mediation and renewal workspaces | Portfolio/detail pages and scheduler deadline paths | No confirmed portfolio regression. Scheduler estate-deadline lookups remain a batching candidate. |
| Template Studio and document automation | `TemplatesPage.jsx`, `TemplateStudioEditor.jsx`, template services | Region undo/redo fixed; published template boundaries retained. |
| Documents, revision and explorer | `DocumentRevisionPage.jsx`, `useDocumentRevision.js`, `useMatterDocumentExplorer.js`, upload routers | Revision context fetches the matter's document list to resolve one source; consider a direct authorized document lookup. Chat upload read bounded. |
| Assistant, research trails and Brief Check | `ChatPage.jsx`, `ResearchWorkspacePage.jsx`, `BriefCheckPage.jsx`, research/brief routers | Research navigation fixed. Brief Check input identity needs a separate schema-aware correction, detailed below. |
| Conflict checks | `ConflictChecksPage.jsx`, `services/conflict_check.py` | Matching contacts lead to repeated matter queries; batch under existing redaction/access rules. Review whether the linked matter should appear as its own conflict before changing legal-review semantics. |
| Firm Memory and on-prem search | Firm Memory pages/service/router, `search-node` parser/extraction/OCR, agent polling/gateway | Existing input/archive/output bounds and bounded long polling found. No additional confirmed serving defect; production search performance was not measured. |
| Microsoft, Google, Teams and cloud administration | `user_sync.py`, `OnboardingWizard.jsx`, `TeamsTabPage.jsx`, `CloudSearchAdmin.jsx`, `SmbAdminPage.jsx` | Directory sync optimized. Onboarding can reload status redundantly; Teams link failures currently look empty. Follow-up recovery UX is appropriate. |
| Administration, add-ons, profile and platform operations | `AdminPage.jsx`, `PluginsPage.jsx`, `ProfilePage.jsx`, `PlatformPage.jsx`, `PlatformInfrastructurePage.jsx` | Source pass only; no subscription, entitlement, commercial-copy, or infrastructure changes. Some optional profile statistics swallow load failures. |
| Connected assistant authorization | Both MCP authorize pages and canonical MCP docs | Request-specific consent isolation fixed. Server authorization remains authoritative. |
| Background scheduling | `services/scheduler.py` | Weekly OC portfolio checks query the latest event per matter; task reminders query the assignee per task; estate deadlines query estates/users repeatedly. Batch each independently with query-count and recipient-equivalence tests. |

## Priority follow-up: Brief Check input identity

`backend/app/routers/brief_checks.py` reads and analyzes an optional opposing
brief but deduplicates only by the primary brief's SHA. The `BriefCheck` model
and migration `139_brief_checks.py` enforce uniqueness on tenant, matter and
that primary digest. Reusing the same primary brief with a different opposing
brief can return the previous analysis.

A follow-up should persist the opposing input digest, define compatibility for
existing checks, update the uniqueness/idempotency contract in the single
migration chain, and test same-primary/different-opposing submissions plus
concurrent duplicate submissions. This PR does not modify that schema or claim
to fix this issue.

## Customer-workflow UX remediation

The follow-up compares the interface with the two first-customer profiles:
mediation with Microsoft 365, and probate/property work with Google. It does
not assume a jurisdiction, Google account edition, or that the mediation
attorney acts as counsel rather than a neutral.

Confirmed friction corrected in this PR:

- Matter sections are stored in the `tab` query parameter. Direct links,
  refresh, and browser Back restore the section; document revision returns to
  Documents. Switching matters isolates pending responses and local drafts.
- Dashboard tasks have links to their full task workspace, a scoped task-list
  link, visible failed-completion feedback, and load retry. Empty pending lists
  no longer imply that waiting or review work is complete.
- The task workspace follows the current route's matter filter. New tasks
  inherit that matter. An Edit action corrects task details without recreating
  the task or changing status/assignment approval flows. Explicitly clearing
  optional dates and notes is persisted; omitted values remain unchanged.
- Estate entries distinguish missing data from failed loads, show required
  field errors, retain drafts after save failure, and expose correction actions
  to touch and keyboard users. Clearing optional dates or values is supported.

Evidence: `MatterNavigation.test.jsx`, `MatterDetailNote.test.jsx`,
`DocumentRevisionPage.test.jsx`, `TasksPage.test.jsx`, estate regression tests,
`test_task_tracking.py`, and the mobile browser journey that returns from tasks
to Documents using Back and refresh. These use synthetic fixtures.

The initial hypothesis of a mandatory probate sequence was not confirmed:
estate creation already accepts incomplete information, and sections can be
opened independently. Mediation stages are already editable. These existing
behaviors are retained, rather than adding another workflow system.

### Mediation ownership handoff and remaining acceptance

The active `addon-module-review` worktree owns mediation changes, so this PR
does not modify those files. Read-only review found:

- Only `our_client` invitations create native client-account access; other
  roles use scoped portal invitations. A neutral need not label either
  participant as the firm's client, but the UI needs clearer role guidance.
- The generic party form initializes its role select as blank and sends null
  if unchanged, which does not use the API's non-null default. The owning task
  should require an explicit participant role and explain its invitation path.
- Party-management and invitation routes need an authorization review against
  intended matter-manager capabilities. Do not broaden recipient visibility or
  reinterpret `opposing_party` decisions to solve a labeling problem.

Customer-specific mediation participation, probate forms/deadlines, and the
exact real-estate/oil-and-gas deliverables still need customer acceptance.
This PR does not claim live Microsoft/Google validation or oil-and-gas-specific
workflow support.

## Test environment

Frontend regressions use synthetic data and delayed promises. Backend unit
regressions mock external providers; PostgreSQL integration checks run in CI.
Browser fixtures, when run, exercise the real UI with intercepted synthetic API
responses. None of these send client communications, approve real consent,
charge accounts, or validate live production-provider behavior.
