# Legal Work Board Plan

**Date:** 2026-08-04

**Status:** Implemented on the feature branch; deployment and firm pilot pending

**Scope:** Production-ready product enhancement; database migration, API, UI,
accessibility, tests, and operating guidance are included

**Branch:** `agent/legal-task-board-plan`

**Primary surface:** Existing `/tasks` workspace

**Decision:** Add a law-firm-oriented continuous-flow board as an optional view
of the existing task system. Keep the current deadline list, use a small fixed
workflow for the first release, and avoid software-team language or required
Scrum ceremonies.

## Executive Summary

The product should use a practical **Scrumban-shaped workflow**, presented to
customers simply as a **Work Board**:

```text
To Do -> In Progress -> Waiting -> Review -> Done
```

This is a better fit for legal work than pure Scrum:

- Legal work arrives continuously from clients, courts, opposing counsel,
  intake, and internal review; it does not naturally wait for a sprint boundary.
- Deadlines remain authoritative even when a card moves between workflow
  columns.
- Attorneys and staff need explicit ownership, handoffs, waiting reasons,
  review responsibility, and an audit trail more than estimates or story points.
- A list grouped by overdue/today/upcoming is still the safest primary view for
  deadline triage, while a board makes workload and bottlenecks visible.
- Lightweight work-in-progress guidance can help managers without preventing a
  user from starting urgent or court-driven work.

The first release should therefore add a `Board | List` view switch, default to
`My Work`, offer an authorized `Firm Work` scope, and reuse the existing task,
matter, contact, assignee, priority, deadline, reminder, intake, and closure
behavior.

## Implementation Result

The plan is now implemented as a full enhancement rather than an isolated MVP:

- The existing `/tasks` workspace offers persistent Board/List preferences,
  My Work/Firm Work scope, multi-dimensional filters, risk counters, per-column
  pagination, responsive mobile navigation, and the original deadline list.
- Board cards are intentionally privacy-minimized. Full descriptions, customer
  details, and the internal history are fetched only when the detail drawer is
  opened.
- Drag-and-drop and keyboard-accessible Move actions share one transition API.
  Waiting requires a reason and supports a follow-up date; Review supports a
  reviewer; completion/cancellation records closure context; reopening clears
  stale closure metadata.
- Task versions provide optimistic concurrency control. A conflict rolls the
  card back, refreshes the board, and asks the user to retry against current
  data rather than overwriting another staff member's work.
- An append-only, tenant-isolated `task_events` timeline records creation,
  assignment, reassignment, workflow changes, contact logging, and material
  edits. Customer communication history remains separate.
- Intake dashboard, mediation, email-agent, and task CRUD paths all emit the
  same workflow/audit semantics, so the board cannot drift from tasks created
  elsewhere in the product.
- Tenant administrators can enable or disable the Work Board independently of
  the deadline list. Content-free structured logs cover view selection, board
  load latency/card counts, oldest Waiting/Review age, transition success,
  validation rejection, and version conflict for controlled rollout.

## Product Terminology

Use language a law office already understands:

| Internal/product concept | Customer-facing label |
|---|---|
| Kanban or Scrumban | Work Board |
| Backlog | To Do |
| Work in progress | In Progress |
| Blocked | Waiting |
| QA or approval gate | Review |
| Sprint | Do not introduce in the initial release |
| Story points | Do not introduce |
| Swimlane | Group by, when grouping is added later |

The Tasks navigation item and page heading remain `Tasks & Deadlines`. "Board"
is a view, not a second task product.

## Why This Fits the Existing Application

The existing implementation already provides the durable core:

- `Task` stores title, description, type, priority, due date/time, matter,
  contact, assignee, creator, lifecycle status, completion, read/contact
  receipts, closure details, source, and external reference.
- `/api/tasks` supports tenant-scoped list, create, fetch, update, delete,
  overdue, upcoming, reminder, intake qualification, view receipt, and customer
  contact operations.
- `TasksPage.jsx` already presents overdue, today, upcoming, no-date, and closed
  groups and includes intake, reassignment, contact, completion/cancellation,
  reminder, and matter-opening actions.
- Task updates already use a row lock, validate tenant-owned references, update
  calendar notifications, and record selected contact-linked lifecycle events.
- Tasks already appear in calendar and first-customer intake workflows.

