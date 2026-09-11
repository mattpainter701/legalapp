# Matter onboarding end-to-end UX audit — 2026-09-11

Audited the attorney onboarding journey as an end user, first as the attorney
who starts the case and then as the colleague who picks it up cold. The journey
was run against the real FastAPI application, a disposable PostgreSQL database,
and a real browser (Chromium via Playwright). Only outbound client delivery was
captured to a local mailbox; browser API responses and document storage were
real. Nothing was sent to a live provider or a real client.

- Audited checkout: the repository at `main` `aace67ee` plus the fixes on
  branch `feat/matter-onboarding-ux-audit`.
- Live deployment observed separately at `df3d5838` (built 2026-09-11); a live
  authenticated walk was not possible because the guided demo requires an
  access code we do not hold. The live version endpoint stayed reachable.
- Artifacts: the capture writes its screenshots and `observations.json` to
  `frontend/test-results/matter-ux-audit/` by default; set `E2E_AUDIT_OUT` to
  write elsewhere.
- Harness: `frontend/e2e/matter-ux-audit.capture.e2e.js`, excluded by
  `testIgnore` unless `E2E_AUDIT=true`, so it does not run in CI.

## Journey verdicts

| Step | What the user does | Verdict | Evidence |
| --- | --- | --- | --- |
| 1. Create a matter and client | New Matter, inline "+ Create new contact", Open Matter | Works | `observations.json` `matter-created`; `03-new-matter-modal.png` |
| 2. Kick off by sending a fee agreement | Overview shows "Start this case" and "Send client paperwork"; the drawer is Documents / Deadlines / Send | Works | `07-paperwork-sent-strip.png`; packet shows 0 of 4 with a status chip |
| 3. Sign by e-sign and get a notification/alert | Client signs in the portal; attorney's screen updates on the poll and now raises a toast and refreshes the signature panel | Works (fixed) | Measured ~27s to the "Fee agreement signed" strip before the fix; toast added after the capture ran |
| 4. Follow-up task on Calendar and Tasks | One assigned task, high priority, due 24 elapsed hours after signing; also rendered as a calendar event | Works | `13-tasks-page.png`, `14-calendar-page.png`; `followupDeltaHours: 24` |
| 5. Email portal info and needed documents | Post-signing portal email is automatic and names the requested records; the composer now prefills the client email and can insert the outstanding-records list | Works (fixed) | email snippets below |
| 6. Manage interactions and hand off | Activity merges timeline, signatures, outbound mail, and notes; client checklist shows requested records | Works | `16-activity-history.png`, `12-client-requested-docs.png` |

## Findings

Severity is from the end user's perspective. "Fixed" means a change is on the
audit branch with a regression test.

### High

1. **The initial paperwork email never told the client which additional records
   to upload. Fixed.**
   The attorney added a requested upload ("Marriage certificate"), but the
   "Review your paperwork" email listed only the documents to sign. The client
   could only discover the request after opening the portal.
   - Before: `Please review and complete each document: Fee agreement, General intake form.pdf. Secure paperwork link: ...`
   - After: `... Please have these records ready to upload: Marriage certificate. ...`
   - Root cause: `message()` in `backend/app/services/matter_intake.py` listed
     `selected_documents` but not `upload_*` requirements.

2. **Signature completion has no timely in-app alert. Fixed.**
   There is no notification bell. The only in-app signal was the Overview
   `CaseSetupCard`, which polls every 30s (`CaseSetupCard.jsx:67`); the measured
   delay was ~27s. `SignatureRequestsPanel` loaded once on mount
   (`MatterDetailPage.jsx:2572`) and did not refresh. A global toast region
   exists (`ToastProvider.jsx:152`) but no toast was emitted on signing.
   - Fix: the card now reports each polled packet through an `onPacketChange`
     callback; the page toasts "Fee agreement signed" on the transition and
     passes a `refreshKey` that re-arms the signature panel. The 30s poll
     remains the transport (an SSE/short-poll signal is still a possible
     follow-up), but the attorney no longer watches a stale panel. The
     staff assignment email and external-calendar push stay deferred to the
     `matter-intake` worker tick (up to ~60s) and suppressed on demo tier
     (`backend/app/services/task_notifications.py:26-31`) — those are a
     fallback, not the alert.

### Medium

3. **Raw machine timestamps leaked into task descriptions. Fixed.**
   Task rows showed `Intake action due 2026-09-12T19:28:47.207444+00:00. Client
   meeting options: ...` next to a correctly formatted due date.
   - Root cause: `backend/app/services/matter_intake.py` used
     `due.isoformat()` in the description.
   - After: `Intake action due Sep 12, 2026 02:28 PM. ...` in the packet's
     timezone.

4. **Calendar events showed the task type, not the task. Fixed.**
   The two follow-ups rendered on the calendar as `follow_up` instead of their
   titles. Cause: `backend/app/routers/calendar.py` used
   `task.task_type or task.title`. Now the readable title wins.

