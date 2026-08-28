import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight, Bot, Braces, BriefcaseBusiness, Building2, Check,
  CheckCircle2, ChevronRight, CircleDollarSign, ClipboardCheck, ClipboardList,
  Clock3, FileCheck2, FileInput, FileSearch, FileSignature, Files, FolderSync,
  Handshake, Headphones, Home, Inbox, Landmark, Layers3, Lightbulb, Link2,
  ListChecks, Lock, MailCheck, MessageSquareText, PhoneCall, Plug, Receipt,
  Rocket, Scale, Search, ShieldCheck, Sparkles, UserCheck, UserCircle, Users, Vault,
} from 'lucide-react'
import MarketingPageLayout from '../components/MarketingChrome'
import { CORE_CAPABILITIES, CAPABILITY_STATES } from '../marketing/capabilities'
import { PRACTICE_SKILLS, WORKSPACE_MODULES } from '../marketing/catalog'

const CATALOG_ICONS = {
  Files, Lock, Landmark, Building2, UserCircle, Rocket, Lightbulb, Bot,
  ClipboardList, Home, Scale, Vault, Users, Handshake,
}

const CAPABILITIES_BY_ID = new Map(CORE_CAPABILITIES.map((capability) => [capability.id, capability]))

const AVAILABILITY_STYLES = {
  implemented: 'border-brand-green/25 bg-brand-green/10 text-brand-green',
  'controlled-pilot': 'border-brand-accent/20 bg-brand-accent/10 text-brand-accent-2',
  'partner-dependent': 'border-brand-amber/30 bg-brand-amber/10 text-[#8a5b08]',
}

const FLOW_STEPS = [
  {
    id: 'capture',
    number: '01',
    icon: PhoneCall,
    label: 'Capture the request',
    title: 'A call becomes structured intake—not another loose note.',
    body: 'Staff can capture a caller manually or from an enabled Teams or Zoom Phone feed, match prior history, record the purpose and outcome, and give follow-up a named owner.',
    sceneLabel: 'Intake / live call',
    sceneTitle: 'Jordan Lee · Brew & Bloom',
    sceneStatus: 'Returning contact matched',
    items: [
      ['Reason for call', 'Manager separation advice'],
      ['Prior contact', '2 calls · 1 open client'],
      ['Follow-up', 'Call back today · Maya Chen'],
    ],
    outcome: 'The next person starts with the facts and the relationship history already together.',
  },
  {
    id: 'qualify',
    number: '02',
    icon: FileSearch,
    label: 'Qualify and clear',
    title: 'The intake decision leaves a reviewable record.',
    body: 'Move a lead through qualification, preserve a conflict search and the result the reviewer saw, then record the decision before opening work. Restricted matches warn without exposing a protected matter.',
    sceneLabel: 'Conflict search / review',
    sceneTitle: 'Brew & Bloom · Manager separation',
    sceneStatus: 'Cleared with note',
    items: [
      ['Search terms', 'Brew & Bloom · Jordan Lee'],
      ['Potential matches', '1 reviewed · no conflict'],
      ['Decision', 'Cleared by A. Rivera · 10:42 AM'],
    ],
    outcome: 'The firm can see who reviewed the search, what they saw, and why the work moved forward.',
  },
  {
    id: 'organize',
    number: '03',
    icon: BriefcaseBusiness,
    label: 'Open and organize',
    title: 'One matter record becomes the operating spine.',
    body: 'Parties, team, key dates, tasks, notes, communications, billing settings, and cloud files stay attached to the same matter instead of being reconstructed across separate systems.',
    sceneLabel: 'Matter / overview',
    sceneTitle: 'Brew & Bloom · Manager separation',
    sceneStatus: 'Active · attorney review',
    items: [
      ['People', 'Client · employee · outside HR'],
      ['Key dates', 'Response due Sep 12'],
      ['Work plan', '6 tasks · 2 need review'],
    ],
    outcome: 'Everyone sees the same matter posture while permissions still decide what each role may open or change.',
  },
  {
    id: 'prepare',
    number: '04',
    icon: FileCheck2,
    label: 'Prepare and review',
    title: 'Work product moves through a visible human review gate.',
    body: 'Use firm templates, reviewed PDF field maps, authorized matter context, and source-linked AI assistance to prepare work. Drafts remain drafts until the right person reviews them.',
    sceneLabel: 'Review queue / work product',
    sceneTitle: 'Separation response package',
    sceneStatus: 'Attorney review required',
    items: [
      ['Response letter.docx', 'Prepared from firm template'],
      ['Separation terms.pdf', '9 fields mapped · preview ready'],
      ['Research note', '4 cited · 1 verify'],
    ],
    outcome: 'The reviewer can inspect the document, its inputs, and its source trail before anything is relied on or sent.',
  },
  {
    id: 'deliver',
    number: '05',
    icon: FileSignature,
    label: 'Deliver and account',
    title: 'Client action, signature, and billing close the loop.',
    body: 'Share approved work through the client portal, route a document for signature, track delivery and first view, capture billable time, and prepare an invoice—with optional accounting sync.',
    sceneLabel: 'Client delivery / billing',
    sceneTitle: 'Approved response package',
    sceneStatus: 'Client action requested',
    items: [
      ['Portal message', 'Opened · 11:18 AM'],
      ['Signature request', 'Signer 1 of 2 complete'],
      ['Draft invoice #1048', '$1,860 · review before issue'],
    ],
    outcome: 'The matter history shows what was shared, who acted, what remains, and how the work will be billed.',
  },
]