The board should extend these contracts rather than create parallel "cards" or
a second source of task truth.

## Current Gaps the Board Must Address

1. The four current task states (`pending`, `in_progress`, `completed`, and
   `cancelled`) cannot express work waiting on another party or work awaiting
   attorney review.
2. The list is optimized for due-date triage but does not expose flow or
   bottlenecks.
3. Status changes do not create a complete task-local history for standalone or
   matter-only tasks. `CommunicationLog` intentionally covers only
   contact-linked customer history.
4. The list endpoint returns bare task records, so a board would otherwise need
   extra requests to show matter and assignee labels.
5. Concurrent drag/move operations have no optimistic-concurrency contract.
6. `TasksPage.jsx` is already large; embedding an entire board in it would make
   the page harder to test and maintain.
7. The current page-load read-receipt behavior should not mark a large firm board
   as "seen" merely because its cards were fetched. A board card and an opened
   task are different levels of attention.

## Initial User Experience

### Page shell

Keep the existing page route and page actions. Add the following controls below
the heading:

1. `Board | List` segmented view switch.
2. `My Work | Firm Work` scope switch. `My Work` is the default.
3. Filters for assignee, matter, priority, task type, and due window.
4. A compact risk summary: overdue, due today, unassigned, and waiting past its
   follow-up date.

The selected view may initially be stored per user in local storage. A server
preference can follow when preferences have a shared home in the data model.

### Board columns

| Column | Stored status | Meaning | Transition behavior |
|---|---|---|---|
| To Do | `pending` | Accepted work that has not started | Default for new tasks |
| In Progress | `in_progress` | Someone is actively working it | Preserves assignee and deadline |
| Waiting | `waiting` | Work is waiting on a client, court, vendor, opposing counsel, or internal dependency | Requires a short waiting reason; may add a follow-up date |
| Review | `review` | Work product or a decision is ready for review | May select a reviewer; otherwise the assignee remains responsible |
| Done | `completed` | Work is complete | Uses the existing close flow and completion metadata |

`cancelled` is a terminal lifecycle state, not a normal work column. Cancellation
remains available from the task menu, requires a reason, and is visible through
the Closed/Cancelled filter.

Do not add an initial "Backlog" or "Later" column. Legal deadlines should not be
made less visible by placing them in an indefinite queue. Future firms that need
an ideas backlog can use an explicitly configured workflow after custom columns
are proven necessary.

### Card design

Every card should show, in this order:

- task title;
- matter name/number when linked;
- due date/time with overdue and due-today treatment;
- assignee avatar/initials or `Unassigned`;
- priority and task type;
- waiting follow-up or reviewer when relevant;
- compact receipt/contact indicators when relevant to intake work.

Descriptions, customer details, and privileged work notes stay out of the
collapsed card. They appear in the task detail drawer after the user opens the
card. This reduces accidental exposure in shared-screen board views.

### Ordering and movement

Dragging a card changes only its workflow status. It never changes its due date,
assignee, matter, or priority.

Within each column, use a risk-first deterministic order:

1. overdue tasks;
2. due today;
3. urgent, then high priority;
4. next due date/time;
5. most recently status-changed.

Do not support arbitrary same-column ranking in the initial release. A manually
ranked urgent filing can otherwise be buried, and a persistent fractional-rank
system adds migration and concurrency complexity without proving customer
value. Saved custom sorting can be evaluated later.

### Move interactions

- Desktop users may drag a card to another column.
- Every card also has a keyboard-accessible `Move to...` action.
- Moving to Waiting opens a small form for waiting reason and optional follow-up
  date.
- Moving to Review offers a reviewer picker but permits self-review for solo
  firms.
- Moving to Done uses the existing completion dialog and optional completion
  reason.
- Moving a completed task back to an open column is an explicit `Reopen` action
  and clears the prior closure fields consistently with existing behavior.
- A concurrent update returns a conflict, restores the card to the server state,
  and tells the user who/what changed when that data is available.

### Responsive behavior

Do not force a five-column miniature board onto a phone.

- At desktop widths, show horizontally scrollable columns with sticky headers.
- At tablet widths, show two or three columns at a time.
- On small screens, show one selected status at a time with a status picker and
  `Move to...`; drag and drop is not required.
- Preserve the existing list as a first-class mobile option.

### Deep links and task detail

