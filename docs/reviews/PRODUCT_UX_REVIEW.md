# Product & UX review — attorney's-seat pass

Reviewed on branch `claude/product-ux-code-review-tf7ake` against the launch
surface (Call Intake + Tasks + Zoom Phone) and the shared workspace shell.

The frame for this review is a working attorney, not an operator: someone who
picks up a ringing phone, types for ten minutes, gets interrupted, and comes
back. Every finding below is something that costs that person work, time, or
confidence in a deadline.

The codebase is in good shape — no `window.alert`, real confirm dialogs on
destructive actions, focus traps on the mobile drawer, `aria-current` on nav,
44px tap targets, empty states with recovery actions, httpOnly session cookies.
The findings are not "this is sloppy." They are specific places where a correct
implementation has a gap that a real user will fall into.

---

## P0 — Loses work or misstates a deadline

### 1. Call drafts cross-contaminate client records

`frontend/src/pages/IntakeDashboardPage.jsx:583`

The multi-draft tab strip lets an intake operator hold several live calls at
once. When the active draft changes, the page restores `q`, `phone`, and
`selectedStaff` from the draft — but not `selected` (the matched
contact/lead from History Matches) or `searchData`.

Those two are then read at submit time:

```js
existing_contact_id: selected?.contact_id || undefined,
existing_lead_id:    selected?.lead_id    || undefined,
assigned_to_user_id: form.task_mode === 'partner_rotation'
  ? searchData?.recommended_attorney_user_id : undefined,
```

So: search "Jane Smith", click her existing contact card, switch to draft tab 2
for a different caller, submit. Bob's call gets filed onto **Jane's contact
record** and routed to **Jane's recommended attorney**. Nothing in the UI warns
about it.

The fix is already half-built. `useCallDrafts.js:33-38` defines
`linked_history_contact_id`, `linked_history_lead_id`, `linked_history_result_id`,
`linked_history_result_type`, `linked_history_title`, `linked_history_phone`, and
`IntakeDashboardPage.jsx:708-713` writes all six on every `selectResult`. They
are persisted and then **never read back**. The write path shipped; the read path
did not.

- Restore `selected` from `form.linked_history_*` in the draft-switch effect.
- Build the submit payload from `form.linked_history_*`, not page-level `selected`.
- Clear `searchData` on draft switch, or scope the rotation recommendation to
  the draft the same way.

### 2. A task due today is painted as overdue

`frontend/src/pages/TasksPage.jsx:919`

```js
const isOverdue = task.due_date && new Date(task.due_date + 'T00:00:00') < new Date() && !isClosed
```

Midnight today is always less than *now*, so every task due today renders with
the rose overdue tint from 12:00:01 a.m. onward. The backend disagrees —
`backend/app/routers/tasks.py:332` correctly uses `Task.due_date < today` — so
the header count says "0 overdue" while rows in the Due Today section are
styled blown. `dueDateLabel` (line 78) gets it right by checking `isToday()`
first; the row styling never got the same guard.

For a deadline product this is the expensive kind of wrong. Red that fires on
days nothing is late trains attorneys to stop reading red.

### 3. There is no firm timezone, anywhere

`backend/app/routers/tasks.py:329,374,511` use `date.today()` — server local,
which in the deployed container is UTC. `TenantSettings`
(`backend/app/models/tenant.py:100`) has cache flags, expertise defaults, and
seven feature toggles, but **no timezone field**. Nothing in the product knows
what day it is at the firm.

A California firm at 5:00 p.m. Monday is already Tuesday in UTC. Every task due
Monday flips to overdue with seven hours of the business day left. Hawaii loses
ten. The frontend meanwhile computes its own "today" in browser local, so the
two halves of the product disagree about the date for a chunk of every day.

Add `TenantSettings.timezone` (IANA name, defaulted at onboarding), compute
`today` in that zone on the server, and pass it to the client so both sides
bucket deadlines identically. This is a schema change, so it wants doing before
the first customer's data exists rather than after.

### 4. Ctrl/Cmd+N throws away whatever you were typing

`frontend/src/components/AppShell.jsx:252-261`

```js
const handler = (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'n') {
    e.preventDefault()
    handleNewConversation()
  }
}
window.addEventListener('keydown', handler)
```

Registered on `window` for every authenticated page, with no check for whether
focus is in an input, textarea, or contenteditable. `handleNewConversation`
creates a conversation and `navigate`s to `/chat`.

Mid-sentence in a qualification memo, a matter note, or a time-entry narrative,
Ctrl+N navigates you away and the text is gone. It also swallows the browser's
native new-window on every screen in the product.

Guard on the event target, and scope the shortcut to the chat surface.

### 5. Session expiry discards the page and forgets where you were

`frontend/src/api.js:154-158`

```js
const redirectToLogin = () => {
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}
```

`window.location.href` is a hard navigation: React state dies, the form dies.
And no `return_to` is attached, so after signing back in the attorney lands on
their default route rather than the matter they were working.

The rest of the app already does this correctly — `ProtectedRoute`
(`App.jsx:150`) builds `?return_to=...`, and `LoginPage.jsx:52` reads and
validates it through `isSafeInternalReturnTo`. The interceptor is the one place
that drops it. Append the current path there and the round trip closes.

### 6. Nothing warns before you navigate away from unsaved work

No `beforeunload`, no router blocker, no autosave anywhere outside the intake
call drafts:

```
grep -rn "beforeunload|useBlocker|usePrompt|unsavedChanges" frontend/src  → 0 hits
```

