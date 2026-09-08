# Workflow suggestions from firm history

Workflow settings and the onboarding Review screen prepare ordinary workflow
template drafts from the firm's practice. Analysis does not activate rules,
apply tasks, send correspondence, or consume AI credits. It is deterministic;
customer-harness inference and separately metered LawHand AI remain distinct.

## Review suggestions

1. Choose **Analyze firm history**, or stage migration history during onboarding.
2. Inspect proposed tasks, timing, assignment roles, observed matter counts,
   source references, and the configuration fingerprint in Workflow settings.
3. Review the ordinary draft under **Templates and versions**. To edit it,
   create the next draft version with the existing editor.
4. A user with `approve_legal_work` approves the template version and separately
   activates its draft automation rule using the existing controls.

An amendment creates a draft version of an active rule's existing template. It
preserves established stages, required fields, and unobserved tasks. Approving
the version updates the active rule's approved template; it does not create a
second active rule. Approval fails if the approved baseline changed meanwhile.

**Decline pattern** requires a reason, retains evidence, and suppresses future
suggestions for the same trigger and matter conditions. A declined draft cannot
later be approved. Declining an amendment leaves the active rule intact.

## Observations and limits

Analysis requires `manage_workflows` and `manage_matters`; including imports also
requires `admin_settings`. The worker rechecks the actor's active status, license,
and capabilities before reading history. Workflow managers and legal approvers
can review proposal evidence.

Native observations cover tasks created in the last 60 days, owner/attorney
assignments, generated-document template usage, review routing, and sent email/SMS
timing. Queries read scheduling metadata and bounded task/template labels, never
document text, correspondence subjects/bodies, or prompts. Evidence stores counts,
timing ranges, roles, source ids and fingerprints. Suggested labels remain tenant
work product requiring review; known matter names, URLs, email addresses, and long
numeric references are removed.

A pattern needs at least three distinct matters and occurrence in at least 60%
of its cohort. Duplicate records count once per matter. Assignment needs 80%
agreement or stays unassigned. Due offsets use the median within 0–3,650 days.
Evidence distinguishes explicit due dates from task/document creation and sent
correspondence timing. Current-stage and review-routing counts are evidence, not
invented historical stage dates or automatic review-policy changes. Initial
suggestions use matter opening as their time anchor.

Native, Clio, and Tabs3 cohorts remain separate to avoid double-counting migrated
matters. A run reads at most 5,000 rows per native source and 5,000 imported history
rows, plus 10,000 import context rows. Truncation and skipped rows are recorded.
It considers at most 50 cohorts and prepares at most five drafts of 50 checklist
items each, respecting the existing 50-rule limit. Repeated analysis shares an
outstanding job; completed jobs and equivalent proposals are idempotent.

## Import history

Existing Tabs3 bundle staging queues analysis in the same transaction.
PracticeMaster `CMCAL.Client_ID` joins `CMCLIENT.Client_ID`; `Due_Date`, `Date_Open`,
and `Desc` supply the schedule. `CMCAT.Category_Number`/`Description` resolves the
category when present. Evidence retains calendar, client, and category references.
Private calendar records and secure clients are excluded. Field definitions come
from the [checked-in vendor schema](tabs3-odbc-schema.json).

For CSV exports, open **Import Clio or Tabs3 workflow history**. Upload a task CSV
and optionally a separate matter CSV. Map task matter key, title, due date, and a
matching matter-open date; map matter type/practice area when available. The UI
joins the files. Each UTF-8 CSV supports 2 MiB, 5,000 rows, and 100 unique columns.
Dates accept ISO, MM/DD/YYYY, or YYYYMMDD. Duplicate matter keys fail explicitly.
Missing dates are reported rather than guessed. Only normalized mapped scheduling
fields are retained; unmapped notes and other source columns are discarded. The
preview fingerprint binds both files and is checked again during staging.

This imports history for suggestions, not a Clio API sync or canonical
matter/contact/accounting records. Clio documents CSV exports in its
[task guide](https://help.clio.com/hc/en-us/articles/9204982812699-Filter-and-Export-Tasks)
and [matter guide](https://help.clio.com/hc/en-us/articles/9286116462747-Filter-Export-and-Convert-Matters).
Column mapping stays explicit because export layouts vary.

## Integrity and verification

Migration `167_workflow_synthesis` adds `workflow_configuration_proposals` with
FORCE RLS, tenant-composite foreign keys, exact draft/running-job bindings,
immutable evidence and configuration, and a one-way reasoned rejection. Templates
and rules use existing models, hash computation, and approval endpoints. Only
authorized expired-demo purge can remove evidence; downgrade refuses to discard it.

`scripts/rehearse_workflow_synthesis.py` exercises a fully migrated PostgreSQL
database through a NOSUPERUSER/NOBYPASSRLS role: native history, both import paths,
human approval, amendments, rejection suppression, crash/retry, content exclusion,
and cross-tenant reads. The Tabs3 fixture automatically prepares a template and
rule after upload, avoiding the two manual configuration-creation forms. Human
template approval and rule activation remain required. Unit and frontend tests
cover mapping errors/limits, authorization changes, metadata-only reads, evidence,
decline, and onboarding. Rehearsal coverage joins the four backend shard reports.