`/tasks/{taskId}` must continue to work. In Board view, load the task even when
it is outside the current filters, open its detail drawer, and explain that it
is outside the active view rather than silently moving it into a column.

Opening the task detail, not merely fetching/rendering a firm board card, should
record the assignee's `viewed_at` receipt. This semantic change must be reflected
in the intake documentation and tests.

## Scope Views and Permissions

### My Work

- Assigned to the current user.
- Optionally includes unassigned tasks created by the current user through a
  deliberate filter, not by default.
- Available to every user with the Tasks module.

### Firm Work

- Uses the application's real task/matter visibility rules; the scope switch is
  never an authorization boundary by itself.
- If matter assignments become ethical-wall or ACL boundaries, both the list and
  board queries must enforce the same server-side policy before rollout.
- Reassignment, cancellation, and firm-wide workload actions continue to follow
  the existing role policy. The board must not broaden mutation rights.

### Unassigned work

Show `Unassigned` prominently in Firm Work and as a quick filter/count. Do not
create a separate workflow status for it: assignment and workflow stage are
orthogonal facts.

## Data Model

### Extend `tasks`

Add:

| Field | Type | Purpose |
|---|---|---|
| `status_changed_at` | timezone-aware datetime, non-null | Flow age and deterministic card ordering; backfill from `updated_at` |
| `waiting_reason` | text, nullable | Required while transitioning into `waiting`; cleared when leaving Waiting unless retained in history |
| `waiting_follow_up_date` | date, nullable | Optional tickler date while waiting; does not replace the legal due date |
| `reviewer_user_id` | tenant user FK, nullable | Explicit reviewer when different from the assignee |
| `version` | integer, non-null, default `1` | Optimistic concurrency for moves and edits |

Extend the allowed status values to:

```text
pending | in_progress | waiting | review | completed | cancelled
```

The database currently stores status as a string rather than an enum, so the
migration does not need an enum rewrite. Add or update indexes for common board
queries, including tenant/status/assignee and tenant/status/due date.

### Add `task_events`

Create a tenant-scoped append-only history table:

| Field | Purpose |
|---|---|
| `id`, `tenant_id`, `task_id` | Identity and tenant boundary |
| `event_type` | `created`, `status_changed`, `assigned`, `reassigned`, `due_changed`, `priority_changed`, `contacted`, `completed`, `cancelled`, `reopened` |
| `actor_user_id` | Who caused the change |
| `from_status`, `to_status` | Explicit transition when applicable |
| `note` | Waiting, completion, cancellation, or assignment context |
| `metadata_json` | Allowlisted structured facts such as previous/new assignee or due date; never arbitrary model output |
| `created_at` | Immutable event time |

This is the internal task audit/history. Continue writing appropriate customer
events to `CommunicationLog` for contact-linked tasks; do not overload the
customer history with every board movement.

Apply strict tenant RLS and explicit tenant predicates using the same standards
as `tasks`. Deleting a task may cascade its task events only if hard deletion
remains an authorized product behavior; a later retention policy may replace
hard deletion with archival.

### Invariants

- `waiting_reason` is required for a transition into `waiting`.
- `reviewer_user_id`, when supplied, must identify an active user in the same
  tenant.
- `completed_at` is non-null only for `completed`.
- `closed_by_user_id` is set for completion/cancellation and cleared on reopen.
- Changing status increments `version` and updates `status_changed_at`.
- All workflow and editorial mutations increment `version`, so a stale board
  move cannot overwrite a simultaneous reassignment or deadline change. A
  read-receipt write does not invalidate an otherwise current work card.
- A board transition and its `task_events` row commit in one transaction.
- Calendar/event sync treats `waiting` and `review` as open statuses.

## API Design

### Board query

Add:

```http
GET /api/tasks/board
```

Suggested query parameters:

```text
scope=mine|firm
assigned_to_user_id=
matter_id=
priority=
task_type=
due_window=overdue|today|7_days|30_days|none
include_completed_days=14
```

Return a board-shaped response with server-derived counts and display labels:

