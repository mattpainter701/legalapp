# TASKS_ARCHIVE.md

## Sprint 7 — Calendar, Communications & Matter Operations (v0.8.0)

**Goal:** Make the practice management layer truly operational — deadline calendar, full communications router, lead-to-matter conversion, matter budget tracking, and document templates.

### 801. Deadline Calendar (P0, MEDIUM) — COMPLETED

- [x] `GET /api/calendar/events` — aggregates task due_dates + matter key_dates JSON + renewal dates; returns `{date, title, type, matter_id, task_id}` list; supports `?start=&end=` date range
- [x] CalendarPage.jsx — month/week calendar view; color-coded by event type; click → matter/task detail
- [x] Sidebar nav link to `/calendar`

Files: `backend/app/routers/calendar.py`, `backend/app/schemas/calendar.py`, `frontend/src/pages/CalendarPage.jsx`

### 802. Communications Router (P0, MEDIUM) — COMPLETED

- [x] Full CRUD router for `communication_logs` table at `/api/communications`
- [x] `GET /api/communications` — list with filter by matter_id, contact_id, channel, date range
- [x] `POST /api/communications` — log a call, email, meeting, or note against a matter/contact
- [x] `GET/PATCH/DELETE /api/communications/{id}` — detail, update, delete
- [x] Frontend: CommunicationsPage.jsx — log list with filters; quick-log form (channel, subject, body, matter link)

Files: `backend/app/routers/communications.py` (extend or create), `backend/app/schemas/communication_log.py` (extend), `frontend/src/pages/CommunicationsPage.jsx`

### 803. Lead-to-Matter Conversion (P1, SMALL) — COMPLETED

- [x] `POST /api/intake/leads/{id}/convert` — creates a Matter from a Lead; links Lead.contact_id as Matter.client_contact_id; sets lead status = "matter_opened"; returns new MatterResponse
- [x] Frontend: IntakePage "Convert to Matter" button on engaged leads → opens confirm modal with matter_name, matter_type, role, jurisdiction, counterparty fields; navigates to new matter on success
- [x] `convertLead` API function in `frontend/src/api.js`
- [x] `LeadConvertRequest` schema in `backend/app/schemas/contact.py`

Files: `backend/app/routers/intake.py`, `backend/app/schemas/contact.py`, `frontend/src/pages/IntakePage.jsx`, `frontend/src/api.js`

### 804. Matter Budget Tracking (P1, MEDIUM) — COMPLETED

- [x] Migration 024: add `budget_amount` (Numeric 12,2), `budget_currency` (String 3, default "USD") to matters table
- [x] `GET /api/reports/matters/{id}/budget` — billable hours × default_rate vs budget_amount; % utilization
- [x] Frontend: budget utilization badge in MatterDetailPage header; budget amount editable inline

Files: `backend/migrations/versions/024_add_matter_budget.py`, `backend/app/routers/reports.py` (add endpoint), `frontend/src/pages/MatterDetailPage.jsx`

### 805. Document Templates (P2, MEDIUM) — COMPLETED

- [x] `DocumentTemplate` model — title, body (Text, `{{variable}}` placeholders), category (engagement_letter/retainer/NDA/motion/other), is_active
- [x] Migration 025: document_templates table + RLS
- [x] `GET/POST /api/templates` — list/create templates with category validation
- [x] `GET/PATCH/DELETE /api/templates/{id}` — detail, update, delete; `PATCH` validates category
- [x] `POST /api/templates/{id}/render` — `{{variable}}` substitution with `{variables: {key: value}}`; optionally creates a MatterDocument with `document_category="generated"`; verifies matter belongs to tenant
- [x] Frontend: TemplatesPage.jsx — template library grid with category badges, active toggle; create/edit modal; generate modal with variable detection and fill-in; render preview; save-to-matter option

Files: `backend/app/models/document_template.py`, `backend/app/schemas/document_template.py`, `backend/app/routers/document_templates.py`, `backend/migrations/versions/025_create_document_templates.py`, `frontend/src/pages/TemplatesPage.jsx`

---
