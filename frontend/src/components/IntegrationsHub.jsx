import {
  ArrowRight,
  BookOpen,
  Boxes,
  Cloud,
  DatabaseZap,
  FolderKey,
  KeyRound,
  MessageSquare,
  Phone,
  ReceiptText,
  Search,
  ShieldCheck,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import IntegrationsPanel from './IntegrationsPanel'
import TeamsPanel from './TeamsPanel'
import ZoomPanel from './ZoomPanel'
import QBOPanel from './QBOPanel'
import MCPPage from '../pages/MCPPage'
import CloudSearchAdmin from '../pages/CloudSearchAdmin'
import SmbAdminPage from '../pages/SmbAdminPage'

export const INTEGRATION_SECTIONS = [
  {
    id: 'cloud',
    label: 'Cloud accounts & storage',
    shortLabel: 'Cloud',
    icon: Cloud,
    eyebrow: 'Identity, mail, calendar & files',
    description: 'Connect Microsoft 365 or Google Workspace, choose where matter documents live, and manage approved imports.',
    permissions: [
      'Directory profiles for user provisioning',
      'Mail and calendar access for enabled workflows',
      'OneDrive, SharePoint, or Google Drive access for matter documents',
    ],
    setup: [
      'An administrator account for the organization provider',
      'Approved OAuth redirect URLs and application credentials',
      'A documented storage owner and destination',
    ],
    guide: '/guide/integrations',
    guideLabel: 'Integration setup guide',
    audience: 'admin',
    render: () => <IntegrationsPanel />,
  },
  {
    id: 'cloud-search',
    label: 'Cloud Search',
    shortLabel: 'Search',
    icon: Search,
    eyebrow: 'Approved content discovery',
    description: 'Search and synchronize authorized Gmail, Outlook, Drive, OneDrive, and SharePoint sources.',
    permissions: [
      'Uses the connected provider grant; it does not create a separate permission boundary',
      'Can expose result metadata, previews, provider links, and selected file contents',
    ],
    setup: [
      'Connect the provider account first',
      'Confirm the permitted site, drive, or mailbox boundary',
      'Run a non-sensitive test query before broader use',
    ],
    guide: '/guide/cloud-search-operations',
    guideLabel: 'Cloud Search operations guide',
    audience: 'admin',
    render: () => <CloudSearchAdmin />,
  },
  {
    id: 'file-shares',
    label: 'File shares',
    shortLabel: 'File shares',
    icon: FolderKey,
    eyebrow: 'On-premises document access',
    description: 'Connect approved SMB shares through a firm-managed agent without exposing file-server credentials to users.',
    permissions: [
      'Service-account access to explicitly configured network paths',
      'File metadata and content required for enabled search and document workflows',
    ],
    setup: [
      'A Windows host with network access to the share',
      'A least-privilege service account and approved root paths',
      'Agent installation, registration, and connectivity validation',
    ],
    guide: '/guide/file-share-operations',
    guideLabel: 'File Share operations guide',
    audience: 'admin',
    render: () => <SmbAdminPage />,
  },
  {
    id: 'teams',
    label: 'Microsoft Teams',
    shortLabel: 'Teams',
    icon: MessageSquare,
    eyebrow: 'Collaboration & call intake',
    description: 'Route approved matter updates to Teams and, when separately configured, capture inbound Teams Phone calls.',
    permissions: [
      'Team and channel lookup plus approved message and activity delivery',
      'Optional application-only call-record access for Teams Phone',
    ],
    setup: [
      'Microsoft 365 authorization with Teams selected',
      'Approved team/channel mappings and audience review',
      'Separate administrator consent for Teams Phone capture',
    ],
    guide: '/guide/microsoft-teams-administration',
    guideLabel: 'Microsoft Teams administration guide',
    audience: 'admin',
    render: () => <TeamsPanel />,
  },
  {
    id: 'zoom',
    label: 'Zoom',
    shortLabel: 'Zoom',
    icon: Phone,
    eyebrow: 'Meetings & phone intake',
    description: 'Connect Zoom Meetings and Zoom Phone as separate grants for meeting workflows and completed-call intake.',
    permissions: [
      'Meeting profile and meeting read/write access for enabled meeting workflows',
      'Account-level completed-call history and detail access for Zoom Phone',
    ],
    setup: [
      'An approved Zoom administrator and target account',
      'OAuth application credentials and redirect configuration',
      'Webhook verification plus recording/transcription consent policy',
    ],
    guide: '/guide/zoom-phone-administration',
    guideLabel: 'Zoom administration guide',
    audience: 'all',
    render: () => <ZoomPanel />,
  },
  {
    id: 'quickbooks',
    label: 'QuickBooks Online',
    shortLabel: 'QuickBooks',
    icon: ReceiptText,
    eyebrow: 'Accounting synchronization',
    description: 'Map and send approved customers, time activity, invoices, and payments to the intended QuickBooks company.',
    permissions: [
      'Accounting access and connected-company identity',
      'Reads service items and sync state; writes configured accounting records',
    ],
    setup: [
      'An Intuit administrator for the intended company',
      'Approved service-item and account mappings',
      'An accounting owner for reconciliation and exception review',
    ],
    guide: '/guide/quickbooks-administration',
    guideLabel: 'QuickBooks administration guide',
    audience: 'accounting',
    render: () => <QBOPanel />,
  },
  {
    id: 'mcp',
    label: 'MCP servers',
    shortLabel: 'MCP',
    icon: KeyRound,
    eyebrow: 'Approved external assistants',
    description: 'Govern tenant-scoped Workspace MCP access and separately keyed Research MCP clients, tools, and usage.',
    permissions: [
      'Workspace MCP acts as the consenting user within their LawHand permissions',
      'Research MCP keys are limited to the configured public-authority tool allowlist',
    ],
    setup: [
      'Choose tenant and per-user Workspace MCP policy',
      'Approve the exact assistant client and requested scopes',
      'For Research MCP, issue a named key and review its allowlist and owner',
    ],
    guide: '/guide/mcp-server-operations',
    guideLabel: 'MCP server operations guide',
    audience: 'admin',
    render: () => <MCPPage embedded />,
  },
]

export const LEGACY_INTEGRATION_TABS = {
  mcp: 'mcp',
  'cloud-search': 'cloud-search',
  smb: 'file-shares',
  teams: 'teams',
  zoom: 'zoom',
  qbo: 'quickbooks',
}

export function availableIntegrationSections(user) {
  if (user?.role === 'accountant') {
    return INTEGRATION_SECTIONS.filter((item) => item.audience === 'accounting')
  }
  if (user?.plan === 'intake-only') {
    return INTEGRATION_SECTIONS.filter((item) => item.id === 'zoom')
  }
  return INTEGRATION_SECTIONS.filter((item) => item.audience !== 'accounting' || user?.role === 'admin')
}

function IntegrationDetails({ item }) {
  return (
    <details className="group border-t border-brand-line bg-brand-bg/55 px-5 py-3">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-xs font-bold text-brand-ink marker:hidden">
        Permissions & setup
        <span className="text-brand-muted transition-transform group-open:rotate-180" aria-hidden="true">⌄</span>
      </summary>
      <div className="grid gap-5 pt-4 text-xs leading-5 text-brand-ink-2 sm:grid-cols-2">
        <div>
          <p className="mb-2 flex items-center gap-2 font-bold text-brand-ink"><ShieldCheck size={14} /> Data & permissions</p>
          <ul className="space-y-1.5">
            {item.permissions.map((permission) => <li key={permission} className="flex gap-2"><span className="text-brand-accent">•</span><span>{permission}</span></li>)}
          </ul>
        </div>
        <div>
          <p className="mb-2 flex items-center gap-2 font-bold text-brand-ink"><DatabaseZap size={14} /> Setup required</p>
          <ul className="space-y-1.5">
            {item.setup.map((step) => <li key={step} className="flex gap-2"><span className="text-brand-accent">•</span><span>{step}</span></li>)}
          </ul>
        </div>
      </div>
      <Link to={item.guide} className="mt-4 inline-flex items-center gap-2 text-xs font-bold text-brand-accent hover:text-brand-ink">
        <BookOpen size={14} /> {item.guideLabel} <ArrowRight size={13} />
      </Link>
    </details>
  )
}

function Overview({ sections, onSelect }) {
  return (
    <div className="space-y-6" data-testid="integrations-overview">
      <div className="rounded-2xl border border-brand-line bg-brand-ink px-6 py-7 text-white shadow-sm md:px-8">
        <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
          <div className="max-w-2xl">
            <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-white/55">Tenant connections</p>
            <h2 className="font-serif text-2xl font-bold tracking-tight md:text-3xl">Every external connection, in one place.</h2>
            <p className="mt-3 text-sm leading-6 text-white/70">Review what each integration does before connecting it. Open the permissions and setup notes to confirm the data boundary, provider consent, and operational owner.</p>
          </div>
          <Link to="/guide/integration-data-visibility" className="inline-flex w-fit shrink-0 items-center gap-2 rounded-lg border border-white/20 bg-white/10 px-4 py-2.5 text-xs font-bold text-white hover:bg-white/15">
            <BookOpen size={15} /> Full data visibility guide
          </Link>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {sections.map((item) => {
          const Icon = item.icon
          return (
            <article key={item.id} className="overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm transition hover:border-brand-line-2 hover:shadow-md">
              <button type="button" onClick={() => onSelect(item.id)} className="flex w-full items-start gap-4 p-5 text-left">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-bg text-brand-ink"><Icon size={20} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">{item.eyebrow}</span>
                  <span className="mt-1 block font-serif text-lg font-bold text-brand-ink">{item.label}</span>
                  <span className="mt-2 block text-xs leading-5 text-brand-ink-2">{item.description}</span>
                  <span className="mt-4 inline-flex items-center gap-1.5 text-xs font-bold text-brand-accent">Open configuration <ArrowRight size={13} /></span>
                </span>
              </button>
              <IntegrationDetails item={item} />
            </article>
          )
        })}
      </div>
    </div>
  )
}

export default function IntegrationsHub({ user, section = 'overview', onSectionChange }) {
  const sections = availableIntegrationSections(user)
  const selected = sections.find((item) => item.id === section)
  const activeSection = selected ? section : 'overview'
  const ActiveIcon = selected?.icon || Boxes

  return (
    <section aria-labelledby="integrations-heading">
      <div className="mb-6 flex flex-col gap-4 border-b border-brand-line pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="mb-1 text-[11px] font-bold uppercase tracking-[0.16em] text-brand-muted">Administration</p>
          <h2 id="integrations-heading" className="font-serif text-2xl font-bold tracking-tight text-brand-ink">Integrations</h2>
          <p className="mt-1 max-w-2xl text-sm text-brand-ink-2">Connect services deliberately, understand their access, and keep setup and health controls together.</p>
        </div>
      </div>

      <nav aria-label="Integration sections" className="mb-6 flex gap-2 overflow-x-auto pb-1">
        <button type="button" onClick={() => onSectionChange('overview')} className={`inline-flex shrink-0 items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold transition ${activeSection === 'overview' ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line bg-brand-surface text-brand-ink hover:border-brand-line-2'}`}>
          <Boxes size={15} /> Overview
        </button>
        {sections.map((item) => {
          const Icon = item.icon
          return (
            <button key={item.id} type="button" onClick={() => onSectionChange(item.id)} className={`inline-flex shrink-0 items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold transition ${activeSection === item.id ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line bg-brand-surface text-brand-ink hover:border-brand-line-2'}`}>
              <Icon size={15} /> {item.shortLabel}
            </button>
          )
        })}
      </nav>

      {activeSection === 'overview' ? (
        <Overview sections={sections} onSelect={onSectionChange} />
      ) : (
        <div className="space-y-6" data-testid={`integration-section-${selected.id}`}>
          <div className="overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm">
            <div className="flex items-start gap-4 p-5 md:p-6">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-bg text-brand-ink"><ActiveIcon size={20} /></span>
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">{selected.eyebrow}</p>
                <h3 className="mt-1 font-serif text-xl font-bold text-brand-ink">{selected.label}</h3>
                <p className="mt-2 max-w-3xl text-sm leading-6 text-brand-ink-2">{selected.description}</p>
              </div>
            </div>
            <IntegrationDetails item={selected} />
          </div>
          {selected.render()}
        </div>
      )}
    </section>
  )
}