```json
{
  "columns": [
    {
      "status": "pending",
      "label": "To Do",
      "total": 12,
      "items": [
        {
          "id": "...",
          "title": "Review discovery responses",
          "status": "pending",
          "version": 4,
          "priority": "high",
          "due_date": "2026-08-07",
          "matter": {"id": "...", "label": "Smith v. Jones"},
          "assignee": {"id": "...", "label": "Pat Paralegal"}
        }
      ],
      "next_cursor": null
    }
  ],
  "risk_counts": {
    "overdue": 2,
    "due_today": 3,
    "unassigned": 1,
    "waiting_follow_up_due": 1
  }
}
```

Join the labels in the board query to avoid N+1 client requests. Keep the detail
payload separate so collapsed cards do not receive unnecessary privileged text.
Cap and cursor-page each column independently; do not rely on the current
`limit=200` page fetch as a complete firm board.

### Atomic transition

Add:

```http
POST /api/tasks/{task_id}/transition
```

Example body:

```json
{
  "to_status": "waiting",
  "expected_version": 4,
  "reason": "Waiting for executed medical authorization",
  "waiting_follow_up_date": "2026-08-11",
  "reviewer_user_id": null
}
```

The endpoint must:

1. set tenant context and load the task with a row lock;
2. enforce visibility and mutation permissions;
3. compare `expected_version` and return `409` with current safe task-card state
   when stale;
4. validate the transition-specific fields and tenant-owned references;
5. update status and closure/waiting/reviewer fields through one shared domain
   service;
6. append a `task_events` row in the same transaction;
7. commit and then invoke existing best-effort notification/calendar behavior;
8. return the updated card and new version.

The existing `PATCH /api/tasks/{id}` remains compatible. Any PATCH that changes
status must call the same transition service so notifications, audit, closure
state, and invariants cannot diverge.

### Task history

Add:

```http
GET /api/tasks/{task_id}/events
```

Return a cursor-paginated, permission-checked timeline for the task detail
drawer. Use server-resolved actor labels and allowlisted metadata.

### Existing query changes

- Extend status validation and filters for `waiting` and `review`.
- Ensure overdue/upcoming queries include all open states and exclude only
  `completed` and `cancelled`.
- Add a `scope=mine` or explicit `assigned_to=current_user` server contract
  rather than trusting a client-provided user id for My Work semantics.
- Return `version` on normal task responses so list edits and board moves share
  concurrency behavior.

## Frontend Architecture

Refactor the Tasks surface into focused components while preserving its route:

```text
frontend/src/pages/TasksPage.jsx
frontend/src/components/tasks/TaskWorkspaceToolbar.jsx
frontend/src/components/tasks/TaskListView.jsx
frontend/src/components/tasks/TaskBoard.jsx
frontend/src/components/tasks/TaskBoardColumn.jsx
frontend/src/components/tasks/TaskCard.jsx
frontend/src/components/tasks/TaskDetailDrawer.jsx
frontend/src/components/tasks/TaskTransitionDialog.jsx
frontend/src/components/tasks/taskPresentation.js
```

Keep the existing specialized intake actions, but expose them through the detail
drawer/card menu rather than duplicating their modals in each view.

Use an accessible drag/drop library with pointer and keyboard sensors (for
example, `@dnd-kit/core`). Dragging is an enhancement over the always-available
`Move to...` control. Announce pickup, destination, success, failure, and stale
conflicts through an ARIA live region.

Use optimistic card movement with a pending state. On validation failure or
`409`, restore the server-provided card, keep focus on the originating control,
and show a specific inline message. Disable duplicate moves while one transition
for that card is pending.

## Implementation Slices

### Slice 0: Contract and regression baseline

- Document the status state machine in shared backend constants.
- Add regression tests around existing pending/in-progress/completed/cancelled
  transitions, closure fields, calendar behavior, intake actions, and tenant
  reference checks.
- Decide and codify the current task/matter authorization rule; do not mistake
  a UI scope filter for authorization.
- Change read receipts to task-open/explicit-view semantics before exposing a
  firm board.

**Exit:** Existing list behavior is covered and the transition contract is
agreed before migration/UI work.

### Slice 1: Data and transition service

- Add migration for task fields, indexes, `task_events`, foreign keys, and RLS.
- Update models and schemas.
- Extract status-change logic from `tasks.py` into a transaction-friendly task
  transition service.
- Backfill `status_changed_at` and initial event history only where facts can be
  stated truthfully; do not invent historical transitions.
- Add atomic transition and event-list endpoints.
- Route PATCH status updates through the shared service.
- Update calendar/notification open-state handling.

