# Scale review — 1 matter to 10,000

Reviewed on branch `claude/product-ux-code-review-tf7ake`, across the three
personas covered in `PRODUCT_UX_REVIEW.md` (attorney), `PARALEGAL_UX_REVIEW.md`
(paralegal), and `RECEPTIONIST_UX_REVIEW.md` (receptionist).

The question is what changes as a tenant goes from one matter to ten thousand.
The short answer: **the backend was largely built for scale and the frontend was
not.** Most list endpoints paginate correctly, use `selectinload` against N+1,
and roll up aggregates in one query. Then the client requests page 1, never asks
for page 2, and filters what it got in JavaScript.

The result is a product that is often correct at demo scale but either silently
wrong or impossible to browse completely once a list crosses 100-200 records.

---

## The shape of the problem

Nothing here degrades loudly. There is no error, no spinner that never stops, no
"too many results" warning. Several surfaces below simply show a prefix of the
truth and present it as the whole. Clients and contacts are a narrower failure:
the UI preserves the API total and server-side search, but offers no way to page
past the first 100 matching records.

| Surface | Fetch | Hard ceiling | At 10,000 matters |
|---|---|---|---|
| Matters portfolio | `getMattersV2({page_size:100})` | **100** | 9,900 matters invisible; search can't reach them |
| Tasks list | `getTasks({limit:200})`, firm-wide | **200** | ~200 of tens of thousands |
| Overdue tasks | `getOverdueTasks()` — **no limit parameter exists** | **unbounded** | entire overdue backlog in one response |
| Clients | `getClients({limit:100, q})` | **100 per result page** | total remains accurate and records remain searchable; no way to browse past the first 100 |
| Contacts | `getContacts({limit:100, q})` | **100 per result page** | same |
| Matter documents | no limit/offset/page at all | **unbounded** | every document rendered, unsearchable |
| Chat sidebar documents | `getDocuments()`, full list | **unbounded** | re-fetched every 3s per upload (see #7) |
| Partner log | `getPartnerLog({limit:25})` | **25** | under one morning |
| Revision history | `priorRevisions.slice(0, 5)` | **5** | version 6+ unreachable |

Three failure classes: **silent truncation** (a prefix presented as the whole),
**acknowledged but unpageable results** (the total is known but the next page is
unreachable), and **unbounded fetch** (everything, rendered).

---

## P0 — Silent truncation that misinforms

### 1. The matters portfolio reports a false total

`frontend/src/pages/MatterPortfolioPage.jsx:359,387,398-409`

```js
getMattersV2({ page_size: 100 })
  .then(data => setMatters(data.items || []))
...
total: matters.length,
...
if (search) { const q = search.toLowerCase(); /* filters the 100 in memory */ }
```

Three compounding problems in one component:

- **One page, ever.** No pagination control, no "load more", no `page` state.
- **Client-side search.** The search box filters the 100 rows already in memory.
  A matter at position 4,000 cannot be found by typing its name.
- **The stat card lies.** `total: matters.length` renders as **"Total: 100"** for
  a firm with 10,000 matters. The backend returns a real `total` in the response
  (`app/routers/matters.py:517`) and the frontend discards it.

The empty state then advises: *"Try clearing filters or searching by client,
attorney, practice area, or matter name"* (`:683`) — guidance that cannot
succeed, because the matter was never fetched.

A firm looking at a dashboard that says it has 100 matters when it has 10,000 has
lost confidence in every other number on the screen.

Pass `search`, `status`, and `practice_area` to the API (it already accepts all
three), render `data.total`, and add pagination.

### 2. Matters cannot be searched by case number

`backend/app/routers/matters.py:505`

```python
if search:
    conditions.append(Matter.matter_name.ilike(f"%{search}%"))
```

`Matter.case_number` exists (`app/models/plugin.py:245`) and is displayed
throughout the UI — but it is **not in the search filter**.

Case number is how a receptionist identifies a caller ("I'm calling about
24-CV-01847"), how a paralegal identifies a filing, and how a court identifies
the matter. At one matter this is invisible. At 10,000 it is the primary key
people actually speak out loud, and it returns nothing.

Add `case_number`, and `client.display_name` while you're in there.

### 3. Overdue tasks is unbounded

`backend/app/routers/tasks.py:319-340`

```python
async def get_overdue_tasks(
    matter_id: Optional[uuid.UUID] = None,
    assigned_to: Optional[uuid.UUID] = None,
    current_user=..., db=...,
):
```

No `limit`. No `offset`. Every sibling endpoint has both — `list_tasks` defaults
to `limit: 100, offset: 0` (`:409`) — this one has neither, and the frontend
calls it with no arguments (`TasksPage.jsx:1197`).

A firm at 10,000 matters carrying a normal overdue backlog gets every overdue
task in a single response, serialized through `TaskResponse.model_validate` in a
Python loop, then rendered as unvirtualized DOM rows in the Overdue section
(`TasksPage.jsx:1524`).

This is the one on the list most likely to actually take a page down rather than
merely mislead. It is also the smallest fix: add `limit`/`offset` matching
`list_tasks`, and paginate the section.

### 4. Every operational search is a full table scan

The receptionist's search — run on **every single call**, the hottest path in the
product — is built from leading-wildcard `ILIKE` across multiple fields:

`backend/app/routers/intake_dashboard.py:298-307`

```python
whole_match = or_(*(field.ilike(whole) for field in fields))
token_match = and_(
    *(or_(*(field.ilike(f"%{token}%") for field in fields)) for token in tokens)
)
return or_(whole_match, token_match)
```

A leading `%` means **no B-tree index can be used**. The indexes on `contacts`
are all exact/prefix shaped — `(tenant_id, email)`, `(tenant_id, last_name)`
(`app/models/contact.py:32-38`) — and none of them apply. Same for the matters
search in #2.

What makes this worth fixing rather than accepting: **the team already knows how
to do this correctly.** There are GIN indexes in the codebase —
`ix_chunks_fts` on document chunks (`app/models/document.py:93`) and
`ix_smb_file_index_search_vector` (`app/models/smb_file_index.py:29`). The RAG
side got real full-text search. The operational side — the queries a human runs
dozens of times a day — never did.

Add `pg_trgm` GIN indexes on the searched contact/lead/matter columns, or a
`tsvector` column following the pattern already established for chunks.

---

## P1 — Ordering and query shape

### 5. Task priority sorts lexically in the list view

`backend/app/routers/tasks.py:438` — `Task.priority.desc()` on a `String` column
yields `urgent, medium, low, high`. Detailed in the receptionist review (#4).

At scale this stops being cosmetic: combined with `limit: 200`, high-priority
tasks are pushed off the fetched page by low-priority ones, so they become
invisible rather than merely mis-ranked. The board's `priority_bucket` case
statement (`:616`) is the fix, already written.

### 6. `sort_by` is passed to `getattr` unvalidated

`backend/app/routers/matters.py:521`

```python
sort_col = getattr(Matter, sort_by, Matter.updated_at)
```

`sort_by` is an unvalidated query parameter. It is not a SQL injection risk —
SQLAlchemy handles that — but any non-column attribute resolves to something that
isn't sortable. `?sort_by=metadata` returns the model's `MetaData` object, and
`.desc()` on it raises, producing a 500 rather than a 400.

Validate against an allowlist of sortable columns.

### 7. Upload polling scales with files, not with work

`frontend/src/components/FileUpload.jsx:47-90`

Per uploaded file, one `setInterval` at 3s, each calling `getDocuments()` — the
**entire** document list, not the one document being polled — and there is no
`useEffect` cleanup anywhere in the component.

Twenty files means roughly 7 full-list fetches per second, continuing for up to
90 seconds after the component unmounts. At a tenant with thousands of documents
each of those responses is large.

Poll the single document by id, share one timer across the batch, and clear it
on unmount. (Also covered as paralegal #6, where the serial-upload half matters
more.)

### 8. The board runs 2 queries per status column

`backend/app/routers/tasks.py:625-640` loops `for status_value in
BOARD_TASK_STATUSES`, issuing a `func.count()` over the tenant's tasks in that
status plus a paginated select, each carrying a correlated
`TaskAutomationRun` subquery.

Bounded at roughly 12 queries per board load, so this is not pathological — but
each count is a full aggregate over a large partition and there is no cheaper
approximate path. Worth measuring at 50k+ tasks before it becomes the slowest
screen in the product.

---

## P2 — Truncation without acknowledgment

### 9. Clients and contacts stop at 100; partner log truncates silently

`ClientsPage.jsx:161-167` (`limit: 100`) and `ContactsPage.jsx:177-183`
(`limit: 100`) both pass the user's search query to the backend and retain the
API's `total`. Their page headers display that total. A record beyond the first
100 therefore remains searchable, and the user can see that more records exist.
However, neither page renders pagination or a load-more control, so broad
browsing still stops after the first 100 matches.

The partner log is the stronger correctness failure:
`IntakeDashboardPage.jsx:387` requests `limit: 25` without a displayed total,
page control, or indication that more entries exist.

Add pagination to clients and contacts. For the partner log, expose and display
a total or next-page signal as well as a way to retrieve the remaining entries.

### 10. Matter documents fetch and render everything

`components/MatterDocumentsTab.jsx` — zero hits for `limit`, `offset`, `page`,
or any search input across 864 lines. A litigation matter with 800 produced
documents renders 800 unvirtualized rows with no way to narrow. Covered as
paralegal #9; noted here because it is the opposite failure from #1 and needs
the opposite fix.

---

## What this means per persona

**Attorney** (1 → 10,000 matters): the portfolio caps at 100 and reports a false
total (#1), case-number search doesn't work (#2), and the task list mis-ranks
priority while capping at 200 (#5). Their filters also don't survive the back
button — `PRODUCT_UX_REVIEW.md` #10 — which is an annoyance at 20 matters and
unusable at 10,000.

**Paralegal**: document lists are the opposite problem — unbounded and
unsearchable (#10), while upload polling load grows with batch size (#7). Their
volume work is exactly the workload these paths were not shaped for.

**Receptionist**: the worst-hit, because their hottest path is the least
indexable query in the product (#4). Every call means a multi-table full scan,
and the partner log they'd use to track handoffs holds 25 rows (#9).

---

## Suggested order

1. **#3 unbounded overdue** — smallest fix, largest blast radius, and the only
   one here likely to take a page down.
2. **#1 matters pagination and real total** — the false "Total: 100" undermines
   trust in every number on the dashboard.
3. **#2 case-number search** — one line, and it restores the identifier people
   actually say out loud.
4. **#5 priority ordering** — reuse the helper that already exists.
5. **#4 trigram/FTS indexes** — the largest infrastructure item, and the one the
   receptionist feels on every call. Follow the pattern already used for chunks.
6. **#9 directory pagination and partner-log disclosure** — let clients and
   contacts traverse the totals they already show, and give the partner log a
   total or next-page signal instead of a silent 25-row ceiling.
7. **#7 / #8 / #10** — polling, board counts, document lists.
8. **#6 sort_by allowlist** — robustness cleanup.