5. **Email Client does not prefill the client's email. Fixed.**
   Composing from the matter showed "No client email on file — enter manually",
   even though the matter's client contact had an address that intake had just
   used.
   - Root cause: the composer read `matter.client?.email`, a field the matter
     payload never carried. `MatterResponse` now includes `client_email` (and
     `client_contact_type`), resolved server-side from the linked contact; the
     composer and the portal invite form read it.
   - The composer also gained an "Insert requested records" helper that names
     the outstanding `upload_*` requirements from the intake packet. An
     "insert portal link" helper was deliberately not added: the raw invite
     token only exists at creation (`client_portal.py` returns it once), so a
     composer insert would mean silently minting an invite. The automatic
     post-signing email already carries the link, and the Client Portal tab
     (now a primary tab) can mint and copy a fresh one.

6. **Client Portal is only reachable through "Matter settings". Fixed.**
   The primary tabs were Overview, Activity, Documents, and Billing, with
   Client Portal in the secondary row that only appears after clicking
   "Matter settings". `portal` is now in `PRIMARY_SECTIONS`, so it renders in
   the main row on every matter; `?tab=portal` deep links are unchanged.

7. **Two client directories. Fixed (analysis corrected the premise).**
   Matter creation selects from contacts; the Clients & CRM area looked like a
   separate client record. In the schema it is not: CRM clients are a filtered
   view over the same `Contact` table (`contact_type IN ('client','prospect')`,
   `clients.py`), and a matter's `client_contact_id` already points at that
   row. The visible gap was navigation: the client name on a matter now links
   to `/clients/{id}` when the contact is a CRM-typed client and to
   `/contacts/{id}` otherwise.

### Low

8. The client-side upload control said "Upload completed document" rather than
   naming the requested record (`12-client-requested-docs.png`). **Fixed** — an
   `upload`-kind requirement now reads "Upload Marriage certificate".
9. The guided demo workspace silently suppresses the staff task-assignment email
   and external-calendar push, which can surprise a demo audience.
10. External calendar cleanup for an unconnected calendar retries every tick and
    logs "Calendar cleanup ... is still pending"; correct by design but noisy.
    The internal calendar is authoritative and did show both follow-ups.

## Fixes in this branch

- `backend/app/services/matter_intake.py`: added `followup_task_description()`
  (human-readable, packet timezone) and `requested_upload_labels()`; the
  "welcome" and "signed" emails now name the requested records.
- `backend/app/routers/calendar.py`: prefer the task title for `task_due` events.
- `CaseSetupCard` reports each polled packet via `onPacketChange`; the matter
  page toasts on the signed transition and refreshes `SignatureRequestsPanel`
  with a `refreshKey`.
- `MatterResponse` carries `client_email` and `client_contact_type`; the Email
  Client composer prefills the To field and can insert the outstanding
  requested-records list.
- Client Portal moved into the matter's primary tabs; the client name on a
  matter links to the CRM client record (or the contact record for
  non-client-typed contacts).
- `ClientIntakeChecklist` names the requested record on the upload control.
- Tests: `backend/tests/unit/test_matter_intake.py` (description formatting and
  requested-upload labels), `backend/tests/test_calendar_sync.py` (calendar
  title), `backend/tests/test_matters.py` (client fields on the matter
  payload), `CaseSetup.test.jsx` (packet-change callback),
  `ComposeEmailModal.test.jsx` (prefill and records insert),
  `MatterIntake.test.jsx` (upload control naming), and
  `MatterNavigation.test.jsx` (Client Portal in the primary row).

## Verification

- `python -m pytest tests/unit/test_matter_intake.py tests/test_calendar_sync.py`
  -> 62 passed.
- `python -m pytest tests/test_matters.py` -> 5 passed (client fields on the
  matter payload).
- `npx vitest run` -> 163 files, 1015 passed; eslint clean on the changed files.
- `frontend/e2e/matter-onboarding-live.e2e.js` (canonical acceptance) -> 1 passed
  (25.9s) on the fixed code.
- `frontend/e2e/matter-ux-audit.capture.e2e.js` -> 1 passed (59.0s);
  `observations.json` records `emailMentionsUploads: true` and the readable task
  description.

## Remaining backlog

1. A matter-level notification surface beyond the toast (a bell/history), and a
   faster signed signal than the 30s poll (SSE or short-poll) if the delay
   still reads as lag in daily use.
2. "Insert portal link" in the composer — needs a non-mutating way to read the
   active invite link; today the raw token exists only at invite creation.
3. The guided demo workspace silently suppresses the staff task-assignment
   email and external-calendar push (finding 9); decide whether the demo should
   say so.
4. External-calendar cleanup log noise for unconnected calendars (finding 10).

## Limitations

- Delivery was captured locally; live Microsoft/Google/SMS providers were not
  exercised. External-calendar behavior is fire-and-forget and was not verified
  against a connected calendar.
- The audit ran against a local disposable database in `DEV_MODE`, so RLS was
  not enforced; tenant isolation is covered by the existing database suites and
  was not re-proven here.
- The guided demo on the live deployment requires an access code, so the live
  build was observed only through its public version endpoint.