**Exit:** API tests prove all transitions, conflicts, audit writes, and tenant
boundaries without the board UI.

### Slice 2: Board read model and filters

- Add the joined board query and response schemas.
- Add My Work/Firm Work scope, filter parity, risk counts, recent-Done window,
  and independent column pagination.
- Verify query plans against realistic firm volume and add indexes based on
  measured plans.

**Exit:** The endpoint returns complete counts and bounded card pages without
N+1 queries or cross-tenant leakage.

### Slice 3: Board UI

- Extract existing list/presentation components without changing list behavior.
- Add Board/List and My Work/Firm Work controls.
- Build desktop columns, mobile single-column mode, cards, skeleton/error/empty
  states, and detail drawer.
- Add pointer drag, keyboard drag, and `Move to...` actions.
- Reuse existing completion, cancellation, reassignment, reminder, contact, and
  intake flows.
- Preserve `/tasks/{taskId}` deep links.

**Exit:** A user can move a task through the complete workflow with mouse,
keyboard, and mobile controls while due dates and ownership remain intact.

### Slice 4: Controlled rollout and observability

- Put Board view behind `enable_task_board` while List remains available.
- Add structured events for board load, transition success/failure/conflict,
  waiting age, review age, and view selection. Do not include task titles,
  descriptions, client names, or matter names in analytics.
- Pilot with one small firm and one larger multi-role firm.
- Measure adoption, transition conflicts, tasks stuck in Waiting/Review, overdue
  visibility, and List fallback usage.
- Update `TASKS.md`, `CHANGELOG.md`, architecture/task documentation, and the
  first-customer runbook when implementation lands.

**Exit:** The feature can be enabled per tenant, monitored without privileged
content, and disabled without losing task data.

### Later slices, only after usage validates them

- Configurable WIP warning thresholds for In Progress and Review; warnings, not
  hard stops.
- Saved views and grouping by assignee, matter, or practice area.
- Matter-type workflow templates that create dependent tasks from matter dates
  or stage changes.
- Subtasks/checklists and task dependencies.
- Recurring tasks and role-based assignment.
- Custom columns mapped to explicit open/closed lifecycle semantics.
- Cycle-time and bottleneck reports derived from `task_events`.
- Optional weekly focus periods; never required sprints.

## Non-Goals for the Initial Release

- Replacing matter stages with task columns.
- A generic custom-board builder.
- Story points, velocity, burndown charts, sprint planning, or Scrum roles.
- Hard WIP limits that can block urgent legal work.
- Automatic legal deadline calculation.
- Changing a due date, assignment, or priority as a side effect of dragging.
- Cross-matter bulk transitions.
- Client-visible boards.
- Workflow automation or dependent-task generation.
- Arbitrary card ranking within a column.

## Acceptance Criteria

### Workflow and correctness

- A new task appears in To Do and in the existing deadline list.
- A user can move an authorized task among To Do, In Progress, Waiting, Review,
  and Done.
- Waiting requires a reason; Review can name an active same-tenant reviewer.
- Cancellation remains a reasoned terminal action outside the normal board.
- Moving a card never changes due date, due time, priority, matter, contact, or
  assignee unless a separate explicit edit does so.
- Reopening clears completion/closure state according to one shared rule.
- Every transition creates exactly one tenant-scoped task event in the same
  transaction.
- Calendar and reminder behavior still treats Waiting and Review as open.

### Concurrency and failure

- Two users moving the same version cannot silently overwrite one another.
- A stale move returns `409`, restores current state, and offers a clear retry.
- Notification or calendar provider failure does not roll back the durable task
  transition, and the UI does not claim that an external notification succeeded.
- Failed board queries and failed transitions have retryable, non-destructive
  error states.

### Security and privacy

- Board, transition, and event endpoints enforce tenant isolation and existing
  task/matter access rules server-side.
- Cross-tenant assignee, reviewer, matter, and contact ids are rejected without
  revealing foreign-record existence.
- Collapsed board cards do not expose descriptions or customer contact details.
- Analytics contain ids/categories and timing only, not privileged content.
- Viewing a firm board does not mark every fetched task as seen.

### Accessibility and responsive behavior

