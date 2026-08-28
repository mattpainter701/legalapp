import { Link } from 'react-router-dom'
import {
  ArrowRight, Bot, Braces, Building2, CalendarClock, CheckCircle2, ClipboardList,
  Files, FileSignature, FolderInput, Home, Inbox, Landmark, Lightbulb, Lock, MessageSquareText,
  Receipt, Rocket, Scale, ShieldCheck, UserCircle, Users, Vault, Handshake,
} from 'lucide-react'
import MarketingPageLayout from '../components/MarketingChrome'
import { PRACTICE_SKILLS, WORKSPACE_MODULES } from '../marketing/catalog'

const CATALOG_ICONS = {
  Files, Lock, Landmark, Building2, UserCircle, Rocket, Lightbulb, Bot,
  ClipboardList, Home, Scale, Vault, Users, Handshake,
}

const CORE_SURFACES = [
  {
    icon: ClipboardList,
    title: 'Intake and tasks',
    body: 'Capture caller details, purpose, prior contact, and outcome, then assign owned follow-up work from the same record.',
  },
  {
    icon: Landmark,
    title: 'Matters and contacts',
    body: 'Hold the parties, documents, correspondence, and history for a matter in one place instead of across mailboxes and drives.',
  },
  {
    icon: Inbox,
    title: 'Correspondence and matter email',
    body: 'Capture mail from a connected mailbox, and — where the deployment enables it — give each matter its own forwarding address. Forwarded mail waits for a person to file it before it becomes matter correspondence.',
  },
  {
    icon: CalendarClock,
    title: 'Calendar and deadlines',
    body: 'Keep matter dates and follow-up commitments visible, with supported Microsoft and Google calendar connections.',
  },
  {
    icon: FolderInput,
    title: 'Documents and automation',
    body: 'Drag files in, connect approved cloud sources, and generate documents from firm templates.',
  },
  {
    icon: Receipt,
    title: 'Time, billing, and trust',
    body: 'Record time, produce invoices, run reports, and keep trust accounting separate from operating funds.',
  },
  {
    icon: FileSignature,
    title: 'Client portal and signature',
    body: 'Share what a client or participant is entitled to see, collect what you need back, and route documents for signature.',
  },
  {
    icon: Bot,
    title: 'Connected assistants',
    body: 'Where a firm enables it, a user can connect an approved external assistant to their own workspace by explicit consent — scoped, audit logged, and revocable by the user or an administrator.',
  },
]

const CONTROLS = [
  ['Tenant isolation', 'Firm workspaces are isolated by tenant. Storage encryption and model-provider handling additionally depend on your configured infrastructure and tenant policy.'],
  ['Role-aware access', 'Module roles decide what each person can see, update, or approve — internal reviewers, clients, counterparties, fiduciaries, and neutrals alike.'],
  ['Review before reliance', 'Source links and confidence cues travel with AI-assisted work so an attorney can verify before anything leaves the firm.'],
  ['Administrative visibility', 'Firm administrators control users, roles, enabled modules, and connected services from the administration workspace.'],
]

const INTEGRATIONS = [
  ['Microsoft 365', 'Outlook mail and calendar, OneDrive, and SharePoint sources'],
  ['Google Workspace', 'Gmail, Google Calendar, and Google Drive sources'],
  ['Microsoft Teams', 'A LawHand tab inside the Teams client'],
  ['Zoom Phone', 'Verified call-log webhooks feeding the intake queue'],
  ['QuickBooks Online', 'Accounting sync for billing workflows'],
  ['Enterprise file shares', 'Supported SMB shares the firm already maintains'],
]

