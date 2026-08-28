# Competitive baseline and claims register

**Claim owner:** Product & Commercial

**Evidence reviewed:** 2026-08-27

**Next scheduled review:** 2026-11-27, or earlier when a cited vendor page,
LawHand release gate, price, support commitment, or research source changes

**Scope:** public comparisons with Clio and Thomson Reuters legal products

This register replaces the June 2026 feature-scorecard memo. It is a claims
control, not a benchmark or a promise to match a vendor's entire bundle.
Vendor statements below are attributed to the vendor and were checked on the
review date. Plan, edition, jurisdiction, configuration, and licensed-content
differences still require deal-specific verification.

## Approved positioning

LawHand is a **unified matter operating system**: intake, matters, tasks,
documents, billing, connected sources, and AI-assisted work share a
tenant-isolated matter record. Its agents are **review first**: they prepare
work, expose sources and review states, and leave professional judgment and
external effects with an authorized person. Its research is
**source transparent**: a user can follow a supported claim to the retrieved
firm document or public authority used in the answer.

That positioning does not mean LawHand has more capable AI than every
incumbent, replaces Westlaw, supplies a citator, covers every jurisdiction, or
includes proprietary Westlaw, KeyCite, or Practical Law content. Proprietary
Thomson Reuters content is available only through a separately licensed or
approved partner integration; LawHand does not copy, bundle, or imply rights to
that content.

## Capability-state vocabulary

Every customer-facing capability must use one of these states. A code path or
roadmap item is not general availability.

| State | Meaning | Claim rule |
|---|---|---|
| **Implemented** | Code-backed behavior covered by repository evidence and available within its documented tenant/module configuration. | Describe the exact behavior and any configuration boundary. Do not infer an SLA, certification, or universal deployment entitlement. |
| **Controlled pilot** | Implemented or release-candidate behavior enabled only for selected tenants under an explicit rollout gate. | Say “controlled pilot” or “controlled onboarding”; do not say generally available. |
| **Planned** | Roadmap intent without accepted production evidence. | Keep off feature lists and sales promises. A date is not a commitment unless separately approved. |
| **Partner-dependent** | Behavior that requires a customer/provider account, license, consent, ingress proof, or approved commercial relationship. | Name the dependency. Never imply LawHand owns the provider's content, uptime, support, or certification. |

## LawHand baseline

| Capability | State as of 2026-08-27 | Substantiated boundary |
|---|---|---|
| Tenant-isolated matter record, contacts, parties, tasks, files, timelines, billing records, and review history | Implemented | The shared matter record is the product's operating spine. Individual modules and permissions still govern access. |
| Review-first chat, drafting, and proposed actions | Implemented | Tagged claims expose `cited`, `verify`, or `model` review states. Actions remain subject to the relevant approval and delivery controls. |
| Public-authority and firm-source retrieval | Controlled pilot | Results can link to retrieved CourtListener material and authorized firm sources. Coverage and currentness depend on the configured corpus; results are not citator treatment. |
| Full-platform tenant rollout, client portal, signature routing, specialized workspaces, Research MCP, and Workspace MCP | Controlled pilot | These surfaces require selected-tenant enablement, acceptance evidence, and the documented permission or consent flow. |
| Microsoft 365, Google Workspace, Teams, Zoom Phone, QuickBooks, Stripe, SMTP, and file-share connections | Partner-dependent | Each connection requires the applicable provider account, consent/scopes, production configuration, and provider availability. |
| Westlaw, KeyCite, Practical Law, or other proprietary legal content | Partner-dependent | License/partner only. No proprietary Thomson Reuters content ships in the LawHand public-authority corpus. |
| Rules-derived deadlines, citator-grade treatment, comprehensive authority coverage, maintained secondary guidance, general customer onboarding/import APIs, and native mobile apps | Planned | No public availability claim. Existing onboarding and import APIs remain platform-operator/internal only. |

## Dated official-vendor baseline