- All board operations are possible without drag and drop.
- Keyboard drag has announcements and a visible focus target.
- Dialogs/drawers trap and restore focus consistently with existing task modals.
- Columns, cards, counts, overdue states, and pending moves have semantic labels.
- The board has no serious automated accessibility violations.
- Mobile users can select a status and move cards without horizontal precision
  dragging.

## Test Plan

### Backend

- Migration upgrade/downgrade and RLS policy coverage.
- Schema validation for all statuses and transition-specific fields.
- Valid and invalid transition matrix.
- Waiting reason, follow-up date, reviewer tenant/active checks.
- Completion, cancellation, reopen, and closure metadata.
- Atomic task event creation and rollback behavior.
- Optimistic-concurrency success and `409` conflict paths.
- My Work versus Firm Work query semantics.
- Per-column pagination and exact aggregate counts.
- Deadline ordering and Waiting/Review inclusion in overdue/upcoming.
- Calendar and notification regression tests.
- Contact communication history remains correct and separate from task history.
- Cross-tenant task, matter, contact, assignee, reviewer, and event access tests.

### Frontend unit/integration

- Board/List and scope switches.
- Card rendering without sensitive description content.
- Filters, counts, empty columns, pagination, and recent-Done window.
- Pointer and keyboard transition success.
- `Move to...` forms for Waiting, Review, Done, Cancel, and Reopen.
- Optimistic rollback on validation, network, provider, and conflict errors.
- Focus restoration and live-region announcements.
- Deep-link task loading outside active filters.
- Read receipt only on detail open.
- Existing list and specialized intake actions remain functional.
- Desktop, tablet, and mobile layouts plus automated accessibility checks.

### End to end

1. Reception creates and assigns a caller follow-up.
2. Assignee opens My Work, sees it in To Do, and opens the detail.
3. Assignee moves it to In Progress and logs customer contact.
4. Assignee moves it to Waiting with a reason/follow-up date.
5. Assignee sends it to Review with a reviewer.
6. Reviewer completes it with a note.
7. Both list/calendar and task history reflect the same facts.
8. A second browser attempts a stale move and receives a safe conflict.
9. Repeat the core flow in mobile mode using `Move to...` rather than drag.

## Rollout Gates

1. No unresolved cross-tenant or authorization failures.
2. Existing Tasks list and first-customer intake E2E remain green.
3. Board query meets an agreed response target at representative task volume.
4. Transition conflicts and external notification failures are truthfully shown.
5. Accessibility and mobile acceptance criteria pass.
6. Pilot firms confirm that the five labels match their vocabulary.
7. List view remains available as an immediate fallback.

## Product Validation Notes

Current legal-practice products support the underlying direction without
requiring a software-development Scrum model:

- [Smokeball Tasks](https://support.smokeball.com/hc/en-us/articles/5859270337815-Tasks)
  emphasizes staff assignment, due dates, accountability, comments, attachments,
  and task history.
- [Smokeball setup guidance](https://support.smokeball.com/hc/en-us/articles/13649307572887-Part-Four-Set-up-Tasks-Workflows-and-Matter-Stages)
  separates tasks, repeatable workflows, and matter stages and highlights time
  stuck in a stage.
- [Filevine terminology](https://support.filevine.com/hc/en-us/articles/8691048633115-Common-Terms)
  separates task lists, project phases, and phase-triggered taskflows.
- [Filevine Taskflow](https://support.filevine.com/hc/en-us/articles/360005080892-Taskflow)
  shows the later opportunity for role-assigned, dependent task sequences
  triggered by matter/project phases.
- [Smokeball civil-litigation workflows](https://support.smokeball.com/hc/en-us/articles/5887455198103-Civil-Litigation-Workflows)
  demonstrate why legal tasks need due-date dependencies and matter-specific
  templates beyond a visual board.

These references support a phased product: first make current work visible and
movable, then add repeatable matter workflows after the board's vocabulary and
usage are validated.

## Recommended Delivery Sequence

Implement Slices 0 through 4 as separate reviewable commits or pull requests.
Do not combine custom workflow templates with the first board release. The first
customer test should answer three questions before scope expands:

1. Do users understand and consistently use Waiting and Review?
2. Do users primarily organize by assignee, matter, or practice area?
3. Does risk-first automatic ordering work, or do firms truly need manual card
   rank?

Those answers determine whether the next investment should be saved views,
matter workflow templates, custom columns, or manual ordering.