export default function ProductPage() {
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:support@getlawhand.com'

  return (
    <MarketingPageLayout>
      <section className="mx-auto max-w-6xl px-6 pb-14 pt-16 md:pb-20 md:pt-24">
        <div className="max-w-3xl">
          <span className="font-sans text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">The LawHand platform</span>
          <h1 className="mt-5 font-serif text-[44px] font-bold leading-[1.04] tracking-tight md:text-[60px]">
            One workspace for the whole matter.
          </h1>
          <p className="mt-6 max-w-2xl font-sans text-[18px] leading-relaxed text-brand-ink-2">
            LawHand holds intake, matters, documents, deadlines, billing, and source-aware
            research in a single tenant-isolated workspace — then layers practice-area skills
            and matter-aware AI chat on top of the record your firm already keeps.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a href={contactUrl} className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-brand-accent px-6 font-sans text-[14px] font-semibold text-white shadow-sm transition-all hover:-translate-y-px hover:bg-brand-accent-2">
              Book a demo <ArrowRight size={17} aria-hidden="true" />
            </a>
            <Link to="/pricing" className="inline-flex min-h-12 items-center rounded-lg border border-brand-line-2 bg-brand-surface px-6 font-sans text-[14px] font-semibold text-brand-ink transition-colors hover:border-brand-ink">
              View pricing
            </Link>
          </div>
        </div>

        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {CORE_SURFACES.map(({ icon: Icon, title, body }) => (
            <article key={title} className="rounded-2xl border border-brand-line bg-brand-surface p-6 transition-all hover:border-brand-line-2 hover:shadow-md">
              <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-brand-line bg-brand-bg-soft text-brand-ink">
                <Icon size={22} strokeWidth={1.5} aria-hidden="true" />
              </div>
              <h2 className="font-serif text-[17px] font-bold">{title}</h2>
              <p className="mt-1.5 font-sans text-[13.5px] leading-relaxed text-brand-ink-2">{body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-24">
          <div className="max-w-2xl">
            <span className="font-sans text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Practice-area library</span>
            <h2 className="mt-3 font-serif text-[34px] font-bold leading-tight">
              {PRACTICE_SKILLS.length} skill libraries, {WORKSPACE_MODULES.length} dedicated workspaces.
            </h2>
            <p className="mt-4 font-sans text-[17px] leading-relaxed text-brand-ink-2">
              Skill libraries bring the document patterns, checks, and terminology of a practice
              area to the shared matter record. Dedicated workspaces go further, adding their own
              records, roles, and routes.
            </p>
          </div>

          <div className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {PRACTICE_SKILLS.map((skill) => {
              const Icon = CATALOG_ICONS[skill.icon]
              return (
                <article key={skill.id} className="flex items-start gap-3.5 rounded-2xl border border-brand-line bg-brand-surface px-4 py-4">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-brand-line bg-brand-bg-soft text-brand-accent-2">
                    <Icon size={19} strokeWidth={1.6} aria-hidden="true" />
                  </span>
                  <span className="min-w-0">
                    <h3 className="font-serif text-[15px] font-bold leading-tight">{skill.name}</h3>
                    <p className="mt-1 font-sans text-[12.5px] leading-relaxed text-brand-ink-2">{skill.description}</p>
                  </span>
                </article>
              )
            })}
          </div>

          <div className="mt-5 grid gap-5 md:grid-cols-3">
            {WORKSPACE_MODULES.map((module) => {
              const Icon = CATALOG_ICONS[module.icon]
              return (
                <article key={module.id} className="rounded-2xl border border-brand-ink/12 bg-brand-ink p-6 text-white">
                  <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/10">
                    <Icon size={21} strokeWidth={1.5} aria-hidden="true" />
                  </div>
                  <h3 className="mt-5 font-serif text-[18px] font-bold text-white">{module.name}</h3>
                  <p className="mt-2 font-sans text-[13px] leading-relaxed text-white/65">{module.description}</p>
                  <ul className="mt-4 space-y-2">
                    {module.features.map((feature) => (
                      <li key={feature} className="flex items-start gap-2.5 font-sans text-[12.5px] leading-relaxed text-white/80">
                        <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-brand-gold" aria-hidden="true" />{feature}
                      </li>
                    ))}
                  </ul>
                </article>
              )
            })}
          </div>

          <p className="mt-6 font-sans text-[13px] leading-relaxed text-brand-ink-2">
            Practice areas are enabled for selected firms during controlled onboarding.{' '}
            <a href={contactUrl} className="font-bold text-brand-accent-2 hover:underline">Tell us which ones you need</a>.
          </p>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr]">
          <div>
            <span className="font-sans text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Where the intelligence lives</span>
            <h2 className="mt-3 font-serif text-[34px] font-bold leading-tight">Work with the matter, or bring the matter to your tools.</h2>
            <p className="mt-4 font-sans text-[16px] leading-relaxed text-brand-ink-2">
              Matter-aware AI chat runs inside LawHand. LawHand MCP takes the opposite direction,
              exposing a bounded set of tools to an approved external system.
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Link to="/product/chat" className="group rounded-2xl border border-brand-line bg-brand-surface p-6 transition-all hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-md motion-reduce:hover:translate-y-0">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-accent text-white">
                <MessageSquareText size={21} aria-hidden="true" />
              </div>
              <h3 className="mt-5 font-serif text-[19px] font-bold">Matter-aware AI chat</h3>
              <p className="mt-2 font-sans text-[13.5px] leading-relaxed text-brand-ink-2">
                Research, review, summarize, and draft with the active matter and authorized sources in context.
              </p>
              <span className="mt-4 inline-flex items-center gap-2 font-sans text-[13px] font-bold text-brand-accent-2">
                Explore AI Chat <ArrowRight size={14} aria-hidden="true" className="transition-transform group-hover:translate-x-0.5" />
              </span>
            </Link>
            <Link to="/product/mcp" className="group rounded-2xl border border-brand-line bg-brand-surface p-6 transition-all hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-md motion-reduce:hover:translate-y-0">
              <div className="flex items-start justify-between gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-brand-line bg-brand-bg-soft text-brand-accent-2">
                  <Braces size={21} aria-hidden="true" />
                </div>
                <span className="rounded-full border border-brand-gold/30 bg-brand-gold/10 px-2.5 py-1 font-sans text-[9px] font-bold uppercase tracking-[0.12em] text-brand-gold">Controlled pilot</span>
              </div>
              <h3 className="mt-5 font-serif text-[19px] font-bold">LawHand MCP</h3>
              <p className="mt-2 font-sans text-[13.5px] leading-relaxed text-brand-ink-2">
                Scoped keys, allowlisted tools, bounded usage, and visible activity for approved integrations.
              </p>
              <span className="mt-4 inline-flex items-center gap-2 font-sans text-[13px] font-bold text-brand-accent-2">
                Explore MCP <ArrowRight size={14} aria-hidden="true" className="transition-transform group-hover:translate-x-0.5" />
              </span>
            </Link>
          </div>
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-24">
          <div className="grid gap-10 lg:grid-cols-2">
            <div>
              <span className="font-sans text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Connected sources</span>
              <h2 className="mt-3 font-serif text-[30px] font-bold leading-tight">Read from the systems your firm already runs.</h2>
              <p className="mt-4 font-sans text-[15px] leading-relaxed text-brand-ink-2">
                Each connection is enabled by a firm administrator and can be disconnected at any
                time. Available integrations depend on your subscription and tenant configuration.
              </p>
              <dl className="mt-7 divide-y divide-brand-line rounded-2xl border border-brand-line bg-brand-surface px-5">
                {INTEGRATIONS.map(([name, detail]) => (
                  <div key={name} className="flex flex-col gap-1 py-3.5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-6">
                    <dt className="font-sans text-[13.5px] font-bold text-brand-ink">{name}</dt>
                    <dd className="font-sans text-[12.5px] leading-relaxed text-brand-muted sm:max-w-[62%] sm:text-right">{detail}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div>
              <span className="font-sans text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Controls</span>
              <h2 className="mt-3 font-serif text-[30px] font-bold leading-tight">Accountability is part of the product, not a policy page.</h2>
              <div className="mt-7 space-y-4">
                {CONTROLS.map(([title, body]) => (
                  <article key={title} className="rounded-2xl border border-brand-line bg-brand-surface p-5">
                    <h3 className="flex items-center gap-2.5 font-serif text-[16px] font-bold">
                      <ShieldCheck size={17} className="shrink-0 text-brand-accent-2" aria-hidden="true" />{title}
                    </h3>
                    <p className="mt-2 font-sans text-[13.5px] leading-relaxed text-brand-ink-2">{body}</p>
                  </article>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="rounded-3xl bg-brand-ink px-8 py-12 text-center text-white md:px-16 md:py-16">
          <h2 className="mx-auto max-w-2xl font-serif text-[32px] font-bold leading-tight text-white md:text-[38px]">
            See it against your own matters.
          </h2>
          <p className="mx-auto mt-4 max-w-xl font-sans text-[16px] leading-relaxed text-white/70">
            A walkthrough covers the modules you would enable, the sources you would connect, and
            what onboarding looks like for your firm.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <a href={contactUrl} className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-white px-6 font-sans text-[14px] font-semibold text-brand-ink shadow-sm transition-all hover:-translate-y-px hover:bg-brand-bg">
              Book a demo <ArrowRight size={17} aria-hidden="true" />
            </a>
            <Link to="/pricing" className="inline-flex min-h-12 items-center rounded-lg border border-white/25 px-6 font-sans text-[14px] font-semibold text-white transition-colors hover:bg-white/10">
              View pricing
            </Link>
          </div>
        </div>
      </section>
    </MarketingPageLayout>
  )
}
