# Product & UX review — platform TAC

Reviewed on branch `claude/product-ux-code-review-tf7ake`, covering the Operator
Console (`frontend/src/pages/PlatformPage.jsx`, ~161 KB, the largest file in the
repository) and the `/platform` API behind it.

The frame is a technical account manager who takes customer questions, bugs, and
issues, and lives in this panel all day. Someone emails "our associate can't see
Tasks" or "the app was throwing errors around 2pm" and the TAC has to turn that
into an answer.

The console is the **best-paginated surface in the product** — every list has
`page`, `limit`, `total`, and working Next controls (`:863`, `:926`, `:987`,
`:2976`), which is more than the customer-facing pages manage. Tenant logs,
access logs, and system errors all paginate with severity, time-window, tenant,
and endpoint filters. The session key is held in `useState` only (`:2634`) and
never written to storage, which is the right call for a credential this
powerful.

The question asked was whether this persona has the visibility, workflows, and
tools to be functional. Visibility into *infrastructure*: yes. Into *customers*:
substantially no.

---

## P0 — The console cannot answer the most common ticket

### 1. Module configuration is invisible, though the API returns it

The single most common support question for a module-gated multi-tenant product
is some form of *"why can't my user see X?"* In this product that is nearly
always `enabled_modules` — the mechanism that hides nav items
(`Sidebar.jsx:139-147`) and blocks routes (`App.jsx:159`).

The backend already serves it. `app/routers/platform.py:769` returns
`enabled_modules` in the tenant detail payload, and `:872-884` accepts updates
to it.

The console never displays it:

```
grep -n "enabled_modules" frontend/src/pages/PlatformPage.jsx  → 0 hits
```

So a TAC holding a ticket that the API could answer in one field has to ask
engineering, or query the database directly. The plumbing is finished on both
ends and the panel in between was never wired.

Render `enabled_modules` in the expanded tenant row, and — since the PATCH
endpoint already exists and validates — let the TAC toggle it.

### 2. There is no impersonation or support view

```
grep -n "impersonat|support_view|act_as" frontend/src/pages/PlatformPage.jsx  → 0 hits
```

The TAC cannot see what the customer sees. Every ticket that starts "the button
isn't there" or "it looks wrong on my screen" has to be resolved through
screenshots and description, because there is no way to load the tenant's
workspace as one of its users.

For a product whose behavior varies this much per tenant — modules, role,
license, plugin entitlements, cloud provider, feature flags — reproduction is
most of diagnosis. A read-only, audited support view is the highest-value
addition to this console.

### 3. Logs fail silently, exactly where it matters most

`PlatformPage.jsx:688,709,730`

```js
} catch { /* silent */ }
```

All three sit on the diagnostic loaders: system errors, tenant logs, and access
logs. When a log query fails — expired key, backend error, timeout — the panel
renders empty or stale with no message.

The TAC then tells the customer "I'm not seeing any errors on your tenant." They
have not looked at an empty result. They have looked at a failed request that
chose not to mention it.

This is the one finding here that causes wrong answers rather than slow ones.
Surface the failure; a stale-data badge on the panel would do.

(The fourth, `:2722` `catch { setTenantDetail(null) }`, has the same problem in
the tenant expander — a failed detail fetch is indistinguishable from a tenant
with no detail.)

---

## P1 — Workflows the console does not have

### 4. There is no ticket, issue, or customer-question surface at all

```
grep -rn "ticket|support_request" backend/app/models backend/app/routers  → 0 hits
```

The seven tabs are Dashboard, Tenants, Integrations, MCP, AI Routing, Logs, and
System (`:2733-2740`). All infrastructure telemetry. Nothing represents a
customer *asking* something.

So the intake half of this persona's job happens entirely outside the product —
email, a spreadsheet, some other helpdesk — and nothing connects a question to
the tenant it concerns, the logs from that hour, or the resolution. There is no
history: the next TAC handling the same firm starts cold.

This is a genuine product gap rather than a bug, and worth sizing deliberately.
The minimum useful version is a note per tenant with an author and timestamp,
visible in the expanded tenant row. That alone converts the console from a
telemetry viewer into a place where account context accumulates.

### 5. Tenant search only reaches the page you are on

`PlatformPage.jsx:2726-2728`

```js
const filtered = tenants.filter((t) =>
  !search || t.name.toLowerCase().includes(search.toLowerCase()) || ...
)
```

`getPlatformTenants(platformKey, page)` fetches one page; the search box filters
that page in memory. A TAC with several hundred tenants types a firm's name and
finds it only if it happens to be on the page already loaded.

Step one of every support interaction is "find your tenant," and it fails at the
scale the console is built for. The same client-side-search-over-one-page pattern
appears in `SCALE_REVIEW.md` #1 for matters; here it sits in the tool the TAC
uses first, every time.

Pass `search` to the API.

### 6. Logs cannot be searched by user, request ID, or message text

The filters are severity, time window, tenant, and endpoint. There is no free-text
field.

The information a customer actually provides — "our paralegal Sarah hit this",
"it said something about a token", a request ID from a screenshot — cannot be
used to narrow anything. The TAC pages through 50-row windows by eye.

Add free-text search over message and a `user_id`/`request_id` filter. Request-ID
lookup in particular turns a ten-minute scroll into one query.

---

## P2 — Friction during an incident

### 7. A refresh ends the session and the investigation

`platformKey` lives in `useState` (`:2634`) and is never persisted. Refreshing —
or an accidental navigation — signs the TAC out and discards the selected tenant,
active tab, filters, and page position.

The non-persistence is the right security posture and should stay. The recoverable
part is everything else: keep tab, tenant, and filters in the URL so
re-authenticating returns the TAC to where they were rather than to the dashboard.

### 8. The console is one 161 KB module

`PlatformPage.jsx` is lazily loaded, so it does not burden other routes — but it
is a single ~3,000-line component tree holding every tab. Any TAC-facing change
means working in the largest file in the repository, and the whole thing
re-renders on state that concerns one tab.

Not urgent, and not user-visible. Worth splitting per tab before the next
significant feature lands here.

---

## Is this persona functional?

**Visibility into infrastructure: yes.** Error logs, access logs, per-tenant
summaries, health, AI routing, integration readiness — all present, all
paginated, all filterable by time and severity. This is real operational
tooling.

**Visibility into customers: no.** The console cannot show what a tenant's users
can see (#1), cannot reproduce what they experience (#2), and cannot be searched
by the identifiers a customer supplies (#5, #6).

**Workflow: absent.** There is no representation of a customer question anywhere
in the system (#4). Every ticket is handled out-of-band and leaves no trace, so
account knowledge does not accumulate and does not transfer between TACs.

**Trust: compromised by #3.** Silent log failures mean the console can report
"nothing wrong" when it has not successfully looked. A diagnostic tool that
fails quietly is worse than one that fails loudly.

A TAC can today answer "is the platform healthy?" They cannot reliably answer
"what happened to this customer?" — and that is the question they are actually
paid to answer.

---

## Suggested order

1. **#3 silent log failures** — smallest fix, and it is currently producing
   confidently wrong answers to customers.
2. **#1 show `enabled_modules`** — the API already returns it; this is wiring,
   and it resolves the most common ticket class outright.
3. **#5 server-side tenant search** — step one of every interaction.
4. **#6 log search by user and request ID** — turns paging into lookup.
5. **#2 read-only support view** — largest effort, largest payoff; audit it.
6. **#4 per-tenant notes** — the minimum viable version of account memory.
7. **#7 / #8** — URL state, then splitting the module.