const ROLE_VIEWS = [
  {
    id: 'attorney', label: 'Attorney', icon: Scale,
    title: 'A review queue built around professional judgment.',
    body: 'See the matter posture, upcoming dates, draft work, source trail, client activity, and decisions waiting for legal review.',
    stat: '3', statLabel: 'items need review',
    items: ['Response package · sources attached', 'Conflict decision · ready to close', 'Client question · new message'],
  },
  {
    id: 'paralegal', label: 'Paralegal', icon: ClipboardCheck,
    title: 'The next action is owned, linked, and traceable.',
    body: 'Work tasks by stage, prepare documents from approved templates, file correspondence, update parties, and keep deadlines visible.',
    stat: '6', statLabel: 'open matter tasks',
    items: ['Prepare response package · in progress', 'Confirm client signature · waiting', 'File approved document · assigned'],
  },
  {
    id: 'intake', label: 'Intake', icon: Headphones,
    title: 'Every new request starts with context and a handoff.',
    body: 'Match the caller, capture the reason and outcome, qualify the lead, preserve conflict review, and route follow-up to the right person.',
    stat: '4', statLabel: 'new calls today',
    items: ['2 returning contacts matched', '1 conflict review pending', '3 callback tasks assigned'],
  },
  {
    id: 'billing', label: 'Billing', icon: CircleDollarSign,
    title: 'Bill from the same work record the team already uses.',
    body: 'Review time and expenses, prepare invoices, track balances and payments, keep trust separate, and synchronize approved accounting records where enabled.',
    stat: '$8.4k', statLabel: 'draft billable value',
    items: ['12 time entries ready', '2 draft invoices need review', '1 QuickBooks sync completed'],
  },
  {
    id: 'client', label: 'Client', icon: UserCheck,
    title: 'A focused portal shows the client what needs attention.',
    body: 'Clients see unread messages, shared documents, signatures, balances, and the next key date without being dropped into the firm’s internal workspace.',
    stat: '2', statLabel: 'actions waiting',
    items: ['Review response package', 'Sign separation terms', 'Invoice #1048 · not yet issued'],
  },
]

const CAPABILITY_GROUPS = [
  { eyebrow: 'Start with the relationship', title: 'From first contact to an opened matter', ids: ['crm', 'intake', 'conflicts'] },
  { eyebrow: 'Run the matter', title: 'Keep people, communication, dates, and client action together', ids: ['tasks', 'communications', 'client-portal'] },
  { eyebrow: 'Produce the work', title: 'Prepare documents and analysis for review', ids: ['documents', 'signature', 'research', 'chat'] },
  { eyebrow: 'Operate the firm', title: 'Account for the work and connect the systems around it', ids: ['billing', 'skills', 'integrations', 'mcp', 'controls'] },
]

const CAPABILITY_DETAILS = {
  crm: ['Clients, contacts, parties, notes, budgets', 'Matter history and relationship activity'],
  intake: ['Caller matching and call history', 'Lead, outcome, task, and export handoff'],
  conflicts: ['Saved search terms and results', 'Reviewer notes, decision, and restricted-match warning'],
  tasks: ['Assignees, priorities, reminders, stages', 'Matter, contact, email, and deadline links'],
  communications: ['Matter forwarding address and review queue', 'Email-to-task and deadline handoff'],
  'client-portal': ['Unread messages and shared documents', 'Upcoming dates, signatures, invoices, and balances'],
  documents: ['DOCX/TXT Smart Fill and retained PDF sources', 'Field mapping, preview, integrity checks, and matter filing'],
  signature: ['Sequential signing and delivery state', 'First-view tracking, reminders, and manual resend'],
  research: ['Configured public authority and authorized firm sources', 'Links and cited / verify / model review states'],
  chat: ['Matter-aware review, summary, research, and drafting', 'Visible context and professional review boundary'],
  billing: ['Time, expenses, invoices, payments, retainers', 'LEDES, trust accounting, and optional Stripe flows'],
  skills: ['Ten practice-area skill libraries', 'Three dedicated role-aware workspaces'],
  integrations: ['Microsoft 365, Google, Teams, Zoom, QuickBooks', 'Cloud storage, SMTP, and enterprise file shares'],
  mcp: ['OAuth-scoped Workspace MCP', 'Research-only MCP with bounded access and usage'],
  controls: ['Tenant and module authorization', 'Privacy controls, encrypted credentials, and audit history'],
}

