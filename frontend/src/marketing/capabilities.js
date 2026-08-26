/**
 * Public capability list for the LawHand platform.
 *
 * This is the single source of truth for "what LawHand is" in every public
 * surface: the home-page capability grid, the crawler-visible no-JavaScript
 * shells, and the `featureList` published in SoftwareApplication structured
 * data. A search engine, an AI answer engine, and a visitor must all read the
 * same list, so a capability can never be advertised in structured data
 * without also being visible on the page.
 *
 * Every entry must be substantiated by shipped behavior described in
 * README.md ("What is in the product"). Release-gated surfaces state their gate
 * in the summary rather than being described as generally available.
 */
export const CORE_CAPABILITIES = Object.freeze([
  Object.freeze({
    id: 'crm',
    icon: 'Users',
    name: 'Client and matter CRM',
    summary:
      'Matters, contacts, parties, notes, assignments, budgets, timelines, communications, and matter files in one tenant-isolated record.',
  }),
  Object.freeze({
    id: 'intake',
    icon: 'PhoneIncoming',
    name: 'Caller intake and Zoom Phone',
    summary:
      'Manual and Zoom Phone intake with caller matching, call-history sync, notes, lead and task handoff, and CSV export.',
  }),
  Object.freeze({
    id: 'tasks',
    icon: 'ListChecks',
    name: 'Tasks, deadlines, and calendar',
    summary:
      'Tenant-scoped tasks with assignees, matter and contact links, priorities, deadlines, reminders, reassignment, and close reasons.',
  }),
  Object.freeze({
    id: 'documents',
    icon: 'FileText',
    name: 'Document preparation and automation',
    summary:
      'DOCX and TXT template analysis with variable substitution, retained PDF sources with AcroForm field mapping, review preview, flattened output, and filing back to the matter.',
  }),
  Object.freeze({
    id: 'billing',
    icon: 'Receipt',
    name: 'Time, invoicing, and trust accounting',
    summary:
      'Time and expense capture, invoices, payments, retainers, LEDES export, and optional Stripe payment flows.',
  }),
  Object.freeze({
    id: 'research',
    icon: 'Search',
    name: 'Source-linked legal research',
    summary:
      'Firm document retrieval and public CourtListener authority returned with citation review states so an attorney can verify before relying on an answer.',
  }),
  Object.freeze({
    id: 'chat',
    icon: 'MessageSquareText',
    name: 'Matter-aware AI chat and drafting',
    summary:
      'Ask, review, summarize, and draft against the active matter, with every tagged claim labeled cited, verify, or model.',
  }),
  Object.freeze({
    id: 'skills',
    icon: 'Layers3',
    name: 'Practice-area skill libraries',
    summary:
      'Commercial, litigation, corporate, employment, privacy, IP, regulatory, real estate, and criminal defense skills, plus dedicated trust and estate, domestic relations, and mediation workspaces.',
  }),
  Object.freeze({
    id: 'integrations',
    icon: 'Plug',
    name: 'Microsoft 365, Google, and QuickBooks',
    summary:
      'Administrator-enabled Microsoft 365, Google Workspace, Microsoft Teams, Zoom Phone, QuickBooks Online, SMTP, and enterprise file-share connections that can be disconnected at any time.',
  }),
  Object.freeze({
    id: 'mcp',
    icon: 'Braces',
    name: 'MCP for approved AI assistants',
    summary:
      'Workspace MCP exposes tenant-scoped matter work to approved assistants over OAuth 2.1, and Research MCP retrieves public legal authority only. Both remain in gated preview.',
  }),
  Object.freeze({
    id: 'controls',
    icon: 'ShieldCheck',
    name: 'Tenant isolation and review controls',
    summary:
      'Per-firm tenant isolation, module and role authorization, scoped platform-operator sessions, and application-encrypted stored credentials.',
  }),
])

/** Short capability labels, in page order, for structured data `featureList`. */
export const CORE_CAPABILITY_NAMES = Object.freeze(
  CORE_CAPABILITIES.map((capability) => capability.name),
)
