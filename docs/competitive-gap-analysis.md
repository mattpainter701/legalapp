# Competitive Gap Analysis — Core Case & Client Management

**Date:** 2026-06-05
**Scope:** Where Clarity Legal stands against the incumbent legal practice-management
suites on the *core* of the product — matter (case) management and client
management — and what to build to close the gap. AI/research differentiation is
treated as our moat, not the subject here.

---

## 1. Executive summary

Clarity Legal already has an unusually deep **practice-management spine** for an
AI-first entrant: firm-wide matters with assignments/parties/notes/budgets/key
dates, contacts + intake pipeline, time/expense/invoice/payment billing with
LEDES + UTBMS, retainers, recurring billing, trust-accounting *backend*, tasks,
an aggregated deadline calendar, a communications log, and document templates.
On AI (RAG, 11 practice-area plugins, MCP, cloud-search architecture) we are
ahead of every incumbent.

The gaps are **not** in breadth of records — they are in the **client-facing and
operational connective tissue** that incumbents have spent a decade hardening,
and that buyers treat as table stakes before they will switch a whole firm onto
us:

| Severity | Gap | Who has it |
|-|-|-|
| **P0 — blocks switching** | No client portal | Clio, MyCase, PracticePanther, CARET, Smokeball |
| **P0 — blocks switching** | No native e-signature | Clio, MyCase, PracticePanther |
| **P0 — compliance** | Trust accounting has no UI / no 3-way reconciliation | CosmoLex, CARET, Clio, MyCase |
| **P1 — litigation deal-breaker** | No court-rules-based deadline/docketing engine | Clio (CalendarRules), all via LawToolBox |
| **P1 — intake conversion** | No public intake forms or online consult scheduling | Clio Grow, MyCase, PracticePanther |
| **P1 — client comms** | No two-way SMS/text messaging | Clio, MyCase, PracticePanther |
| **P1 — efficiency** | No no-code workflow/automation builder | PracticePanther, Filevine, Smokeball |
| **P2 — depth** | Thin contact model; text-only document automation; no native mobile app; basic reporting/BI | Tabs3/PracticeMaster, CARET, Smokeball |

**Recommendation:** spend the next two quarters bolstering the *standard* tier
(the flat-subscription practice-management core) with a **Client Portal +
E-signature + Trust reconciliation + Rules-based calendaring** package. These
four are the most-cited reasons a firm picks Clio/MyCase over a point solution,
and each plugs cleanly into models we already have.

---

## 2. Competitor landscape

| Product | Positioning | Core strength | Where they're weak |
|-|-|-|-|
| **Clio (Manage + Grow + Accounting)** | Market leader, broadest ecosystem (200+ integrations) | Client portal, CalendarRules docketing, Feb-2026 native GL accounting, 50-state court rules | AI is bolt-on; expensive once you add Grow/Accounting tiers |
| **PracticePanther** | Best value for budget small firms | Workflow automation, native payments, intake forms, e-sign | Shallower reporting; less litigation depth |
| **Tabs3 + PracticeMaster** | Trusted on-prem/hybrid incumbent (now Tabs3 Cloud) | Matter Manager 360° view, best-in-class conflict checker (partial/phonetic), email auto-filing, document assembly | Dated UX; AI essentially absent |
| **MyCase** | Solo/small-firm simplicity | Built-in 2-way text, unlimited e-sign + intake (Pro), legal accounting | Less customizable; scales poorly to mid-size |
| **CARET Legal** (ex-Zola) | All-in-one with built-in accounting + BI | Native GL accounting, analytics, LawToolBox calendaring | Smaller ecosystem |
| **Smokeball** | Document-heavy & high-volume practices | Automatic passive time capture, deep Word document automation | Desktop-anchored; pricey |
| **Filevine** | Large firms / PI / mass tort | Workflow automation, project management at scale | Overkill + cost for SMB; complex |

---

## 3. Feature matrix — core case & client management

✅ have · 🟡 partial / backend-only · ❌ missing