That is what makes findings 4 and 5 costly instead of annoying. The intake
drafts hook is the pattern to copy — debounced local persistence plus a backend
upsert — and the long-form surfaces that need it most are the matter note
composer, task qualification notes, and time-entry narratives.

---

## P1 — Makes the product feel like a filing cabinet you have to already know

### 7. There is no global search

```
grep -rn "GlobalSearch|command palette|omnibox" frontend/src  → 0 hits
```

The header (`AppShell.jsx:322`) holds a page title, a "My Matters" button that
duplicates the sidebar, and an Admin shield. That's it.

The single most common thing that happens at a law firm is a person saying a
name. "This is Jane Smith calling about my case." The attorney must currently
guess which module Jane lives in — Clients? Contacts? Matters? Tasks?
Communications? — and search each one separately. Every module has its own
search box and none of them talk.

One header search that spans matters, clients, contacts, tasks, and documents,
on `/` or Cmd+K, is the highest-leverage single addition on this list.

### 8. The Contacts page has no way in

`/contacts` is a full CRUD page (`ContactsPage.jsx`, 13.8 KB) and it is **absent
from `NAV_GROUPS`** (`Sidebar.jsx:14-53`). The only paths to it:

- `MatterDetailPage.jsx:1752` — a text link inside the client picker
- `ContactDetailPage.jsx:124` — a back button, which requires already being there

An attorney told "add opposing counsel as a contact" has nowhere to click.

That matter-detail link is also a raw `<a href="/contacts">` inside a SPA, so it
triggers a full page reload and discards the matter form the attorney was
filling in. Make it a `<Link>` regardless.

### 9. "Call Intake" and "Intake" sit next to each other in the sidebar

`Sidebar.jsx:28-29`

```js
{ path: '/intake/dashboard', label: 'Call Intake', icon: PhoneCall },
{ path: '/intake',           label: 'Intake',      icon: ClipboardList },
```

Two adjacent items, near-identical labels, different pages ("Call Intake Desk"
vs "Client Intake"). Nothing distinguishes them at the point of clicking. A new
user will pick wrong roughly half the time and won't know they did.

Either name the jobs — "Live Call Desk" and "New Client Intake" — or merge them
into one Intake surface with two tabs.

### 10. Filters die on the back button

None of the workspace list pages put filter or search state in the URL:

```
useSearchParams in MatterPortfolioPage / ClientsPage / ContactsPage / InvoicesPage / TasksPage  → 0
```

`MatterPortfolioPage.jsx:344` holds `search` in `useState`. TasksPage persists
only the board/list toggle to localStorage (`:1142`) — the status, priority,
type, due-window, matter, and assignee filters are all component state.

So: filter to firm work / overdue / family law, open a task, hit Back, and every
filter is gone. Working a list of twelve overdue matters means re-filtering
twelve times. It also means no attorney can send another attorney a link to a
filtered view.

Lift filters into `useSearchParams`. It fixes bookmarking, sharing, and the back
button in one change.

### 11. Tasks cannot be searched by text

TasksPage offers status, priority, type, due window, matter, and assignee
filters — and no text box. "Where's that task about the Henderson deposition?"
has no answer short of scrolling. Every other list surface in the product has a
search input; the one that will hold the most rows doesn't.

### 12. Sidebar navigation can't be opened in a new tab

`Sidebar.jsx:210-236` and `AppShell.jsx:391` render nav as `<button onClick={navigate}>`.
No middle-click, no Cmd+click, no "copy link address," no right-click menu.

Attorneys work across many tabs — matter in one, calendar in another, tasks in a
third. `MatterPortfolioPage.jsx:298` already uses a real `<Link>` for table rows
and gets this right; the nav should match.

---

## P2 — Polish and discoverability

### 13. Keyboard shortcuts exist but are effectively secret

The intake desk binds Alt+Shift+N for a new draft and Alt+1…9 to switch draft
tabs (`IntakeDashboardPage.jsx:547-563`). The only hint anywhere in the product
is a `title` attribute on one button (`DraftTabStrip.jsx:122`). No shortcuts
panel, no `?` overlay, no mention in onboarding.

The same handler also has no editable-target guard, so Alt+1 while typing in a
note swaps the entire form underneath the cursor. Lower severity than the Ctrl+N
issue because drafts autosave, but it's the same missing guard.

### 14. No in-app help at all

```
grep -rn "Help|help center|/docs|Learn more|tour" frontend/src/pages frontend/src/components  → 3 hits, all on marketing pages
```

Nothing in the authenticated product. The intake form asks an attorney to choose
between "Partner rotation," "Specific staff," and "Log only" with no explanation
of what any of them do to the record. `OnboardingWizard` exists but is admin-only
and module-gated, so a newly invited associate sees none of it.

A help link in the header and hover definitions on the routing choices would
cover most of it.

### 15. Brand identity is now consistent

The product name is **LawHand** across the UI, documentation, operational
configuration, and service metadata. Retired brand collateral must not be
reintroduced.

---

## Suggested order

1. Draft cross-contamination (#1) — it files calls onto the wrong client and the
   fix is finishing a read path that's already designed.
2. Deadline correctness (#2, #3) — same-day false overdue, then tenant timezone
   before customer data exists.
3. Work preservation (#4, #5, #6) — Ctrl+N guard and `return_to` are small;
   unsaved-change guards follow.
4. Global search (#7) and Contacts nav (#8) — largest perceived-quality gain per
   hour spent.
5. URL-backed filters (#10) and task text search (#11).
6. Everything else.
