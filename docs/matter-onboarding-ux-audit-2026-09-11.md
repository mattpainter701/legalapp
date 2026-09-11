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
| 3. Sign by e-sign and get a notification/alert | Client signs in the portal; attorney's screen updates on a 30s poll. No toast and no immediate alert. | Partial | Measured ~27s to the "Fee agreement signed" strip; `notificationSurface` was the empty global toast region |
| 4. Follow-up task on Calendar and Tasks | One assigned task, high priority, due 24 elapsed hours after signing; also rendered as a calendar event | Works | `13-tasks-page.png`, `14-calendar-page.png`; `followupDeltaHours: 24` |
| 5. Email portal info and needed documents | Post-signing portal email is automatic and now names the requested records; the manual composer still does not prefill the email or insert the portal link | Partial (improved) | email snippets below |
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

2. **Signature completion has no timely in-app alert. Open.**
   There is no notification bell. The only in-app signal is the Overview
   `CaseSetupCard`, which polls every 30s (`CaseSetupCard.jsx:67`); the measured
   delay was ~27s. `SignatureRequestsPanel` loads once on mount
   (`MatterDetailPage.jsx:2572`) and does not refresh. A global toast region
   exists (`ToastProvider.jsx:152`) but no toast is emitted on signing. The
   staff assignment email and external-calendar push are deferred to the
   `matter-intake` worker tick (up to ~60s) and are suppressed on demo tier
   (`backend/app/services/task_notifications.py:26-31`).
   - Recommendation: emit a toast when the packet transitions to signed and
     refresh the signature panel, or move the matter to an SSE/short-poll
     signal. Keep any email/calendar behavior as a fallback, not the only alert.

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

5. **Email Client does not prefill the client's email. Open.**
   Composing from the matter showed "No client email on file — enter manually",
   even though the matter's client contact had an address that intake had just
   used. The attorney must retype it, and there is no shortcut to insert the
   portal link or the outstanding-records list, so step 5 is split across two
   places.
   - Root cause: `MatterDetailPage.jsx:2291` passes
     `clientEmail={matter.client?.email || null}`, which is empty for a
     contact-linked client; the matter payload does not carry the contact email.
   - Recommendation: resolve the client contact email server-side (or fetch the
     contact when composing) and offer an "insert portal link" / "include
     requested records" helper in the composer.

6. **Client Portal is only reachable through "Matter settings". Open.**
   The primary tabs are Overview, Activity, Documents, and Billing
   (`MatterDetailPage.jsx:238`). Client Portal lives in the secondary row that
   only appears after clicking "Matter settings" (`MatterDetailPage.jsx:825`),
   which is the wrong mental model for a destination an attorney uses to share
   the portal and documents.

7. **Two client directories. Open (pre-existing).**
   Matter creation selects from contacts; the Clients & CRM area maintains a
   separate client record. Nothing in the matter flow reconciles them, which
   risks duplicate customers and split history.

### Low

8. The client-side upload control says "Upload completed document" rather than
   naming the requested record (`12-client-requested-docs.png`).
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
- Tests: `backend/tests/unit/test_matter_intake.py` (description formatting and
  requested-upload labels) and `backend/tests/test_calendar_sync.py` (calendar
  title).

## Verification

- `python -m pytest tests/unit/test_matter_intake.py tests/test_calendar_sync.py`
  -> 62 passed.
- `frontend/e2e/matter-onboarding-live.e2e.js` (canonical acceptance) -> 1 passed
  (25.9s) on the fixed code.
- `frontend/e2e/matter-ux-audit.capture.e2e.js` -> 1 passed (59.0s);
  `observations.json` records `emailMentionsUploads: true` and the readable task
  description.

## Remaining backlog

1. E-sign in-app alert (toast + panel refresh) and a matter-level notification
   surface.
2. Composer: prefill the client email, insert the portal link, and attach the
   outstanding-records list.
3. Surface Client Portal as a first-class matter destination.
4. Bridge contacts and CRM clients at matter creation.
5. Name the requested record in the client upload control.

## Limitations

- Delivery was captured locally; live Microsoft/Google/SMS providers were not
  exercised. External-calendar behavior is fire-and-forget and was not verified
  against a connected calendar.
- The audit ran against a local disposable database in `DEV_MODE`, so RLS was
  not enforced; tenant isolation is covered by the existing database suites and
  was not re-proven here.
- The guided demo on the live deployment requires an access code, so the live
  build was observed only through its public version endpoint.