| Capability | Clarity | Clio | PPanther | Tabs3/PM | MyCase | CARET | Smokeball |
|-|-|-|-|-|-|-|-|
| Firm-wide matter records (assignments, parties, notes, budget, key dates) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Contacts / CRM | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Lead intake pipeline + lead→matter conversion | ✅ | ✅ (Grow) | ✅ | 🟡 | ✅ | ✅ | ✅ |
| Conflict checking | 🟡 cross-matter service | ✅ | ✅ | ✅ best-in-class | ✅ | ✅ | ✅ |
| Time / expense / invoice / payment billing | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| LEDES / UTBMS | ✅ | ✅ | 🟡 | ✅ | 🟡 | ✅ | 🟡 |
| Retainers + recurring billing | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Trust / IOLTA accounting | 🟡 backend only, no UI, no reconciliation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Three-way trust reconciliation | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Client portal** (docs, messages, invoices, pay) | ❌ (mediation portal only) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Native e-signature** | ❌ | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ |
| **Two-way SMS / text** | ❌ | ✅ | ✅ | ❌ | ✅ | 🟡 | 🟡 |
| **Court-rules deadline / docketing** | ❌ (manual key dates) | ✅ | ✅ (LawToolBox) | ✅ | ✅ | ✅ | ✅ |
| **Public intake forms (conditional)** | ❌ (internal only) | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ |
| **Online consult scheduling / booking** | ❌ | ✅ (Grow) | ✅ | ❌ | ✅ | ✅ | 🟡 |
| **No-code workflow / automation builder** | 🟡 recurring-billing scheduler only | ✅ | ✅ | 🟡 | 🟡 | ✅ | ✅ |
| Document automation / assembly | 🟡 text templates, `{{var}}` only | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ best |
| Email-to-matter filing | 🟡 email agent, no auto-file | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Native mobile apps (iOS/Android) | ❌ responsive web | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Reporting / BI depth | 🟡 basic dashboards | ✅ | ✅ | ✅ | ✅ | ✅ best | ✅ |
| AI research / RAG / agentic | ✅ best-in-class | 🟡 | 🟡 | ❌ | 🟡 | 🟡 | 🟡 |
| Practice-area plugins (11) | ✅ unique | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 4. Gap detail & why it matters

### P0 — table-stakes for switching a whole firm

**4.1 Client Portal.** Every major incumbent ships a secure portal where the
client logs in to see matter status, exchange messages, upload/download
documents, view invoices, and pay. We have the *plumbing* for a portal —
mediation already has `mediation_portal.py` + `PortalAcceptPage`/`PortalCasePage`
and an invite model — but nothing general for matters/clients. This is the single
most common "why we left a point tool for Clio" reason. **Reuse:** generalize the
mediation portal pattern over `Matter`, `Contact`, `MatterDocument`, `Invoice`,
and `CommunicationLog`.

**4.2 Native e-signature.** Engagement letters, retainer agreements, and consents
need in-product signing. Clio/MyCase/PracticePanther all do this natively (often
bundled). We render templates but cannot route a document for signature. **Reuse:**
sits on top of `DocumentTemplate` → `MatterDocument`; add a signature-request
entity + a provider (DocuSign/Dropbox Sign API, or a lightweight built-in).

**4.3 Trust accounting UI + three-way reconciliation.** `trust_accounting.py`
(9 endpoints, `TrustAccount` + `TrustTransaction`, migration 017) is fully built
but **headless** — no pages, no `api.js` functions, no routes (see TASKS BK05).
And there is no three-way reconciliation (bank ↔ trust ledger ↔ sum of client
ledgers), which bar regulators treat as mandatory. This is a compliance blocker
for any firm holding client funds. **Reuse:** build the frontend for the existing
backend; add a reconciliation report + per-client trust ledger view.

### P1 — high-impact differentiators