const INTEGRATIONS = [
  { name: 'Microsoft 365', detail: 'Outlook, calendar, OneDrive, and SharePoint', icon: MailCheck },
  { name: 'Google Workspace', detail: 'Gmail, calendar, and Drive', icon: FolderSync },
  { name: 'Teams', detail: 'Matter tab, notifications, and enabled call intake', icon: MessageSquareText },
  { name: 'Zoom Phone', detail: 'Verified call history into intake', icon: PhoneCall },
  { name: 'QuickBooks Online', detail: 'Client and invoice accounting sync', icon: Receipt },
  { name: 'Enterprise file shares', detail: 'Firm-controlled SMB source indexing', icon: Files },
]

const CONTROLS = [
  ['Tenant-isolated record', 'Firm workspaces stay separated, while module and role rules narrow access inside the firm.'],
  ['Review before effect', 'AI and connected assistants prepare work for a person; they do not approve, file, send, or deliver it on their own.'],
  ['Source-visible assistance', 'Retrieved sources and review states stay close to AI-assisted work so an attorney can inspect before reliance.'],
  ['Consent and revocation', 'Administrators enable integrations; users explicitly connect approved assistants; access can be disconnected or revoked.'],
]

function AvailabilityBadge({ availability }) {
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[9px] font-bold uppercase tracking-[0.11em] ${AVAILABILITY_STYLES[availability] || AVAILABILITY_STYLES.implemented}`}>
      {CAPABILITY_STATES[availability]}
    </span>
  )
}

function CapabilityIcon({ id }) {
  const icons = {
    crm: Users, intake: PhoneCall, conflicts: FileSearch, tasks: ListChecks,
    communications: Inbox, 'client-portal': UserCheck, documents: FileInput,
    signature: FileSignature, research: Search, chat: MessageSquareText,
    billing: Receipt, skills: Layers3, integrations: Plug, mcp: Braces, controls: ShieldCheck,
  }
  const Icon = icons[id] || CheckCircle2
  return <Icon size={18} aria-hidden="true" />
}

function MatterCommandCenter() {
  return (
    <div aria-label="Illustrative LawHand matter command center" role="region" className="overflow-hidden rounded-[28px] border border-white/10 bg-[#f9f8f5] text-brand-ink shadow-2xl shadow-black/25">
      <div className="flex items-center justify-between border-b border-brand-line bg-white px-4 py-3 sm:px-5">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-ink text-[10px] font-black text-white">LH</span>
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-brand-muted">Matter command center</p>
            <p className="text-[11px] font-semibold">Illustrative workspace</p>
          </div>
        </div>
        <span className="rounded-full bg-brand-green/10 px-2.5 py-1 text-[9px] font-bold text-brand-green">Active</span>
      </div>

      <div className="grid min-h-[440px] sm:grid-cols-[116px_1fr]">
        <aside className="hidden border-r border-brand-line bg-[#eeebe4] p-3 sm:block" aria-label="Illustrative matter navigation">
          {[
            ['Overview', BriefcaseBusiness, true], ['People', Users], ['Tasks', ListChecks],
            ['Documents', Files], ['Messages', Inbox], ['Billing', Receipt],
          ].map(([label, Icon, active]) => (
            <div key={label} className={`mb-1 flex items-center gap-2 rounded-lg px-2.5 py-2 text-[10px] font-semibold ${active ? 'bg-white text-brand-ink shadow-sm' : 'text-brand-muted'}`}>
              <Icon size={13} strokeWidth={1.8} aria-hidden="true" /> {label}
            </div>
          ))}
        </aside>

        <div className="p-4 sm:p-5">
          <div className="flex flex-col gap-3 border-b border-brand-line pb-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-brand-accent-2">Employment · active matter</p>
              <h2 className="mt-1 text-[18px] font-bold leading-tight">Brew &amp; Bloom · Manager separation</h2>
              <p className="mt-1 text-[10px] text-brand-muted">MAT-1048 · Maya Chen, responsible attorney</p>
            </div>
            <span className="w-fit rounded-full border border-brand-accent/20 bg-brand-accent/10 px-2.5 py-1 text-[9px] font-bold text-brand-accent-2">Review in progress</span>
          </div>

          <div className="mt-4 grid grid-cols-3 gap-2">
            {[
              ['3', 'Need review'], ['Sep 12', 'Next key date'], ['$1.9k', 'Draft billable'],
            ].map(([value, label]) => (
              <div key={label} className="rounded-xl border border-brand-line bg-white p-3">
                <p className="text-[14px] font-black sm:text-[16px]">{value}</p>
                <p className="mt-0.5 text-[8px] font-bold uppercase tracking-[0.08em] text-brand-muted sm:text-[9px]">{label}</p>
              </div>
            ))}
          </div>

          <div className="mt-4 rounded-xl border border-brand-line bg-white p-4">
            <div className="flex items-center justify-between">
              <p className="text-[11px] font-bold">Review queue</p>
              <span className="text-[9px] font-semibold text-brand-accent-2">View matter</span>
            </div>
            <div className="mt-3 space-y-2.5">
              {[
                ['Response package', 'Prepared from firm template', FileCheck2, 'Review'],
                ['Research note', '4 cited · 1 verify', Search, 'Verify'],
                ['Client message', 'Question about separation date', MessageSquareText, 'New'],
                ['Signature request', 'Signer 1 of 2 completed', FileSignature, 'Track'],
              ].map(([title, detail, Icon, action]) => (
                <div key={title} className="flex items-center gap-3 rounded-lg bg-brand-bg-soft/55 px-3 py-2.5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-brand-accent-2"><Icon size={15} aria-hidden="true" /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[10.5px] font-bold">{title}</span>
                    <span className="block truncate text-[9px] text-brand-muted">{detail}</span>
                  </span>
                  <span className="text-[9px] font-bold text-brand-accent-2">{action}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-[9px] font-semibold text-brand-muted">
            <span>Working team</span>
            {['Attorney', 'Paralegal', 'Client'].map((label) => <span key={label} className="rounded-full border border-brand-line bg-white px-2 py-1 text-brand-ink">{label}</span>)}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function ProductPage() {
  const [activeFlow, setActiveFlow] = useState(0)
  const [activeRole, setActiveRole] = useState(0)
  const [activeSkill, setActiveSkill] = useState(0)

  const flow = FLOW_STEPS[activeFlow]
  const role = ROLE_VIEWS[activeRole]
  const skill = PRACTICE_SKILLS[activeSkill]
  const SkillIcon = CATALOG_ICONS[skill.icon]
  const demoUrl = '/request-demo?source=product'

  return (
    <MarketingPageLayout>
      <section className="relative overflow-hidden bg-brand-ink text-white">
        <div className="absolute -right-20 top-12 h-72 w-72 rounded-full bg-brand-accent/30 blur-3xl" aria-hidden="true" />
        <div className="absolute -bottom-40 left-[28%] h-80 w-80 rounded-full bg-brand-green/15 blur-3xl" aria-hidden="true" />
        <div className="relative mx-auto grid max-w-6xl gap-12 px-6 pb-16 pt-14 md:pb-24 md:pt-20 lg:grid-cols-[0.86fr_1.14fr] lg:items-center">
          <div>
            <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.16em] text-white/75"><Layers3 size={13} aria-hidden="true" /> Unified matter operating system</span>
            <h1 className="mt-6 max-w-xl font-serif text-[44px] font-bold leading-[0.98] tracking-tight text-white md:text-[62px]">See the entire matter move.</h1>
            <p className="mt-6 max-w-xl font-sans text-[18px] leading-relaxed text-white/72">
              From the first call through conflict review, drafting, client approval, signature,
              billing, and follow-up—LawHand keeps the work, the people, and the reason for every
              next step together.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to={demoUrl} className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-white px-6 font-sans text-[14px] font-semibold text-brand-ink shadow-sm transition-all hover:-translate-y-px hover:bg-brand-bg motion-reduce:hover:translate-y-0">Book a workflow demo <ArrowRight size={17} aria-hidden="true" /></Link>
              <a href="#matter-flow" className="inline-flex min-h-12 items-center gap-2 rounded-lg border border-white/20 bg-white/5 px-6 font-sans text-[14px] font-semibold text-white transition-colors hover:bg-white/10">Follow a matter <ChevronRight size={17} aria-hidden="true" /></a>
            </div>
            <div className="mt-9 grid max-w-lg grid-cols-3 gap-3 border-t border-white/10 pt-6">
              {[
                ['One record', 'for the whole matter'], ['Review first', 'before reliance or delivery'], ['Role aware', 'for staff, counsel, and clients'],
              ].map(([title, detail]) => (
                <div key={title}><p className="text-[12px] font-bold text-white">{title}</p><p className="mt-1 text-[9.5px] leading-relaxed text-white/50">{detail}</p></div>
              ))}
            </div>
          </div>
          <MatterCommandCenter />
        </div>
      </section>

      <section className="border-b border-brand-line bg-brand-surface">
        <div className="mx-auto grid max-w-6xl gap-5 px-6 py-7 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ['Matter-centered', 'Intake, people, work, and money share one record', BriefcaseBusiness],
            ['Human-approved', 'Consequential work stays behind a review gate', UserCheck],
            ['Source-visible', 'Research links and review states stay in view', Link2],
            ['Firm-controlled', 'Roles, modules, integrations, and consent are explicit', ShieldCheck],
          ].map(([title, detail, Icon]) => (
            <div key={title} className="flex items-start gap-3"><span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-bg-soft text-brand-accent-2"><Icon size={15} aria-hidden="true" /></span><div><p className="text-[12px] font-bold">{title}</p><p className="mt-1 text-[10.5px] leading-relaxed text-brand-muted">{detail}</p></div></div>
          ))}
        </div>
      </section>

      <section id="matter-flow" className="scroll-mt-20 mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="grid gap-8 lg:grid-cols-[0.7fr_1.3fr] lg:items-end">
          <div>
            <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">One matter, end to end</span>
            <h2 className="mt-3 max-w-xl font-serif text-[36px] font-bold leading-tight md:text-[44px]">Legal work is a chain of handoffs. Make every one visible.</h2>
          </div>
          <p className="max-w-2xl font-sans text-[16px] leading-relaxed text-brand-ink-2 lg:justify-self-end">Pick a stage to see what the team receives, what LawHand keeps with the matter, and what becomes possible next. The scene is illustrative; availability depends on the modules, permissions, and provider connections enabled for the firm.</p>
        </div>

        <div role="tablist" aria-label="Matter workflow stages" className="mt-10 grid gap-2 md:grid-cols-5">
          {FLOW_STEPS.map((step, index) => {
            const Icon = step.icon
            const selected = index === activeFlow
            return (
              <button key={step.id} type="button" role="tab" aria-selected={selected} aria-controls="matter-flow-panel" onClick={() => setActiveFlow(index)} className={`group min-h-24 rounded-2xl border p-4 text-left transition-all ${selected ? 'border-brand-ink bg-brand-ink text-white shadow-lg' : 'border-brand-line bg-brand-surface text-brand-ink hover:border-brand-line-2 hover:-translate-y-0.5 motion-reduce:hover:translate-y-0'}`}>
                <span className="flex items-center justify-between"><Icon size={18} className={selected ? 'text-white' : 'text-brand-accent-2'} aria-hidden="true" /><span className={`text-[9px] font-black tracking-[0.14em] ${selected ? 'text-white/45' : 'text-brand-muted'}`}>{step.number}</span></span>
                <span className="mt-4 block text-[11px] font-bold leading-snug">{step.label}</span>
              </button>
            )
          })}
        </div>

        <div id="matter-flow-panel" role="tabpanel" className="mt-4 overflow-hidden rounded-[28px] border border-brand-line bg-brand-surface shadow-xl shadow-brand-ink/5">
          <div className="grid lg:grid-cols-[1.1fr_0.9fr]">
            <div className="border-b border-brand-line p-6 sm:p-8 lg:border-b-0 lg:border-r">
              <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-brand-accent-2"><span>{flow.number}</span><span className="h-px w-6 bg-brand-line-2" />{flow.label}</div>
              <h3 className="mt-4 max-w-2xl font-serif text-[27px] font-bold leading-tight md:text-[32px]">{flow.title}</h3>
              <p className="mt-4 max-w-2xl text-[14.5px] leading-relaxed text-brand-ink-2">{flow.body}</p>
              <div className="mt-7 flex items-start gap-3 rounded-2xl border border-brand-accent/15 bg-brand-accent/5 p-4"><CheckCircle2 size={18} className="mt-0.5 shrink-0 text-brand-accent-2" aria-hidden="true" /><div><p className="text-[10px] font-bold uppercase tracking-[0.12em] text-brand-accent-2">What changes</p><p className="mt-1.5 text-[13px] leading-relaxed text-brand-ink-2">{flow.outcome}</p></div></div>
            </div>
            <div className="bg-brand-bg-soft/55 p-6 sm:p-8">
              <div className="rounded-2xl border border-brand-line bg-white p-4 shadow-sm">
                <div className="flex items-start justify-between gap-4 border-b border-brand-line pb-4"><div><p className="text-[9px] font-bold uppercase tracking-[0.13em] text-brand-muted">{flow.sceneLabel}</p><p className="mt-1 text-[13px] font-bold">{flow.sceneTitle}</p></div><span className="rounded-full bg-brand-green/10 px-2.5 py-1 text-[8.5px] font-bold text-brand-green">{flow.sceneStatus}</span></div>
                <dl className="mt-2 divide-y divide-brand-line">
                  {flow.items.map(([label, value]) => <div key={label} className="grid gap-1 py-3 sm:grid-cols-[0.8fr_1.2fr] sm:gap-4"><dt className="text-[9.5px] font-bold uppercase tracking-[0.08em] text-brand-muted">{label}</dt><dd className="text-[11px] font-semibold text-brand-ink sm:text-right">{value}</dd></div>)}
                </dl>
                <div className="mt-3 flex items-center justify-between rounded-xl bg-brand-ink px-3.5 py-3 text-white"><span className="text-[10px] font-bold">Open in the matter</span><ArrowRight size={14} aria-hidden="true" /></div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-24">
          <div className="max-w-3xl">
            <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">One record, different responsibilities</span>
            <h2 className="mt-3 font-serif text-[36px] font-bold leading-tight md:text-[44px]">Show each person the work that is theirs.</h2>
            <p className="mt-4 max-w-2xl text-[16px] leading-relaxed text-brand-ink-2">The matter stays shared. The view, authority, and next action change with the role.</p>
          </div>
          <div className="mt-9 grid gap-5 lg:grid-cols-[260px_1fr]">
            <div role="tablist" aria-label="Matter views by role" className="grid gap-2 sm:grid-cols-5 lg:grid-cols-1">
              {ROLE_VIEWS.map((view, index) => {
                const Icon = view.icon
                const selected = activeRole === index
                return <button key={view.id} type="button" role="tab" aria-selected={selected} aria-controls="role-view-panel" onClick={() => setActiveRole(index)} className={`flex min-h-12 items-center gap-3 rounded-xl border px-4 py-3 text-left text-[12px] font-bold transition-colors ${selected ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line bg-white text-brand-ink hover:border-brand-line-2'}`}><Icon size={16} aria-hidden="true" /> {view.label}</button>
              })}
            </div>
            <div id="role-view-panel" role="tabpanel" className="grid overflow-hidden rounded-[26px] border border-brand-line bg-white md:grid-cols-[1.05fr_0.95fr]">
              <div className="p-6 sm:p-8"><span className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-accent-2">{role.label} workspace</span><h3 className="mt-3 font-serif text-[27px] font-bold leading-tight">{role.title}</h3><p className="mt-4 text-[14px] leading-relaxed text-brand-ink-2">{role.body}</p><div className="mt-7 flex items-end gap-3 border-t border-brand-line pt-6"><span className="text-[36px] font-black leading-none text-brand-ink">{role.stat}</span><span className="pb-1 text-[11px] font-semibold text-brand-muted">{role.statLabel}</span></div></div>
              <div className="border-t border-brand-line bg-brand-ink p-6 text-white sm:p-8 md:border-l md:border-t-0"><div className="flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-[0.13em] text-white/50">Right now</span><Clock3 size={15} className="text-white/45" aria-hidden="true" /></div><div className="mt-5 space-y-3">{role.items.map((item, index) => <div key={item} className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/5 p-3.5"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/10 text-[9px] font-black">{index + 1}</span><span className="text-[11px] font-semibold leading-relaxed text-white/80">{item}</span></div>)}</div></div>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="max-w-3xl">
          <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">The platform, without the feature fog</span>
          <h2 className="mt-3 font-serif text-[36px] font-bold leading-tight md:text-[44px]">Everything the team needs around the matter record.</h2>
          <p className="mt-4 max-w-2xl text-[16px] leading-relaxed text-brand-ink-2">Rollout labels are part of the story. Implemented capabilities, controlled pilots, and provider-dependent connections are identified instead of being presented as one undifferentiated promise.</p>
        </div>
        <div className="mt-12 space-y-14">
          {CAPABILITY_GROUPS.map((group) => (
            <div key={group.title}>
              <div className="grid gap-2 border-b border-brand-line pb-4 md:grid-cols-[0.5fr_1.5fr] md:items-end"><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-accent-2">{group.eyebrow}</p><h2 className="font-serif text-[24px] font-bold leading-tight md:text-[28px]">{group.title}</h2></div>
              <div className="mt-5 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {group.ids.map((id) => {
                  const capability = CAPABILITIES_BY_ID.get(id)
                  if (!capability) return null
                  return (
                    <article key={capability.id} className="flex min-h-full flex-col rounded-2xl border border-brand-line bg-brand-surface p-5 transition-all hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-md motion-reduce:hover:translate-y-0">
                      <div className="flex items-start justify-between gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-bg-soft text-brand-accent-2"><CapabilityIcon id={id} /></span><AvailabilityBadge availability={capability.availability} /></div>
                      <h3 className="mt-5 font-serif text-[17px] font-bold">{capability.name}</h3>
                      <p className="mt-2 text-[12.5px] leading-relaxed text-brand-ink-2">{capability.summary}</p>
                      <ul className="mt-4 space-y-2 border-t border-brand-line pt-4">{(CAPABILITY_DETAILS[id] || []).map((detail) => <li key={detail} className="flex gap-2 text-[10.5px] leading-relaxed text-brand-muted"><Check size={13} className="mt-0.5 shrink-0 text-brand-green" aria-hidden="true" /> {detail}</li>)}</ul>
                    </article>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-ink text-white">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-24">
          <div className="grid gap-10 lg:grid-cols-[0.85fr_1.15fr]">
            <div>
              <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-brand-gold">Review-first intelligence</span>
              <h2 className="mt-3 font-serif text-[36px] font-bold leading-tight text-white md:text-[44px]">AI can prepare the work. Your team remains responsible for it.</h2>
              <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-white/65">Matter-aware chat works inside LawHand. Approved assistants can connect through scoped MCP. In both directions, sources, permissions, consent, and human review define the boundary.</p>
              <div className="mt-7 flex flex-wrap gap-3"><Link to="/product/chat" className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-white px-5 text-[12px] font-bold text-brand-ink hover:bg-brand-bg">Explore AI Chat <ArrowRight size={14} aria-hidden="true" /></Link><Link to="/product/mcp" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-white/20 px-5 text-[12px] font-bold text-white hover:bg-white/10">Explore MCP <ArrowRight size={14} aria-hidden="true" /></Link></div>
            </div>
            <div className="grid gap-3 sm:grid-cols-4 lg:self-end">
              {[
                ['1', 'Context', 'Authorized matter and sources', BriefcaseBusiness], ['2', 'Prepare', 'Research, summarize, or draft', Sparkles], ['3', 'Review', 'Inspect sources and work product', UserCheck], ['4', 'Act', 'An authorized person completes the step', CheckCircle2],
              ].map(([number, title, detail, Icon], index) => (
                <div key={title} className="relative rounded-2xl border border-white/10 bg-white/5 p-4"><div className="flex items-center justify-between"><Icon size={17} className="text-brand-gold" aria-hidden="true" /><span className="text-[9px] font-black text-white/30">{number}</span></div><p className="mt-6 text-[12px] font-bold">{title}</p><p className="mt-1 text-[9.5px] leading-relaxed text-white/50">{detail}</p>{index < 3 && <ChevronRight size={14} className="absolute -right-2.5 top-1/2 hidden -translate-y-1/2 text-white/30 sm:block" aria-hidden="true" />}</div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="grid gap-8 lg:grid-cols-[0.72fr_1.28fr]">
          <div><span className="text-[11px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Practice-area intelligence</span><h2 className="mt-3 font-serif text-[36px] font-bold leading-tight">The same matter spine, shaped for the work.</h2><p className="mt-4 text-[15px] leading-relaxed text-brand-ink-2">Skill libraries bring familiar review patterns and terminology to the shared record. Choose one to see the work it organizes.</p></div>
          <div>
            <div role="tablist" aria-label="Practice-area skill libraries" className="grid gap-2 sm:grid-cols-2">
              {PRACTICE_SKILLS.map((practice, index) => {
                const Icon = CATALOG_ICONS[practice.icon]
                const selected = activeSkill === index
                return (
                  <button key={practice.id} type="button" role="tab" aria-selected={selected} aria-controls="practice-skill-panel" onClick={() => setActiveSkill(index)} className={`flex items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors ${selected ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line bg-white hover:border-brand-line-2'}`}>
                    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${selected ? 'bg-white/10 text-white' : 'bg-brand-bg-soft text-brand-accent-2'}`}><Icon size={16} aria-hidden="true" /></span>
                    <span><h3 className="text-[11.5px] font-bold">{practice.name}</h3><span className={`mt-0.5 block text-[9px] ${selected ? 'text-white/55' : 'text-brand-muted'}`}>{practice.description}</span></span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
        <div id="practice-skill-panel" role="tabpanel" className="mt-5 grid overflow-hidden rounded-[26px] border border-brand-line bg-brand-surface lg:grid-cols-[0.8fr_1.2fr]">
          <div className="border-b border-brand-line bg-brand-bg-soft/55 p-6 sm:p-8 lg:border-b-0 lg:border-r">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-ink text-white"><SkillIcon size={20} aria-hidden="true" /></span><p className="mt-6 text-[10px] font-bold uppercase tracking-[0.13em] text-brand-accent-2">{skill.name}</p><h3 className="mt-2 font-serif text-[25px] font-bold">{skill.example}</h3><div className="mt-5 flex flex-wrap gap-2"><span className="rounded-full bg-brand-amber/10 px-2.5 py-1 text-[9px] font-bold text-[#8a5b08]">{skill.status}</span><span className="rounded-full bg-brand-accent/10 px-2.5 py-1 text-[9px] font-bold text-brand-accent-2">{skill.signal}</span></div><p className="mt-6 text-[10.5px] leading-relaxed text-brand-muted">{skill.language}</p>
          </div>
          <div className="p-6 sm:p-8"><div className="grid gap-5 sm:grid-cols-2"><div><p className="text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">Work in view</p><div className="mt-3 space-y-2">{skill.artifacts.map(([label, status]) => <div key={label} className="flex items-center justify-between gap-4 rounded-xl border border-brand-line px-3.5 py-3"><span className="text-[10.5px] font-semibold">{label}</span><span className="text-right text-[9px] font-bold text-brand-accent-2">{status}</span></div>)}</div></div><div><p className="text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">How the skill helps</p><ul className="mt-3 space-y-3">{skill.features.map((feature) => <li key={feature} className="flex gap-2.5 text-[11px] leading-relaxed text-brand-ink-2"><CheckCircle2 size={15} className="mt-0.5 shrink-0 text-brand-green" aria-hidden="true" />{feature}</li>)}</ul></div></div></div>
        </div>

        <div className="mt-12">
          <div className="flex flex-col gap-3 border-b border-brand-line pb-5 sm:flex-row sm:items-end sm:justify-between"><div><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-accent-2">Dedicated workspaces</p><h2 className="mt-2 font-serif text-[28px] font-bold">When the practice needs its own records and roles.</h2></div><p className="max-w-md text-[11px] leading-relaxed text-brand-muted">Enabled for selected firms during controlled onboarding.</p></div>
          <div className="mt-5 grid gap-5 lg:grid-cols-3">
            {WORKSPACE_MODULES.map((module) => {
              const Icon = CATALOG_ICONS[module.icon]
              return (
                <article key={module.id} className="rounded-2xl border border-brand-line bg-brand-surface p-5">
                  <div className="flex items-start justify-between gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-bg-soft text-brand-accent-2"><Icon size={18} aria-hidden="true" /></span><span className="rounded-full border border-brand-accent/20 bg-brand-accent/10 px-2.5 py-1 text-[8.5px] font-bold uppercase tracking-[0.1em] text-brand-accent-2">Controlled onboarding</span></div>
                  <h3 className="mt-5 font-serif text-[17px] font-bold">{module.name}</h3><p className="mt-2 text-[11px] leading-relaxed text-brand-ink-2">{module.description}</p>
                  <div className="mt-4 grid grid-cols-3 gap-2">{module.metrics.map(([value, label]) => <div key={label} className="rounded-lg bg-brand-bg-soft/60 p-2.5"><p className="text-[11px] font-black">{value}</p><p className="mt-1 text-[8px] leading-tight text-brand-muted">{label}</p></div>)}</div>
                  <ul className="mt-4 space-y-2 border-t border-brand-line pt-4">{module.features.map((feature) => <li key={feature} className="flex gap-2 text-[10px] leading-relaxed text-brand-muted"><Check size={12} className="mt-0.5 shrink-0 text-brand-green" aria-hidden="true" />{feature}</li>)}</ul>
                </article>
              )
            })}
          </div>
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto grid max-w-6xl gap-12 px-6 py-16 md:py-24 lg:grid-cols-2">
          <div>
            <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Connected around the matter</span><h2 className="mt-3 font-serif text-[34px] font-bold leading-tight">Use the systems the firm already runs.</h2><p className="mt-4 max-w-xl text-[14px] leading-relaxed text-brand-ink-2">Connections are administrator-enabled, depend on the applicable provider account and permissions, and can be disconnected. LawHand keeps the matter record and review state at the center.</p>
            <div className="mt-7 grid gap-3 sm:grid-cols-2">{INTEGRATIONS.map(({ name, detail, icon: Icon }) => <div key={name} className="flex items-start gap-3 rounded-xl border border-brand-line bg-white p-4"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-bg-soft text-brand-accent-2"><Icon size={16} aria-hidden="true" /></span><div><h3 className="text-[11px] font-bold">{name}</h3><p className="mt-1 text-[9.5px] leading-relaxed text-brand-muted">{detail}</p></div></div>)}</div>
          </div>
          <div>
            <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Controls in the workflow</span><h2 className="mt-3 font-serif text-[34px] font-bold leading-tight">Accountability is visible where the work happens.</h2>
            <div className="mt-7 space-y-3">{CONTROLS.map(([title, body], index) => <article key={title} className="grid grid-cols-[34px_1fr] gap-3 rounded-xl border border-brand-line bg-white p-4"><span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-ink text-[9px] font-black text-white">0{index + 1}</span><div><h3 className="text-[12px] font-bold">{title}</h3><p className="mt-1.5 text-[10.5px] leading-relaxed text-brand-muted">{body}</p></div></article>)}</div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="relative overflow-hidden rounded-[30px] bg-brand-accent px-7 py-12 text-white shadow-xl sm:px-10 md:px-16 md:py-16">
          <div className="absolute -right-16 -top-24 h-64 w-64 rounded-full bg-white/10" aria-hidden="true" />
          <div className="relative grid gap-8 lg:grid-cols-[1fr_auto] lg:items-end">
            <div><span className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/60">Make the demo about your firm</span><h2 className="mt-3 max-w-2xl font-serif text-[34px] font-bold leading-tight text-white md:text-[42px]">Bring us one workflow that keeps falling between systems.</h2><p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-white/72">We’ll walk it from intake to review, client action, and follow-through—then show which modules, integrations, and rollout controls apply.</p></div>
            <div className="flex flex-wrap gap-3 lg:justify-end"><Link to={demoUrl} className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-white px-6 text-[13px] font-bold text-brand-ink hover:bg-brand-bg">Book a workflow demo <ArrowRight size={16} aria-hidden="true" /></Link><Link to="/pricing" className="inline-flex min-h-12 items-center rounded-lg border border-white/25 px-6 text-[13px] font-bold text-white hover:bg-white/10">View pricing</Link></div>
          </div>
        </div>
      </section>
    </MarketingPageLayout>
  )
}
