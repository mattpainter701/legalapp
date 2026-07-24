# Authenticated Platform UX Review

Date: 2026-07-23
Scope: authenticated application shell, assistant, and representative core, finance, reporting, administrative, and add-on module surfaces

## Executive summary

The platform has a coherent visual foundation and several strong workflow-specific surfaces, especially Matters, Tasks, and the intake dashboard. The largest usability issue is not missing capability; it is interaction drift. Similar page-level actions, filters, loading states, tables, and responsive behaviors are implemented differently from module to module.

The assistant had a separate set of high-impact issues:

- model and public-source controls were unavailable on compact screens;
- the “LEGAL-SAFE” label could imply a level of assurance the product should not claim;
- conversation history and reusable source documents competed in one long rail;
- the mobile rail lacked complete modal and focus behavior;
- the empty state described the product but did not efficiently start real work;
- the composer was visually dense and attachment, prompt, send, and review information competed for space.

This PR fixes those assistant issues and introduces reusable workspace primitives. Contacts, Tasks, Time Tracking, and Invoices now demonstrate the intended module-page pattern across core and finance workflows.

## Review principles

1. Lead with the user’s current work, not generic dashboard chrome.
2. Keep the primary action and current state visible without making every action equally prominent.
3. Use the same interaction for the same job across modules.
4. Replace horizontal-scroll-only mobile tables with task-oriented summaries where practical.
5. Make empty, loading, error, and filtered-empty states actionable.
6. Preserve keyboard, touch, focus, and screen-reader behavior as part of the interaction design.
7. Describe AI capability precisely and keep attorney review expectations visible.

## Findings and disposition

| Priority | Finding | User impact | Disposition |
|---|---|---|---|
| P0 | Assistant response settings were hidden on phone layouts. | Mobile users could not choose the model or public case-law setting. | Fixed with a single responsive response-settings panel. |
| P0 | “LEGAL-SAFE” overstated the assurance provided by generated work. | Could be interpreted as a product or legal-safety guarantee. | Replaced with “Review required” and clearer verification language. |
| P1 | Assistant history and source-library tasks were mixed in one scrolling rail. | Conversations became harder to scan as reusable documents accumulated. | Split into Conversations and Sources tabs with separate counts and empty states. |
| P1 | The assistant’s mobile drawer did not fully behave as a modal. | Focus and page scrolling could escape the drawer. | Added dialog semantics, focus containment, Escape handling, scroll locking, and focus restoration. |
| P1 | The empty assistant state was descriptive instead of task-oriented. | Users had to invent a prompt before seeing value. | Added practical Review, Draft, and Chronology starters. |
| P1 | Composer controls competed for limited width. | Compact layouts felt cramped and prompt suggestions were hidden behind a dropdown. | Rebuilt the composer around an expandable input, visible prompt chips, clearer attachments, and responsive controls. |
| P1 | Module headers, filters, metrics, and state panels use several unrelated patterns. | Users must relearn page structure and action placement between modules. | Added shared workspace header, page, filter, segmented-control, metric, spinner, alert, and empty-state primitives. |
| P1 | Time Tracking and Invoices relied heavily on inline desktop table styling. | These finance workflows were visually disconnected and required horizontal scrolling on phones. | Rebuilt both with the shared pattern and mobile task cards while preserving desktop tables. |
| P2 | App-shell titles are frequently repeated by module-local headers. | Reduces usable vertical space and weakens hierarchy. | Follow-up: define when the shell owns the title versus when a module needs a local workflow header. Avoid changing this in parallel with the navigation-shell PR. |
| P2 | Loading and error states vary from full-screen text to silent API failure. | Users cannot consistently tell whether to wait, retry, or change course. | Improved in migrated modules. Remaining modules should adopt the shared state pattern. |
| P2 | Several large pages combine many independent workflows in one component. | Makes interaction consistency and regression coverage harder to maintain. | Follow-up: split by workflow boundary while migrating each surface. |

## Module observations

### Matters

The portfolio board, list view, filters, metrics, and actionable empty states are a strong reference for other modules. Remaining issues are duplicated page chrome and high information density on smaller tablets. The matter detail page should be reviewed separately because its scope and number of workflows make it an information-architecture project rather than a styling pass.

### Tasks

Task grouping and workflow-specific actions are strong. This pass aligns its page container, primary action, and filter surface with the shared pattern. A later pass should consider saved views such as “Assigned to me,” “Due this week,” and “Waiting on client.”

### Contacts

The core list is understandable and its filtered-empty state is useful. This pass adds clearer module framing, search/filter labels, and shared page structure. The create-contact dialog still needs the same modal accessibility standard used by the assistant drawer and confirmation system.

### Calendar

The agenda presentation is effective, but the header contains event creation, provider connection, synchronization, count, month navigation, and legend controls in a small vertical area. A focused calendar PR should group provider health separately from daily scheduling actions and create a compact mobile month/agenda control.

### Communications

The split layout is appropriate for high-volume logs. Raw Matter ID and Contact ID filters are implementation-oriented; they should become searchable entity pickers. Outlook and Gmail sync actions should report last successful sync and consolidate provider state.

### Time Tracking

This pass replaces inline styling with the shared workspace pattern, adds the missing visible date field to manual entry, makes the running timer the dominant temporary state, improves error handling, and provides mobile entry cards.

### Invoices

This pass adds visible outstanding, overdue, and draft metrics; clearer draft-generation feedback; recoverable load errors; responsive status filters; and mobile invoice cards. Invoice detail and payment actions should receive a separate workflow review.

### Reports

Report content is useful, but loading and error states occupy a full viewport and the page introduces another bespoke header/tab system. It should adopt the shared module header and segmented control, followed by clearer “what changed” and comparison-period context.

### Document Automation

The feature set is deep enough that progressive disclosure matters more than visual polish. The next review should separate source-template management, generation, preview, and matter-save tasks, then ensure the user always knows which source, matter, and output format are active.

### Add-on Modules

Portfolio pages generally follow the product’s stronger card/list conventions. Detail pages vary considerably by domain. Shared page and state primitives should be adopted without flattening domain-specific workflows into a generic dashboard.

### Administration and Platform

These surfaces are powerful but dense and contain many locally implemented tabs, forms, and tables. They should be reviewed as operator tools with explicit danger levels, save state, validation summaries, and responsive expectations. They are intentionally out of this user-workflow PR.

## Recommended follow-up sequence

1. Calendar and Communications: compact scheduling controls, entity-based filters, and integration health.
2. Matter Detail and Document Automation: information architecture, progressive disclosure, and clearer active context.
3. Reports, Invoice Detail, and Trust Accounting: consistent financial states and comparison context.
4. Administration and Platform: operator-focused navigation, save feedback, validation, and destructive-action hierarchy.
5. Modal and form accessibility sweep: migrate remaining custom dialogs and field patterns to shared behavior.

## Validation expectations

Every follow-up should include:

- phone, tablet, and desktop layout checks;
- keyboard-only completion of the primary workflow;
- focus containment and restoration for overlays;
- explicit loading, error, initial-empty, and filtered-empty coverage;
- interaction tests for shared primitives and any workflow-specific state;
- the standard frontend lint, test, and production build gate.