**4.4 Court-rules-based deadline engine.** We store `key_dates` as free JSON and
aggregate them on the calendar, but we cannot *calculate* deadlines from a
triggering event using jurisdiction court rules (e.g., "answer due 21 days after
service"). Clio acquired CalendarRules; everyone else integrates LawToolBox
(50 states, 2,300+ jurisdictions). For litigation firms this is a malpractice-risk
deal-breaker. **Options:** integrate LawToolBox API (fast), or build a rules
engine seeded from our CourtListener pipeline (slower, more defensible, on-brand).

**4.5 Public intake forms + online scheduling.** Our intake pipeline is
*internal* — staff create leads. Competitors capture leads via public,
practice-area-specific forms with conditional logic that auto-create a `Lead`/
`Contact`, plus online consult booking. This is top-of-funnel revenue. **Reuse:**
a public form builder that writes into the existing `Lead`/`Contact` models;
scheduling can wrap our calendar + Google/Microsoft availability we already sync.

**4.6 Two-way SMS/text.** Clients increasingly expect texting; conversations
should thread into the matter. We have a `CommunicationLog` but no SMS channel.
**Reuse:** add a Twilio channel that writes inbound/outbound into
`CommunicationLog` with matter/contact linkage.

**4.7 No-code workflow automation.** PracticePanther markets "8 hrs/week saved"
via triggers that auto-create tasks/events (e.g., on matter open → spawn a task
checklist). We only have the recurring-billing scheduler. **Reuse:** a trigger →
action engine over Matter/Task/CommunicationLog/Template events; this also
pairs naturally with our AI to *suggest* the next action.

### P2 — depth & polish

- **Contact model depth:** no custom fields, no contact↔contact relationships
  (company ↔ people, related parties), no per-practice-area custom matter fields.
  Incumbents lean on customization heavily.
- **Document automation:** templates are text-only with `{{var}}` substitution.
  Native DOCX/PDF assembly with field mapping is already in TASKS "Future" — that
  closes most of the Smokeball/Tabs3 gap.
- **Email-to-matter filing:** auto-file inbound/outbound email to the right matter
  (and include in conflict search, as PracticeMaster does).
- **Native mobile apps:** we're responsive-web only; incumbents have iOS/Android.
- **Reporting/BI:** our dashboards are basic vs CARET/Clio analytics.

---

## 5. What we already win on (protect the narrative)

- **AI-native research** grounded in tenant docs + CourtListener case law with
  confidence-tagged citations — no incumbent matches this.
- **11 practice-area plugins** with structured skill prompts and compliance gates.
- **MCP** — connect Claude/Cursor/custom agents to the firm's knowledge base.
- **Privacy-first cloud-search architecture** — search the customer's own
  Drive/Graph at query time, store only routing metadata. Strong enterprise/
  in-house selling point.
- **Trust & Estate + Mediation modules** — depth most SMB suites lack.

The strategy is **not** to out-feature Clio everywhere — it's to reach
*table-stakes parity on the practice-management core* so the AI moat can win the
deal instead of being disqualified on a checklist.

---

## 6. Recommended "Core Standard Bolster" roadmap

Sequenced to maximize switchability of the flat-subscription **standard** tier.
Each item maps to models/routers that already exist.

### Sprint A — Client-facing core (P0)
1. **Client Portal** — generalize mediation portal over Matter/Contact/
   MatterDocument/Invoice/CommunicationLog. Client auth via existing invite +
   short-lived code pattern. *(LARGE)*
2. **Native e-signature** — signature-request entity on MatterDocument; provider
   adapter (Dropbox Sign / DocuSign) + status webhooks. *(MEDIUM)*
3. **Trust accounting frontend + three-way reconciliation** — UI for the existing
   headless backend; reconciliation report; per-client trust ledger. *(MEDIUM)*

### Sprint B — Intake & litigation core (P1)
4. **Public intake forms + online scheduling** — form builder → Lead/Contact;
   consult booking over synced calendars. *(LARGE)*
5. **Court-rules deadline engine** — start with LawToolBox API integration into
   `key_dates`/calendar; evaluate CourtListener-seeded native engine later. *(MEDIUM)*
6. **Two-way SMS** — Twilio channel into CommunicationLog. *(SMALL–MEDIUM)*

### Sprint C — Efficiency & depth (P1/P2)
7. **Workflow automation builder** — trigger→action over matter/task events;
   AI-suggested next actions. *(LARGE)*
8. **Document automation overhaul** — native DOCX/PDF assembly w/ field mapping
   (already in TASKS "Future"). *(MEDIUM)*
9. **Contact/matter custom fields + contact relationships.** *(MEDIUM)*
10. **Email-to-matter auto-filing** (+ include in conflict search). *(MEDIUM)*

### Later
- Native mobile apps; reporting/BI depth; built-in GL accounting (vs current QBO
  integration) if we want to displace Clio Accounting/CosmoLex.

---

## 7. Sources

- [Clio Manage](https://www.clio.com/manage/) · [Clio client portal](https://www.clio.com/features/legal-client-portal-software/) · [Clio rules-based calendaring](https://www.clio.com/blog/rules-based-calendaring-software-law-firms/)
- [PracticePanther case management](https://www.practicepanther.com/case-management/) · [workflows](https://www.practicepanther.com/case-management/workflows/) · [legal calendaring](https://www.practicepanther.com/legal-calendaring/)
- [Tabs3 PracticeMaster features](https://www.tabs3.com/products/practicemaster/practicemaster_features.html)
- [MyCase legal calendaring](https://www.mycase.com/features/legal-calendaring/) · [MyCase accounting](https://www.mycase.com/features/legal-accounting-software/) · [MyCase comparisons](https://www.mycase.com/comparison/)
- [CARET Legal calendaring](https://caretlegal.com/case-management/legal-calendaring/)
- [LawToolBox key differentiators](https://lawtoolbox.com/features/key_differentiators/)
- [Clio vs MyCase intake automation](https://ustechautomations.com/resources/blog/automate-law-firm-client-intake-2026)
- [CosmoLex trust accounting](https://www.cosmolex.com/features/trust-accounting/) · [ABA IOLTA compliance guide](https://www.americanbar.org/groups/law_practice/resources/law-technology-today/2024/a-guide-to-ensuring-iolta-account-compliance/) · [Three-way reconciliation (Above the Law)](https://abovethelaw.com/2025/07/the-law-firms-guide-to-trust-accounting-and-three-way-reconciliation/)
- [Case management comparison 2026 (My Legal Academy)](https://mylegalacademy.com/kb/case-management-software-comparison-2026)
</content>
</invoke>
