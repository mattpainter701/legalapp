# Product & UX review — receptionist's-seat pass

Reviewed on branch `claude/product-ux-code-review-tf7ake`, following the call
handoff end to end: phone rings → search → log → lead → task → assignment →
someone picks it up (or doesn't).

The frame is a receptionist at a firm that either has Zoom Phone or has nothing,
and who logs calls by hand. They are the first human a distressed caller
reaches. Their whole job is a promise — *"someone will call you back today"* —
and the product's job is to make that promise true.

The failure mode here is different from the attorney's (losing work) and the
paralegal's (volume). The receptionist's failure mode is **making a promise and
never learning whether it was kept.**

---

## P0 — The handoff goes dark

### 1. Follow-through status is unreachable without Zoom

This is the finding. Everything else is smaller.

The product **does** track whether a handoff was acted on. `CallFacts.jsx:16-51`
renders exactly the right things:

```jsx
if (caller.task_customer_contacted_at) { ... }
if (caller.task_viewed_at) { ... }
...
caller.task_viewed_at ? new Date(caller.task_viewed_at).toLocaleString() : 'Not yet',
```

Viewed at. Contacted at. Contact method. That is precisely what the receptionist
needs to answer *"did anyone call Mrs. Alvarez back?"*

But `CallFacts` renders only under one condition
(`IntakeDashboardPage.jsx:1027`):

```jsx
{selectedRecentCaller && <CallFacts caller={selectedRecentCaller} />}
```

and `selectedRecentCaller` has exactly two entry points, both fed by the same
Zoom source:

- `onSelect={selectRecentCaller}` on `<CallFeed>` (`:1020`) — the Zoom-populated
  recent-callers feed
- `selectCallById` from `<NewCallToasts>` (`:1389`) — driven by `newCallIds`
  from the same feed poll

A receptionist logging calls manually has an empty feed, permanently. So the one
view in the product that closes the accountability loop is **structurally
unreachable for the persona that most needs it.**

They log the call, hand it off, and the trail ends. If the caller phones back
angry two days later, the receptionist has no way to check what happened before
picking up.

Surface viewed/contacted on the manual path too: a "handoffs I logged" list with
status per row, reachable without a Zoom call record behind it.

### 2. A non-admin sees a dead third of the screen with no explanation

`IntakeDashboardPage.jsx:991`

```jsx
{user?.role === 'admin' && zoomStatus && !zoomConnected && (
  ... "Connect Zoom Phone to populate the live call feed" ...
)}
```

The banner explaining why the call feed is empty is **admin-gated**. A
receptionist is not an admin.

So the receptionist's screen has a 340px left rail
(`xl:grid-cols-[340px_minmax(0,1fr)]`, `:1014`) that permanently reads
**"No calls yet."** (`CallFeed.jsx:145`) with no explanation, no context, and no
indication whether this is expected or broken. Every day, forever.

Show a non-admin version of the notice — "Live call feed requires Zoom Phone,
which isn't connected. Ask your administrator." — or collapse the rail entirely
when Zoom is absent so the layout gives that space to the work they actually do.

Right now the product's default appearance for a manual-logging firm is
*broken-looking*, and the person staring at it has no standing to fix it and no
information to raise it.

### 3. "No calls yet" is shown when calls exist but are filtered out

`CallFeed.jsx:142-146`

```jsx
) : visible.length === 0 ? (
  <div ...>No calls yet.</div>
```

`visible` is the post-filter list. Toggle off a filter chip, forget about it, and
the feed says "No calls yet" while calls are sitting there hidden. There *is* a
`hiddenCount` display above (`:132`), but the empty message contradicts it.

When `hiddenCount > 0` the message should say so and offer to clear the filters.
A receptionist who believes no calls came in is a receptionist not returning
calls.

---

## P1 — The task flow itself

### 4. High-priority tasks sort below low-priority ones

`backend/app/routers/tasks.py:438`

```python
stmt = stmt.order_by(Task.due_date.nulls_last(), Task.priority.desc())
```

`Task.priority` is a `String` column (`app/models/task.py:146`), so `.desc()`
sorts **lexically**, not by severity:

```
urgent, medium, low, high
```

`high` sorts dead last — below `low`. Within any given due date, the second-most
urgent work in the firm is at the bottom of the list.

The correct implementation already exists 178 lines away, in the board endpoint
(`:616`):

```python
priority_bucket = case(
    (Task.priority == "urgent", 0),
    (Task.priority == "high", 1),
    (Task.priority == "medium", 2),
    else_=3,
)
```

The list view never got it. So Board and List present the same tasks in
different, and in List's case wrong, order. Reuse `priority_bucket` in
`list_tasks`.

This compounds badly with the `limit: 200` in finding #9 of the scale review —
high-priority tasks get pushed off the fetched page entirely by low-priority
ones.

### 5. The Partner Log records the promise, not the outcome

`IntakeDashboardPage.jsx:437-450` renders assignee, method, practice area,
timestamp, and who assigned it. Its own subtitle says it is "captured for finance
and accountability."

It is a record of **assignment events**. Nothing in it says whether the assignee
opened the task, called the client back, or closed it. Combined with #1, the
receptionist's entire visibility into their own work product is a list of things
they handed to people, with no column for what happened next.

The data exists — `task_viewed_at` and `task_customer_contacted_at` are already
on the caller payload. Add them as columns here and the panel becomes what its
subtitle claims.

### 6. Partner Log holds 25 entries with no pagination

`IntakeDashboardPage.jsx:387` — `getPartnerLog({ limit: 25 })`, no offset, no
load-more, no total.

At a firm taking 40 calls a day, the log covers less than one morning. There is a
CSV export, which is the honest workaround, but "open a spreadsheet" is not an
answer to "what did I hand off this morning."

### 7. Keyboard shortcuts fire while typing

`IntakeDashboardPage.jsx:547-563` binds Alt+Shift+N (new draft) and Alt+1…9
(switch draft tab) on `window`, with no check for whether focus is in an input.

The receptionist is typing into the Purpose textarea with a caller on the line.
Alt+2 swaps the entire form underneath them. Drafts autosave so nothing is
destroyed, but mid-call disorientation is its own cost — and it is the same
missing editable-target guard as the Ctrl+N bug in the attorney review.

Add the guard, and put the shortcuts somewhere discoverable. Right now the only
hint in the product is a `title` attribute on one button
(`DraftTabStrip.jsx:122`).

---

## P2

### 8. The manual path is second-class throughout

Taken together, #1, #2, and #5 describe a product that treats manual logging as
a degraded mode of the Zoom flow rather than a first-class path. The call
capture form itself is good — multi-draft, autosaving, receipt trail, sensible
routing. But everything *around* it — the feed, the accountability view, the
setup guidance — assumes Zoom.

The README positions this as "Manual and Zoom Phone intake." For that to be true
the manual path needs its own feed (calls I logged today), its own follow-through
view, and its own empty states.

---

## Suggested order

1. **#2 non-admin Zoom notice** — smallest fix here, and it stops the product
   looking broken to the person who uses it most.
2. **#1 follow-through without Zoom** — the accountability view already exists;
   it needs a second entry point.
3. **#4 priority sort** — one-line reuse of an existing helper, and it is
   currently mis-ordering every firm's task list.
4. **#3 filtered empty state** and **#7 shortcut guard** — both small.
5. **#5 / #6 Partner Log** — add outcome columns, then pagination.
