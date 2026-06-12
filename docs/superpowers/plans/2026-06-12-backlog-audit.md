# Backlog Audit & Refresh Plan

> **For agentic workers:** This is a *review* plan, not an implementation plan. Use superpowers:subagent-driven-development to audit each item in parallel. Each task gathers information, assesses staleness, and produces a decision (Archive / Refine → Promote / Keep as-is).

**Goal:** Determine which of 7 underscoped backlog items (1304, 1305, 1307, 1308×2, BK05, Future) are stale/obsolete/still relevant, and decide: archive vs refine vs promote.

**Status:** COMPLETED 2026-06-12

---

## Summary of Decisions

| Item | Decision | Action |
|------|----------|--------|
| **1304** | REFINE | Split into Phase 1 (public form → auto-lead, small–medium) + Phase 2 (scheduling/branching, deferred) |
| **1305** | SPIKE | 1-day spike → contact LawToolBox sales (pricing, coverage, TTM) → schedule Phase 1 or defer |
| **1307** | PARK | Revisit after 1301–1306 stabilize. Spec vague, user demand indirect. |
| **1308a** | PROMOTE P1 | Document Automation: DOCX upload + field mapping + render (python-docx). |
| **1308b** | PROMOTE P1 | Reporting/BI: 3 core reports (realization, WIP, A/R aging). Data foundation ready. |
| **BK05** | PROMOTE → 1314 | Trust Accounting Frontend for Sprint 14. Backend 9/9 endpoints complete. |
| **Future** | ARCHIVE | Mobile apps archived with Q4 2026 revisit trigger (customer demand ≥3 requests or >15% iOS/Android traffic). |

---

## Findings by Task

### Task 1: Audit 1304 (Public Intake Forms)

**Context:** Sprint 13 M2, incomplete design on conditional logic scope.

**Finding:** Spec conflates three features: form builder + conditional logic + online scheduling. Scope split:
- **Phase 1 (small–medium):** Public form → auto-create Lead + notify attorney. No branching, no scheduling.
- **Phase 2 (medium–large, deferred):** Conditional field visibility + online calendar scheduling.

**User demand:** Competitive gap (listed in core-bolster analysis). Zero explicit customer requests.

**Decision:** REFINE — Keep Phase 1 (ships P1, closes competitive gap quickly). Defer Phase 2.

---

### Task 2: Audit 1305 (Court-Rules Deadline / Docketing Engine)

**Context:** Sprint 13 M2, marked "later: evaluate native engine". Decision-gated work.

**Finding:**
- **Phase 1 (not started):** LawToolBox API integration. Path is clear, effort moderate (1–2 weeks). Unblocked by external dependency (LawToolBox commercial API).
- **Phase 2 (speculative):** Native CourtListener engine. Design incomplete, high effort, no customer pull.

**User demand:** Deal-breaker feature (litigation firms lose prospects without court-rules deadlines), but no customer complaints yet.

**Decision:** CONVERT TO SPIKE — 1-day spike to confirm LawToolBox pricing/availability before scheduling Phase 1. Archive Phase 2 (speculative optimization).

---

### Task 3: Audit 1307 (No-Code Workflow Automation)

**Context:** Sprint 13 M3, "domain events → actions" design incomplete.

**Finding:** Spec is vague (events undefined, actions undefined, UI style unknown). Existing ad-hoc automations in codebase (billing scheduler, calendar sync). User demand indirect (competitive gap analysis, no interviews). Competing P0/P1 tasks on critical path (1304, 1305, 1306) take priority.

**Decision:** PARK — Revisit after 1301–1306 land and gather user interviews. Propose narrow Phase 1 scope (manual-only task auto-creation) if demand emerges.

---

### Task 4: Audit 1308a (Document Automation)

**Context:** Sprint 13 M3, "native DOCX/PDF assembly with field mapping" entirely undefined.

**Finding:** User demand exists (Tabs3/Smokeball parity, explicitly in competitive gap analysis). Existing text templates (v0.8.0) as foundation. Scope clear:
- **Phase 1:** DOCX upload → extract fields → map to variables → render (python-docx, 2 weeks).
- **Phase 2:** Conditionals + PDF (Jinja2 + pandoc, later).

**Decision:** PROMOTE TO P1 (was P2) — User demand clear, tooling clear, existing foundation. Phase 1 scope small-medium.

---

### Task 5: Audit 1308b (Reporting/BI)

**Context:** Sprint 13 M3, "realization/collection, WIP, A/R aging, matter profitability" no schema/UX.

**Finding:** Data foundation complete (TimeEntry, Invoice, Payment models indexed, all fields present). Zero billing reports frontend. User demand implicit (accounting parity, backlog priority P2). 3 core reports (realization, WIP, A/R aging) can ship in Phase 1 (3–4 days backend + 2 days frontend).

**Decision:** PROMOTE TO P1 (was P2) — Data foundation ready, scope clear, accounting team needs it. Phase 1 SQL queries + REST endpoints + table UI.

---

### Task 6: Audit BK05 (Trust Accounting Frontend)

**Context:** Marked "gap" in 1303, backend implemented but "headless" since Sprint 12.

**Finding:** Backend COMPLETE (9 REST endpoints in `trust_accounting.py`, migration 017, reconciliation logic all implemented). Frontend greenfield, modest scope (~1 week: portfolio view, detail page, reconciliation screen, matter integration). Sprint 13 explicitly calls this "headless backlog item".

**Decision:** PROMOTE TO SPRINT 14 AS 1314 — Backend ready for consumption. No reason to defer. Effort fits in 2-week sprint with other work.

---

### Task 7: Audit Future (Mobile Apps)

**Context:** Tagged "deferred (XL)", no revisit trigger.

**Finding:** No customer demand signal. Competitive pressure rated P2 (not a deal-breaker). Responsive web (task 1110, Sprint 12) covers table-stakes mobile UX. P0/P1 roadmap (client portal, e-sig, trust accounting, docketing, intake, SMS, workflows) takes priority. Scope: 8–12 weeks for 1 developer (React Native/Flutter MVP).

**Decision:** ARCHIVE with conditional revisit trigger — Revisit Q4 2026 if: (1) ≥3 customer requests cite iOS/Android as blocker, (2) major competitor mobile-exclusive feature ships, (3) analytics show >15% iOS/Android traffic, or (4) team has post-Sprint-14 capacity.

---

## Impact on TASKS.md

All decisions committed to TASKS.md (2026-06-12):
- 1304 spec split into Phase 1 + Phase 2
- 1305 converted to spike + phase schedule clarified
- 1307 marked PARKED with revisit condition
- 1308 split into 1308a (P1) + 1308b (P1) + 1308c/d (future)
- BK05 updated to reference new 1314
- Sprint 14 section added with 1314 (Trust Accounting Frontend)
- Future section updated with mobile apps archive + revisit trigger
