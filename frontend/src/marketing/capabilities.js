/**
 * Reviewed public capability catalog for LawHand.
 *
 * The catalog is the single source for visible marketing capability copy and
 * SoftwareApplication structured data. Each item carries a customer-claim
 * state, owner, and review date. Planned items stay in the catalog so product
 * and commercial reviewers can see the boundary, but `public: false` keeps
 * them out of pages and structured data.
 *
 * Canonical evidence: README.md and docs/competitive-gap-analysis.md.
 */

export const CAPABILITY_STATES = Object.freeze({
  implemented: 'Implemented',
  'controlled-pilot': 'Controlled pilot',
  planned: 'Planned',
  'partner-dependent': 'Partner-dependent',
})

export const CAPABILITY_CATALOG_REVIEW = Object.freeze({
  owner: 'Product & Commercial',
  reviewedAt: '2026-08-27',
  nextReviewAt: '2026-11-27',
})

const reviewedCapability = (capability) => Object.freeze({
  claimOwner: CAPABILITY_CATALOG_REVIEW.owner,
  reviewedAt: CAPABILITY_CATALOG_REVIEW.reviewedAt,
  public: true,
  ...capability,
})

export const CAPABILITY_CATALOG = Object.freeze([
  reviewedCapability({
    id: 'crm',
    icon: 'Users',
    name: 'Client and matter CRM',
    availability: 'implemented',
    summary:
      'Matters, contacts, parties, notes, assignments, budgets, timelines, communications, and matter files in one tenant-isolated record.',
    availabilityNote: 'Available within the firm\'s configured modules and permissions.',
  }),
  reviewedCapability({
    id: 'intake',
    icon: 'PhoneIncoming',
    name: 'Caller intake and Zoom Phone',
    availability: 'controlled-pilot',
    summary:
      'Manual and Zoom Phone intake with caller matching, call-history sync, notes, lead and task handoff, and CSV export.',
    availabilityNote: 'Selected-tenant rollout; Zoom requires a verified provider connection.',
  }),
  reviewedCapability({
    id: 'conflicts',
    icon: 'FileSearch',
    name: 'Saved conflict reviews',
    availability: 'controlled-pilot',
    summary:
      'Search clients, contacts, and matters; preserve the terms and results the reviewer saw; record notes and a decision; and warn on restricted matches without exposing the protected matter.',
    availabilityNote: 'Selected-tenant rollout; a saved search supports review but does not replace the firm\'s conflicts policy.',
  }),
  reviewedCapability({
    id: 'tasks',
    icon: 'ListChecks',
    name: 'Tasks, deadlines, and calendar',
    availability: 'implemented',
    summary:
      'Tenant-scoped tasks with assignees, matter and contact links, priorities, deadlines, reminders, reassignment, and close reasons.',
    availabilityNote: 'Stores and projects reviewed deadlines; it does not calculate court-rule deadlines.',
  }),
  reviewedCapability({
    id: 'communications',
    icon: 'Inbox',
    name: 'Matter communications and email',
    availability: 'controlled-pilot',
    summary:
      'Keep communication history on the matter, review forwarded mail before filing it, and turn explicitly tagged email into a traceable task or reviewed deadline handoff.',
    availabilityNote: 'Selected-tenant rollout; mailbox and forwarding behavior depends on the configured connection.',
  }),
  reviewedCapability({
    id: 'client-portal',
    icon: 'UserCheck',
    name: 'Client portal',
    availability: 'controlled-pilot',
    summary:
      'Give clients a focused view of unread messages, shared documents, upcoming dates, signature requests, invoices, balances, and the actions that need them.',
    availabilityNote: 'Selected-tenant rollout with matter-scoped invitations and access controls.',
  }),
  reviewedCapability({
    id: 'documents',
    icon: 'FileText',
    name: 'Document preparation and automation',
    availability: 'controlled-pilot',
    summary:
      'DOCX and TXT template analysis with variable substitution, retained PDF sources with AcroForm field mapping, review preview, flattened output, and filing back to the matter.',
    availabilityNote: 'Selected-tenant rollout with review and supported-input gates.',
  }),
  reviewedCapability({
    id: 'signature',
    icon: 'FileSignature',
    name: 'Signature routing and follow-through',
    availability: 'controlled-pilot',
    summary:
      'Route internal signature requests, track delivery and first view, progress sequential signers, schedule reminders, and resend manually when follow-up is needed.',
    availabilityNote: 'Selected-tenant rollout; signature scope and enforceability remain subject to the firm\'s workflow and applicable law.',
  }),
  reviewedCapability({
    id: 'billing',
    icon: 'Receipt',
    name: 'Time, invoicing, and trust accounting',
    availability: 'controlled-pilot',
    summary:
      'Time and expense capture, invoices, payments, retainers, LEDES export, and optional Stripe payment flows.',
    availabilityNote: 'Selected-tenant rollout; payment processing is partner-dependent.',
  }),
  reviewedCapability({
    id: 'research',
    icon: 'Search',
    name: 'Source-linked legal research',
    availability: 'controlled-pilot',
    summary:
      'Firm document retrieval and configured public CourtListener authority returned with citation review states so an attorney can verify before relying on an answer.',
    availabilityNote: 'Coverage depends on the configured corpus; this is not citator treatment or a good-law determination.',
  }),
  reviewedCapability({
    id: 'chat',
    icon: 'MessageSquareText',
    name: 'Matter-aware AI chat and drafting',
    availability: 'controlled-pilot',
    summary:
      'Ask, review, summarize, and draft against the active matter, with every tagged claim labeled cited, verify, or model.',
    availabilityNote: 'Selected-tenant rollout; outputs remain subject to professional review.',
  }),
  reviewedCapability({
    id: 'skills',
    icon: 'Layers3',
    name: 'Practice-area skill libraries',
    availability: 'controlled-pilot',
    summary:
      'Commercial, litigation, corporate, employment, privacy, IP, regulatory, real estate, and criminal defense skills, plus dedicated trust and estate, domestic relations, and mediation workspaces.',
    availabilityNote: 'Enabled per firm during controlled onboarding; skills are not maintained proprietary legal treatises.',
  }),
  reviewedCapability({
    id: 'integrations',
    icon: 'Plug',
    name: 'Microsoft 365, Google, and QuickBooks',
    availability: 'partner-dependent',
    summary:
      'Administrator-enabled Microsoft 365, Google Workspace, Microsoft Teams, Zoom Phone, QuickBooks Online, SMTP, and enterprise file-share connections that can be disconnected at any time.',
    availabilityNote: 'Requires the provider account, consent, configuration, and production proof applicable to the connection.',
  }),
  reviewedCapability({
    id: 'mcp',
    icon: 'Braces',
    name: 'MCP for approved AI assistants',
    availability: 'controlled-pilot',
    summary:
      'Workspace MCP exposes tenant-scoped matter work to approved assistants over OAuth 2.1, and Research MCP retrieves configured public legal authority only.',
    availabilityNote: 'Disabled by default and enabled only for approved tenants, users, scopes, and Research billing.',
  }),
  reviewedCapability({
    id: 'controls',
    icon: 'ShieldCheck',
    name: 'Tenant isolation and review controls',
    availability: 'implemented',
    summary:
      'Per-firm tenant isolation, module and role authorization, scoped platform-operator sessions, and application-encrypted stored credentials.',
    availabilityNote: 'Security behavior, not a certification or service-level promise.',
  }),
  reviewedCapability({
    id: 'licensed-research-content',
    icon: 'Library',
    name: 'Licensed proprietary legal content',
    availability: 'partner-dependent',
    public: false,
    summary:
      'Westlaw, KeyCite, Practical Law, and comparable proprietary content may be used only through a separately approved license or partner relationship.',
    availabilityNote: 'No proprietary Thomson Reuters content ships in the LawHand public-authority corpus.',
  }),
  reviewedCapability({
    id: 'court-rules-citator',
    icon: 'Scale',
    name: 'Court-rule deadlines and citator-grade treatment',
    availability: 'planned',
    public: false,
    summary:
      'Rules-derived deadlines, comprehensive coverage manifests, treatment history, and good-law determinations remain roadmap work.',
    availabilityNote: 'Not available and not approved for customer claims.',
  }),
  reviewedCapability({
    id: 'customer-import-api',
    icon: 'Upload',
    name: 'Customer self-service onboarding and import APIs',
    availability: 'planned',
    public: false,
    summary:
      'A customer-facing onboarding or bulk-import API is not available.',
    availabilityNote: 'Existing provisioning, onboarding, and import paths remain platform-operator/internal only.',
  }),
])

/** Capabilities eligible for public pages and structured data. */
export const CORE_CAPABILITIES = Object.freeze(
  CAPABILITY_CATALOG.filter((capability) => capability.public),
)

/** Non-public boundaries retained for claim review and automated checks. */
export const NON_PUBLIC_CAPABILITIES = Object.freeze(
  CAPABILITY_CATALOG.filter((capability) => !capability.public),
)

/** Short capability labels, in page order, for structured data `featureList`. */
export const CORE_CAPABILITY_NAMES = Object.freeze(
  CORE_CAPABILITIES.map((capability) => capability.name),
)