| Evidence | What the vendor page supports | Comparison constraint for LawHand |
|---|---|---|
| [Clio features](https://www.clio.com/features/) (reviewed 2026-08-27) | Clio presents practice management, intake/CRM, billing, payments, client communications/portal, document automation/e-signature/e-filing, mobile access, AI, integrations, support, and an uptime guarantee as parts of its platform. | Do not describe Clio as a point solution, AI-free, or missing ordinary practice-management breadth. Do not copy Clio's support or uptime statements into a LawHand claim. |
| [Clio Work](https://www.clio.com/work/) and [Clio Library](https://www.clio.com/work/ai-legal-research/) (reviewed 2026-08-27) | Clio markets matter-context AI for research, strategy, analysis, and drafting, backed by a large licensed library with citation-status signals and multi-jurisdiction content. | Retire categorical AI-superiority claims. LawHand's differentiator is workflow and source transparency, not an unsupported assertion of broader or better AI/legal content. |
| [Westlaw KeyCite](https://legal.thomsonreuters.com/en/products/westlaw/keycite) (reviewed 2026-08-27) | Thomson Reuters describes KeyCite as treatment/history/alert tooling used to evaluate whether authority remains valid, including Overruling Risk. | LawHand citation labels, links, reranking, and confidence cues are not KeyCite and do not determine good law. |
| [Westlaw Quick Check](https://legal.thomsonreuters.com/en/products/westlaw-edge/quick-check) (reviewed 2026-08-27) | Quick Check analyzes briefs for cited-authority warnings, quotation analysis, omitted relevant authority, and tables of authorities, with KeyCite integration. | Do not call LawHand review or drafting a Quick Check equivalent without a separately accepted comparison test. |
| [Practical Law features](https://legal.thomsonreuters.com/en/products/practical-law/features) (reviewed 2026-08-27) | Thomson Reuters describes attorney-editor-maintained guidance, templates, checklists, dynamic tools, and broad domestic/global content. | LawHand practice-area skills and customer templates are not maintained Practical Law content. Proprietary know-how is license/partner only. |
| [Westlaw Dockets and Court Wire coverage](https://legal.thomsonreuters.com/en/products/westlaw/dockets-coverage) (page lists update 2026-07-30; reviewed 2026-08-27) | Westlaw lists all U.S. district courts and many, but not all, state courts, with court-specific dates and product coverage. | Describe LawHand corpus coverage by named source, court, jurisdiction, and date. Never collapse partial source coverage into “nationwide” or “comprehensive.” |
| [CoCounsel Legal features](https://legal.thomsonreuters.com/en/products/cocounsel-legal/features) (reviewed 2026-08-27) | Thomson Reuters markets research, analysis, drafting, matter workspaces, integrations, and source-traceable outputs grounded in Westlaw and Practical Law. | Do not say established vendors lack matter-context or agentic AI. Compare only an accepted, dated workflow with like-for-like inputs and licenses. |

## Comparative claim register

Every comparative claim requires the owner and review date in this table before
it reaches a page, demo, proposal, or structured-data field.

| ID | Approved claim or boundary | Prohibited or required qualification | Owner | Reviewed |
|---|---|---|---|---|
| `COMP-CLAIM-01` | LawHand unifies the matter record and review-first work across intake, tasks, documents, billing, connected sources, and AI-assisted workflows. | Do not say competitors cannot connect these functions or that LawHand has every incumbent feature. | Product & Commercial | 2026-08-27 |
| `COMP-CLAIM-02` | LawHand exposes source links and explicit review states for supported AI-assisted claims. | Do not turn source transparency into a guarantee of accuracy, completeness, currentness, controlling authority, or good law. | Product & Commercial | 2026-08-27 |
| `COMP-CLAIM-03` | LawHand can be evaluated as a unified matter operating system with review-first agents. | No categorical “best AI,” “AI leader,” or blanket incumbent-superiority statement without a dated, reproducible, like-for-like acceptance result. | Product & Commercial | 2026-08-27 |
| `COMP-CLAIM-04` | LawHand public-authority research can complement a firm's licensed research workflow. | LawHand is not a Westlaw replacement. Westlaw, KeyCite, and Practical Law content and functionality remain separately licensed/partner-dependent. | Product & Commercial | 2026-08-27 |
| `COMP-CLAIM-05` | Coverage may be stated for a named source, jurisdiction, court, content type, and evidence date. | No “comprehensive,” “nationwide,” “all jurisdictions,” or equivalent coverage claim without a published coverage manifest and acceptance evidence supporting that exact scope. | Product & Commercial | 2026-08-27 |
| `COMP-CLAIM-06` | Citation links and treatment metadata help an attorney review research. | Do not say LawHand determines good law or offers citator-grade treatment until the citator acceptance program is complete. | Product & Commercial | 2026-08-27 |
| `COMP-CLAIM-07` | Contracted support, security, and availability terms may be stated exactly as approved for that deployment. | No public SLA, uptime guarantee, certification, support-hours, response-time, or service-level claim before the commitment is attained, documented, and approved. | Product & Commercial | 2026-08-27 |
| `COMP-CLAIM-08` | Operators can use internal onboarding/import paths within controlled rollout procedures. | Do not market or document those APIs as customer-facing, public, self-service, or a broader import platform. | Product & Commercial | 2026-08-27 |

## Publication gate

Before publishing or presenting a comparative statement:

1. Link it to a claim ID above or add a reviewed claim row.
2. Verify the cited official page on the publication date and record material
   plan, edition, jurisdiction, and licensing limits.
3. Match the LawHand side to repository evidence and its availability state.
4. Remove absolutes unless the exact scope is testable and the evidence proves
   it. Vendor marketing language remains attributed vendor language.
5. Have Product & Commercial approve pricing, support, SLA, certification,
   customer, and coverage statements. Security/Privacy must approve claims in
   its domain.
6. Re-review the claim by the scheduled date or immediately after a relevant
   vendor or LawHand release change.

The public capability catalog and README are the implementation sources for
LawHand's side of this register. Roadmap entries in `TASKS.md` are not marketing
evidence.
