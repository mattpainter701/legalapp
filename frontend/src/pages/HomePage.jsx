import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ShieldCheck, BadgeCheck, Scale, Lock, Landmark, Building2, UserCircle,
  Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake, ArrowRight,
  Gavel, FileText, Plug, FolderInput, MonitorSmartphone, Sparkles,
  PhoneIncoming, ListChecks, CheckCircle2, Clock3, ChevronDown,
} from 'lucide-react'
import balancedAccessImg from '../assets/home/balanced-access-record-editorial-v1-1280.webp'
import balancedAccessSmallImg from '../assets/home/balanced-access-record-editorial-v1-720.webp'
import secureArchiveImg from '../assets/home/secure-source-archive-cta-v1-1280.webp'
import secureArchiveSmallImg from '../assets/home/secure-source-archive-cta-v1-720.webp'
import WellPledLogo from '../components/WellPledLogo'

const SKILLS = [
  { icon: Scale, name: 'Commercial Legal', description: 'Contract review, NDA triage, SaaS analysis, renewal tracking' },
  { icon: Lock, name: 'Privacy Legal', description: 'DPA review, DSAR responses, Privacy Impact Assessments' },
  { icon: Landmark, name: 'Litigation Legal', description: 'Matter intake, portfolio management, demand letters, claim charts' },
  { icon: Building2, name: 'Corporate Legal', description: 'M&A diligence, closing checklists, entity compliance' },
  { icon: UserCircle, name: 'Employment Legal', description: 'Hire/termination review, worker classification, leave tracking' },
  { icon: Rocket, name: 'Product Legal', description: 'Launch reviews, marketing claims check, regulatory triage' },
  { icon: Lightbulb, name: 'IP Legal', description: 'Trademark clearance, freedom-to-operate, C&D letters' },
  { icon: Bot, name: 'AI Governance', description: 'AI use-case triage, impact assessments, vendor AI review' },
  { icon: ClipboardList, name: 'Regulatory Legal', description: 'Regulatory monitoring, policy gap analysis, NPRM comments' },
]

const ADDONS = [
  {
    id: 'estate',
    icon: Vault,
    name: 'Trust & Estate management',
    description: 'Estate portfolios with role-aware access for trustees, grantors, and beneficiaries \u2014 asset tracking, tax analysis, and probate records organized for review.',
    example: 'Hamilton Family Estate',
    status: 'Attorney review',
    roles: ['Attorney \u00b7 full review', 'Trustee \u00b7 update assets', 'Beneficiary \u00b7 view approved'],
    steps: [
      { title: 'Open the estate', body: 'Capture fiduciaries, beneficiaries, key dates, and the source documents in one record.' },
      { title: 'Build the inventory', body: 'Organize assets, liabilities, valuations, ownership, and the supporting evidence.' },
      { title: 'Review tax & probate', body: 'Surface filing tasks, open questions, and attorney checkpoints before work advances.' },
      { title: 'Share the approved record', body: 'Prepare a beneficiary-ready report from the information the firm has reviewed.' },
    ],
    outcomes: ['One current asset record', 'Role-aware updates', 'Reviewable reporting trail'],
  },
  {
    id: 'mediation',
    icon: Handshake,
    name: 'Mediation management',
    description: 'A neutral two-party workspace \u2014 intake, briefs, settlement drafting, and case tracking with balanced access for each side.',
    example: 'Rivera v. Northwind',
    status: 'Proposal pending',
    roles: ['Mediator \u00b7 neutral view', 'Party A \u00b7 private workspace', 'Party B \u00b7 private workspace'],
    steps: [
      { title: 'Bring in both sides', body: 'Collect party details, scheduling context, conflicts, and the issues in dispute.' },
      { title: 'Organize private briefs', body: 'Keep each side\u2019s submissions and supporting material in the right access boundary.' },
      { title: 'Work the issue board', body: 'Track shared facts, open issues, calculations, and mediator notes without losing context.' },
      { title: 'Record the resolution', body: 'Draft proposals, capture approvals, and preserve the path to the final agreement.' },
    ],
    outcomes: ['Balanced party access', 'Visible issue status', 'Recorded approval trail'],
  },
]

const HOW = [
  {
    icon: PhoneIncoming,
    title: 'Capture the whole story',
    body: 'Bring caller details, context, prior contact, and the intended outcome into one intake record.',
  },
  {
    icon: ListChecks,
    title: 'Put the next step in motion',
    body: 'Turn the intake into an owned task, deadline, matter, or follow-up without re-entering the same facts.',
  },
  {
    icon: BadgeCheck,
    title: 'Review before you rely',
    body: 'Keep source links, confidence cues, and attorney approval in the workflow when AI-assisted work is used.',
  },
]

const FEATURES = [
  {
    icon: Gavel,
    title: 'Source-linked legal research',
    body: 'When public-law sources are configured, research can include citations and confidence labels for attorney verification.',
  },
  {
    icon: Plug,
    title: 'Microsoft 365 & Google Drive sources',
    body: 'Connect supported Microsoft and Google cloud sources so authorized users can bring documents into matter workflows.',
  },
  {
    icon: FolderInput,
    title: 'Drag, drop & file-share access',
    body: 'Drag files in, or connect enterprise file shares so WellPled reads from the documents your firm already keeps.',
  },
  {
    icon: ClipboardList,
    title: 'Call Intake & Zoom Phone',
    body: 'Capture caller details, history, outcomes, and assigned follow-up tasks. Configured Zoom Phone webhooks can add call records to the intake queue.',
  },
  {
    icon: MonitorSmartphone,
    title: 'Responsive secure web workspace',
    body: 'A responsive web workspace keeps your matters available across modern desktop and mobile browsers.',
  },
  {
    icon: Lock,
    title: 'Tenant-isolated document storage',
    body: 'Firm workspaces are isolated by tenant. Storage encryption and model-provider data handling depend on your configured infrastructure, provider, and tenant policy.',
  },
]

function AddonDisclosureButton({ addon, isOpen, onToggle }) {
  const { id, icon: Icon, name, description } = addon
  const buttonId = id + '-workflow-toggle'
  const panelId = id + '-workflow-panel'

  return (
    <button
      id={buttonId}
      type="button"
      aria-expanded={isOpen}
      aria-controls={panelId}
      onClick={onToggle}
      className={
        'group w-full rounded-2xl border bg-brand-surface p-6 text-left shadow-sm transition-all md:order-none ' +
        'motion-reduce:transition-none focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent-2 ' +
        (id === 'estate' ? 'order-1 ' : 'order-3 ') +
        (isOpen
          ? 'border-brand-line-2 shadow-md'
          : 'border-brand-line hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-md motion-reduce:hover:translate-y-0')
      }
    >
      <div className="flex items-start gap-4">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-brand-line bg-brand-bg-soft text-brand-accent-2">
          <Icon size={24} strokeWidth={1.5} aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <h3 className="font-serif text-[18px] font-bold leading-tight">{name}</h3>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-brand-line bg-brand-bg-soft text-brand-accent-2">
              <ChevronDown
                size={16}
                aria-hidden="true"
                className={'transition-transform motion-reduce:transition-none ' + (isOpen ? 'rotate-180' : '')}
              />
            </span>
          </div>
          <p className="mt-1.5 font-sans text-[14px] leading-relaxed text-brand-ink-2">{description}</p>
          <span className="mt-4 inline-flex items-center gap-2 font-sans text-[11px] font-bold uppercase tracking-[0.12em] text-brand-accent-2">
            <span className="h-px w-5 bg-brand-accent-2/50" aria-hidden="true" />
            {isOpen ? 'Close workflow' : 'Explore workflow'}
          </span>
        </div>
      </div>
    </button>
  )
}

function AddonWorkflowPanel({ addon, isOpen }) {
  const { id, name, example, status, roles, steps, outcomes } = addon

  return (
    <div
      id={id + '-workflow-panel'}
      role="region"
      aria-labelledby={id + '-workflow-toggle'}
      hidden={!isOpen}
      className={id === 'estate'
        ? 'order-2 md:order-none md:col-span-2'
        : 'order-4 md:order-none md:col-span-2'}
    >
      <div className="relative overflow-hidden rounded-3xl border border-brand-ink/10 bg-brand-ink p-1 shadow-xl">
        <div className="absolute -right-16 -top-20 h-56 w-56 rounded-full bg-brand-gold/20 blur-3xl" aria-hidden="true" />
        <div className="absolute -bottom-24 -left-16 h-56 w-56 rounded-full bg-brand-green/20 blur-3xl" aria-hidden="true" />
        <div className="relative overflow-hidden rounded-[20px] bg-brand-surface">
          <div className="flex flex-col gap-4 bg-brand-ink px-5 py-5 text-white sm:flex-row sm:items-center sm:justify-between sm:px-7">
            <div>
              <p className="font-sans text-[10px] font-bold uppercase tracking-[0.18em] text-white/55">
                Illustrative {name} workflow
              </p>
              <p className="mt-1.5 font-serif text-[21px] font-bold">{example}</p>
            </div>
            <span className="inline-flex w-fit items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1.5 font-sans text-[11px] font-bold text-white">
              <span className="h-2 w-2 rounded-full bg-brand-gold shadow-[0_0_0_4px_rgba(184,150,90,0.15)]" aria-hidden="true" />
              {status}
            </span>
          </div>

          <div className="p-5 sm:p-7">
            <div className="flex flex-col gap-3 border-b border-brand-line pb-5 sm:flex-row sm:items-center sm:justify-between">
              <p className="font-sans text-[11px] font-bold uppercase tracking-[0.14em] text-brand-muted">
                Access follows the work
              </p>
              <div className="flex flex-wrap gap-2" aria-label="Workflow roles">
                {roles.map((role) => (
                  <span key={role} className="rounded-full border border-brand-line bg-brand-bg-soft px-2.5 py-1 font-sans text-[10.5px] font-semibold text-brand-ink-2">
                    {role}
                  </span>
                ))}
              </div>
            </div>

            <ol className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label={name + ' stages'}>
              {steps.map((step, index) => (
                <li key={step.title} className="relative rounded-2xl border border-brand-line bg-brand-surface-2 p-4 shadow-sm">
                  <div className="flex items-center gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-brand-ink font-serif text-[12px] font-bold text-white">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="font-sans text-[9px] font-bold uppercase tracking-[0.15em] text-brand-accent-2">
                      Stage
                    </span>
                  </div>
                  <h4 className="mt-4 font-serif text-[15px] font-bold leading-tight text-brand-ink">{step.title}</h4>
                  <p className="mt-2 font-sans text-[12px] leading-relaxed text-brand-ink-2">{step.body}</p>
                  {index < steps.length - 1 && (
                    <span className="absolute -right-[13px] top-1/2 z-10 hidden h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full border border-brand-line bg-white text-brand-gold shadow-sm lg:flex" aria-hidden="true">
                      <ArrowRight size={13} />
                    </span>
                  )}
                </li>
              ))}
            </ol>

            <div className="mt-5 flex flex-col gap-3 rounded-2xl border border-brand-green/20 bg-brand-green/5 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="font-sans text-[10px] font-bold uppercase tracking-[0.15em] text-brand-green">
                What stays visible
              </p>
              <div className="flex flex-wrap gap-x-5 gap-y-2">
                {outcomes.map((outcome) => (
                  <span key={outcome} className="inline-flex items-center gap-1.5 font-sans text-[11.5px] font-semibold text-brand-ink-2">
                    <CheckCircle2 size={14} className="text-brand-green" aria-hidden="true" />
                    {outcome}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function HomePage() {
  const [expandedAddon, setExpandedAddon] = useState(null)
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:contact@perevagagroup.com'
  const intakeStartUrl = import.meta.env.VITE_PUBLIC_SIGNUP_ENABLED === 'true'
    ? '/signup?plan=intake-only'
    : contactUrl
  const scrollTo = (id) => (e) => {
    e.preventDefault()
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <div className="min-h-screen bg-brand-bg text-brand-ink">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-brand-ink focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white"
      >
        Skip to main content
      </a>
      {/* ── Top nav ───────────────────────────────────────────────── */}
      <header className="sticky top-0 z-40 bg-brand-bg/85 backdrop-blur border-b border-brand-line">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <Link to="/" aria-label="WellPled home" className="rounded-lg">
            <WellPledLogo compact />
          </Link>
          <nav aria-label="Marketing" className="hidden lg:flex items-center gap-7 text-[14px] font-sans font-medium text-brand-ink-2">
            <a href="#how" onClick={scrollTo('how')} className="inline-flex min-h-11 items-center hover:text-brand-ink transition-colors">How it works</a>
            <a href="#features" onClick={scrollTo('features')} className="inline-flex min-h-11 items-center hover:text-brand-ink transition-colors">Platform</a>
            <a href="#skills" onClick={scrollTo('skills')} className="inline-flex min-h-11 items-center hover:text-brand-ink transition-colors">Practice areas</a>
            <a href="#security" onClick={scrollTo('security')} className="inline-flex min-h-11 items-center hover:text-brand-ink transition-colors">Security</a>
          </nav>
          <div className="flex items-center gap-2 sm:gap-3">
            <Link to="/login" className="inline-flex min-h-11 items-center px-2 text-[14px] font-sans font-semibold text-brand-ink hover:text-brand-accent-2 transition-colors">
              Sign in
            </Link>
            <a href={contactUrl} className="hidden sm:inline-flex min-h-11 items-center gap-2 px-4 py-2 bg-brand-ink text-white text-[14px] font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px]">
              Request a walkthrough
            </a>
            <a href={contactUrl} className="inline-flex sm:hidden min-h-11 items-center px-3 py-2 bg-brand-ink text-white text-[13px] font-sans font-semibold rounded-xl">
              Get started
            </a>
          </div>
        </div>
      </header>

      <main id="main-content" tabIndex="-1">
      {/* ── Hero ──────────────────────────────────────────────────── */}
      <section className="max-w-6xl mx-auto px-5 sm:px-6 pt-10 pb-14 md:pt-20 md:pb-20">
        <div className="grid lg:grid-cols-[1.02fr_0.98fr] gap-10 xl:gap-16 items-center">
          <div>
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">
              Firm operations · source-aware AI
            </span>
            <h1 className="font-serif font-bold text-[42px] sm:text-[50px] md:text-[58px] leading-[1.02] tracking-tight mt-4">
              Practice,{' '}
              <em className="italic text-brand-accent-2">well played.</em>
            </h1>
            <p className="text-brand-ink-2 font-sans text-[17px] sm:text-[18px] leading-relaxed mt-5 max-w-xl">
              Every fact, document, deadline, decision, and next action held in context—so
              your team can keep work moving without reconstructing the story.
            </p>
            <div className="grid sm:flex sm:flex-wrap items-center gap-3 mt-7">
              <a href={intakeStartUrl} className="inline-flex min-h-12 items-center justify-center gap-2 px-6 py-3.5 bg-brand-ink text-white font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px]">
                Start with Call Intake <ArrowRight size={18} />
              </a>
              <a href="#how" onClick={scrollTo('how')} className="inline-flex min-h-12 items-center justify-center gap-2 px-6 py-3.5 bg-brand-surface border border-brand-line text-brand-ink font-sans font-semibold rounded-xl hover:border-brand-ink hover:bg-brand-bg-soft transition-all shadow-sm">
                See the workflow
              </a>
            </div>
            <div className="flex flex-col sm:flex-row sm:flex-wrap gap-x-6 gap-y-2 mt-7 text-[13px] font-sans text-brand-muted">
              <span className="inline-flex items-center gap-2"><CheckCircle2 size={15} className="text-brand-accent-2" /> Start with one workflow</span>
              <span className="inline-flex items-center gap-2"><CheckCircle2 size={15} className="text-brand-accent-2" /> Attorney-controlled review</span>
              <span className="inline-flex items-center gap-2"><CheckCircle2 size={15} className="text-brand-accent-2" /> Tenant-isolated workspace</span>
            </div>
          </div>

          {/* Product preview card */}
          <div className="relative mt-2 lg:mt-0">
            <div className="absolute -inset-4 bg-brand-accent/5 rounded-[28px] blur-xl" aria-hidden />
            <div className="relative bg-brand-surface border border-brand-line rounded-2xl shadow-xl overflow-hidden">
              <div className="flex items-center justify-between px-5 py-3.5 border-b border-brand-line bg-brand-bg-soft/40">
                <div className="flex items-center gap-2">
                  <img src="/brand/wellpled/wellpled-mark.svg" alt="" aria-hidden="true" className="h-5 w-auto" />
                  <span className="font-serif font-semibold text-[14px]">Intake desk</span>
                </div>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold bg-brand-green/10 text-brand-green border border-brand-green/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-brand-green" /> Illustrative workflow
                </span>
              </div>
              <div className="p-4 sm:p-5 space-y-3.5">
                <div className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="inline-flex rounded-full bg-brand-bg-soft px-2 py-1 text-[10px] font-sans font-bold uppercase tracking-[0.12em] text-brand-accent-2">New call</span>
                        <span className="text-[11px] font-sans text-brand-muted">10:42 AM</span>
                      </div>
                      <p className="mt-2 font-serif text-[19px] font-bold text-brand-ink">Maria Torres</p>
                      <p className="mt-0.5 text-[12.5px] font-sans text-brand-muted">(312) 555-0148 · Returning caller</p>
                    </div>
                    <span className="shrink-0 rounded-full border border-brand-amber/20 bg-brand-amber/10 px-2.5 py-1 text-[10.5px] font-sans font-bold text-brand-amber">
                      Needs response
                    </span>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-3">
                    <div className="rounded-xl bg-brand-bg-soft/70 px-3 py-2.5">
                      <p className="text-[10px] font-sans font-bold uppercase tracking-wider text-brand-muted">Purpose</p>
                      <p className="mt-1 text-[12.5px] font-sans font-semibold text-brand-ink">Estate planning</p>
                    </div>
                    <div className="rounded-xl bg-brand-bg-soft/70 px-3 py-2.5">
                      <p className="text-[10px] font-sans font-bold uppercase tracking-wider text-brand-muted">Prior contact</p>
                      <p className="mt-1 text-[12.5px] font-sans font-semibold text-brand-ink">3 months ago</p>
                    </div>
                  </div>
                  <p className="mt-3 text-[12.5px] font-sans leading-relaxed text-brand-ink-2">
                    Wants to update an existing will and ask whether a trust fits the family’s needs.
                  </p>
                </div>
                <div className="rounded-2xl border border-brand-line bg-brand-bg-soft/55 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white text-brand-accent-2 border border-brand-line">
                        <Clock3 size={17} />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[10px] font-sans font-bold uppercase tracking-wider text-brand-muted">Next step</p>
                        <p className="mt-0.5 truncate text-[13px] font-sans font-semibold text-brand-ink">Jordan Lee · call back today</p>
                      </div>
                    </div>
                    <CheckCircle2 size={19} className="shrink-0 text-brand-accent-2" />
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-1 text-[11.5px] font-sans font-medium text-brand-muted">
                  <span className="inline-flex items-center gap-1.5"><BadgeCheck size={13} className="text-brand-accent-2" /> Caller captured</span>
                  <span className="inline-flex items-center gap-1.5"><ListChecks size={13} className="text-brand-accent-2" /> Follow-up assigned</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Verifiable capability ribbon ───────────────────────── */}
      <section className="border-y border-brand-line bg-brand-bg-soft/40">
        <div className="max-w-6xl mx-auto px-6 py-8">
          <p className="text-center text-[11px] font-sans font-bold uppercase tracking-[0.16em] text-brand-muted mb-5">
            Built for accountable legal work
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { icon: BadgeCheck, label: 'Source-tagged research' },
              { icon: FileText, label: 'Attorney-controlled drafts' },
              { icon: Plug, label: 'Microsoft, Google & Zoom' },
              { icon: Lock, label: 'Tenant-isolated workspace' },
            ].map(({ icon: Icon, label }) => (
              <div key={label} className="flex items-center justify-center gap-2.5 rounded-xl border border-brand-line bg-brand-surface px-4 py-3 text-brand-ink-2 shadow-sm">
                <Icon size={16} strokeWidth={1.75} className="shrink-0 text-brand-accent-2" aria-hidden="true" />
                <span className="font-sans text-[13px] font-semibold">{label}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How it works ──────────────────────────────────────────── */}
      <section id="how" className="max-w-6xl mx-auto px-6 py-16 md:py-24 scroll-mt-20">
        <div className="max-w-2xl">
          <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">From first contact to finished work</span>
          <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
            A clear handoff at every step.
          </h2>
          <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
            Capture the context once, carry it into the work, and keep the next action visible
            without giving up attorney review.
          </p>
        </div>
        <div className="grid md:grid-cols-3 gap-5 mt-10">
          {HOW.map(({ icon: Icon, title, body }, index) => (
            <div key={title} className="relative bg-brand-surface border border-brand-line rounded-2xl p-6 md:p-7">
              <div className="flex items-center justify-between mb-5">
                <div className="w-12 h-12 rounded-xl bg-brand-bg-soft border border-brand-line flex items-center justify-center text-brand-accent-2">
                  <Icon size={24} strokeWidth={1.75} />
                </div>
                <span className="font-serif text-[28px] text-brand-line-2">0{index + 1}</span>
              </div>
              <h3 className="font-serif font-bold text-[19px] mb-2.5">{title}</h3>
              <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Platform features ─────────────────────────────────────── */}
      <section id="features" className="bg-brand-bg-soft/40 border-y border-brand-line scroll-mt-20">
        <div className="max-w-6xl mx-auto px-6 py-16 md:py-24">
          <div className="max-w-2xl mb-10">
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">One calm workspace</span>
            <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
              The operating context your team should not have to reconstruct.
            </h2>
            <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
              Move from intake through documents, deadlines, billing, and source-linked research
              while keeping the work tied to the right people and matter.
            </p>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {FEATURES.map(({ icon: Icon, title, body }) => (
              <div key={title} className="bg-brand-surface border border-brand-line rounded-2xl p-6 hover:shadow-md hover:border-brand-line-2 transition-all">
                <div className="w-11 h-11 rounded-xl bg-brand-bg-soft border border-brand-line flex items-center justify-center text-brand-ink mb-4">
                  <Icon size={22} strokeWidth={1.5} />
                </div>
                <h3 className="font-serif font-bold text-[17px] mb-1.5">{title}</h3>
                <p className="text-brand-ink-2 font-sans text-[13.5px] leading-relaxed">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Review-first workflows ────────────────────────────────── */}
      {/* ── Practice-area skills ──────────────────────────────────── */}
      <section id="skills" className="bg-brand-bg-soft/40 border-y border-brand-line scroll-mt-20">
        <div className="max-w-6xl mx-auto px-6 py-16 md:py-24">
          <div className="md:flex items-end justify-between gap-8 mb-10">
            <div className="max-w-2xl">
              <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Practice-area skills</span>
              <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
                Start with the work your firm does most.
              </h2>
              <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
                Add relevant document patterns, checks, and terminology without forcing
                every team into the same generic workflow.
              </p>
            </div>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {SKILLS.map(({ icon: Icon, name, description }) => (
              <div key={name} className="bg-brand-surface border border-brand-line rounded-2xl p-5">
                <div className="flex items-start gap-4">
                  <div className="w-11 h-11 rounded-xl bg-brand-bg-soft border border-brand-line flex items-center justify-center text-brand-ink shrink-0">
                    <Icon size={22} strokeWidth={1.5} />
                  </div>
                  <div className="min-w-0">
                    <h3 className="font-serif font-bold text-[16px] leading-tight mb-1.5">{name}</h3>
                    <p className="text-brand-ink-2 font-sans text-[13.5px] leading-relaxed">{description}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Optional add-on modules ───────────────────────────────── */}
      <section id="modules" className="max-w-6xl mx-auto px-6 py-16 md:py-24 scroll-mt-20">
        <div className="max-w-2xl mb-10">
          <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Optional add-on modules</span>
          <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
            Add depth where a generic matter view falls short.
          </h2>
          <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
            Trust and estate work and mediation need ongoing portfolios with role-aware
            access for clients and parties. Add those surfaces only when the work calls for them.
          </p>
        </div>
        <div className="grid md:grid-cols-2 gap-6 items-start">
          {ADDONS.map((addon) => (
            <AddonDisclosureButton
              key={addon.id}
              addon={addon}
              isOpen={expandedAddon === addon.id}
              onToggle={() => setExpandedAddon(expandedAddon === addon.id ? null : addon.id)}
            />
          ))}
          {ADDONS.map((addon) => (
            <AddonWorkflowPanel
              key={addon.id + '-panel'}
              addon={addon}
              isOpen={expandedAddon === addon.id}
            />
          ))}
        </div>
      </section>

      {/* ── Both sides of the table ───────────────────────────────── */}
      <section className="max-w-6xl mx-auto px-6 py-16 md:py-24">
        <div className="grid lg:grid-cols-2 gap-14 items-center">
          <div>
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Built for both sides of the table</span>
            <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
              Attorneys keep control. Clients get clarity.
            </h2>
            <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4 max-w-xl">
              Each firm workspace runs role-aware access — for trustees, grantors, and beneficiaries,
              or for two parties in a mediation — surfacing what each person needs and hiding what they don’t.
            </p>
            <div className="flex flex-wrap gap-2.5 mt-7">
              {['Attorney · full edit', 'Trustee · update assets', 'Grantor · update assets', 'Beneficiary · view-only', 'Mediator · neutral view', 'Firm admin · billing & seats'].map((b) => (
                <span key={b} className="px-3 py-1.5 rounded-full text-[12.5px] font-sans font-medium text-brand-ink-2 bg-brand-surface border border-brand-line">
                  {b}
                </span>
              ))}
            </div>
          </div>
          <div className="relative">
            <div className="absolute -inset-3 bg-brand-gold/10 rounded-[28px] -rotate-1" aria-hidden />
            <img
              src={balancedAccessImg}
              srcSet={`${balancedAccessSmallImg} 720w, ${balancedAccessImg} 1280w`}
              sizes="(max-width: 768px) 100vw, 50vw"
              alt="Two role-specific sets of legal documents converging on one organized record"
              loading="lazy"
              className="relative w-full aspect-[3/2] object-cover rounded-2xl shadow-xl border border-brand-line"
            />
          </div>
        </div>
        <div className="grid sm:grid-cols-3 gap-5 mt-14">
          {[
            { n: '01', t: 'Helps keep records current', b: 'Configured reminders and roll-forwards surface work on the cadence you set.' },
            { n: '02', t: 'Supports hard conversations', b: 'In mediation, a shared workspace can organize items, calculations, and recorded approvals.' },
            { n: '03', t: 'Keeps a reviewable trail', b: 'Key values, citations, and approvals can be tracked and included in generated reports.' },
          ].map(({ n, t, b }) => (
            <div key={n} className="bg-brand-surface border border-brand-line rounded-2xl p-6">
              <div className="font-serif text-[26px] text-brand-accent-2">{n}</div>
              <h3 className="font-serif font-bold text-[16px] mt-2 mb-2">{t}</h3>
              <p className="text-brand-ink-2 font-sans text-[13.5px] leading-relaxed">{b}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Pricing ───────────────────────────────────────────────── */}
      <section id="pricing" className="bg-brand-bg-soft/40 border-y border-brand-line scroll-mt-20">
        <div className="max-w-6xl mx-auto px-6 py-16 md:py-24">
          <div className="text-center max-w-2xl mx-auto mb-12">
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">A practical rollout</span>
            <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">Start with one workflow. Expand when it earns its place.</h2>
            <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
              Give the team one clear improvement first. Add broader matter, integration,
              and AI workflows only after scope and access are agreed.
            </p>
          </div>
          <div className="grid lg:grid-cols-2 gap-8 items-stretch max-w-5xl mx-auto">
            {/* Licensing model */}
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8">
              <h3 className="font-serif font-bold text-[22px] mb-2">Begin with the intake desk</h3>
              <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed mb-7">
                Give reception a focused workspace for caller history, outcomes, and assigned
                follow-up tasks. Add Zoom Phone only after its tenant connection is verified.
              </p>
              <ol className="space-y-5">
                {[
                  { icon: ClipboardList, t: 'Capture every caller', b: 'Record contact details, purpose, prior history, and the intake outcome.' },
                  { icon: UserCircle, t: 'Assign clear follow-up', b: 'Create a staff-owned task as part of the intake workflow.' },
                  { icon: Plug, t: 'Connect Zoom when ready', b: 'A verified Zoom Phone connection can feed call records into the same queue.' },
                ].map(({ icon: Icon, t, b }) => (
                  <li key={t} className="flex items-start gap-4">
                    <div className="w-10 h-10 rounded-xl bg-brand-bg-soft border border-brand-line flex items-center justify-center text-brand-accent-2 shrink-0">
                      <Icon size={20} strokeWidth={1.75} />
                    </div>
                    <div>
                      <p className="font-serif font-bold text-[16px]">{t}</p>
                      <p className="text-brand-ink-2 font-sans text-[13.5px] leading-relaxed mt-0.5">{b}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
            {/* Price card */}
            <div className="bg-brand-surface border-[1.5px] border-brand-ink rounded-2xl p-8 shadow-lg flex flex-col">
              <div className="flex items-center justify-between">
                <h3 className="font-serif font-bold text-[22px]">Add the wider platform</h3>
                <span className="px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold text-brand-muted bg-brand-bg-soft border border-brand-line">configured to fit</span>
              </div>
              <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed mt-5">
                Choose the operational scope first. We document integrations, onboarding,
                support, and commercial terms before anything is enabled in production.
              </p>
              <hr className="border-brand-line my-6" />
              <div className="space-y-3">
                {[
                  { name: 'Call Intake + Tasks', price: 'day one', body: 'Caller tracking, history, outcomes, staff assignment, and optional verified Zoom Phone intake.' },
                  { name: 'Matter platform', price: 'when ready', body: 'Matter, contact, document, task, billing, and approved integration workflows selected for your firm.' },
                  { name: 'AI model access', price: 'controlled', body: 'Model access and usage controls are configured for the users and providers your firm approves.' },
                  { name: 'Practice modules', price: 'optional', body: 'Add specialized workflows only after scope, readiness, and access requirements are agreed.' },
                ].map((plan) => (
                  <div key={plan.name} className="rounded-xl border border-brand-line bg-brand-bg-soft px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="font-serif font-bold text-[16px] text-brand-ink">{plan.name}</p>
                      <span className="shrink-0 rounded-full bg-white px-2.5 py-1 text-[11px] font-sans font-bold text-brand-accent-2 border border-brand-line">
                        {plan.price}
                      </span>
                    </div>
                    <p className="mt-1 text-[13px] font-sans leading-relaxed text-brand-ink-2">{plan.body}</p>
                  </div>
                ))}
              </div>
              <div className="mt-5 flex items-start gap-2.5 rounded-xl bg-brand-bg-soft border border-brand-line px-4 py-3">
                <Sparkles size={16} className="text-brand-gold shrink-0 mt-0.5" />
                <p className="text-[13px] font-sans text-brand-ink-2 leading-relaxed">
                  <span className="font-semibold text-brand-ink">Module independence by design.</span>{' '}
                  Intake-only firms can log into just the call tracker. Standard and premium firms see
                  the broader workflows their tenant has licensed.
                </p>
              </div>
              <a href={intakeStartUrl} className="block w-full mt-7 py-3 rounded-xl text-center font-sans font-semibold text-[14px] bg-brand-ink text-white hover:bg-brand-ink-2 transition-all">
                Request a Call Intake workspace
              </a>
              <p className="text-center text-brand-muted font-sans text-[12.5px] mt-4">
                Questions about onboarding or commercial terms?{' '}
                <a href={contactUrl} className="text-brand-accent-2 font-semibold hover:underline">Talk to us</a>.
              </p>
            </div>
          </div>

        </div>
      </section>

      {/* ── Security strip ────────────────────────────────────────── */}
      <section id="security" className="max-w-6xl mx-auto px-6 py-20 md:py-24 scroll-mt-20">
        <div className="bg-brand-ink rounded-3xl px-8 py-14 md:px-16 text-center relative overflow-hidden">
          <img src={secureArchiveImg} srcSet={`${secureArchiveSmallImg} 720w, ${secureArchiveImg} 1280w`} sizes="(max-width: 768px) 100vw, 1152px" alt="" aria-hidden loading="lazy" className="absolute inset-0 w-full h-full object-cover opacity-30" />
          <div className="absolute inset-0 bg-brand-ink/80" aria-hidden />
          <div className="relative">
            <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-[12px] font-sans font-semibold bg-white/10 text-white border border-white/15">
              <ShieldCheck size={14} /> Your firm’s data stays isolated
            </span>
            <h2 className="font-serif font-bold text-[34px] md:text-[40px] text-white leading-tight mt-6">
              Start focused. Add the broader platform when you need it.
            </h2>
            <p className="text-white/70 font-sans text-[17px] leading-relaxed mt-4 max-w-2xl mx-auto">
              Begin with Call Intake and Tasks, connect your team, and expand into the
              broader matter platform when the workflow is right for your firm.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3 mt-8">
              <a href={intakeStartUrl} className="inline-flex items-center gap-2 px-6 py-3.5 bg-white text-brand-ink font-sans font-semibold rounded-xl hover:bg-brand-bg transition-all shadow-sm">
                Start with Call Intake <ArrowRight size={18} />
              </a>
              <a href={contactUrl} className="inline-flex items-center gap-2 px-6 py-3.5 bg-transparent border border-white/25 text-white font-sans font-semibold rounded-xl hover:bg-white/10 transition-all">
                Request a 20-min walkthrough
              </a>
            </div>
          </div>
        </div>
      </section>
      </main>

      {/* ── Footer ────────────────────────────────────────────────── */}
      <footer className="border-t border-brand-line">
        <div className="max-w-6xl mx-auto px-6 py-10 flex flex-col sm:flex-row items-center justify-between gap-4">
          <WellPledLogo compact />
          <p className="text-brand-gold font-serif italic text-[14px] tracking-wide">Practice, well played.</p>
          <div className="flex items-center gap-4 text-brand-muted font-sans text-[12.5px]">
            <Link to="/privacy" className="inline-flex min-h-11 items-center hover:text-brand-ink">Privacy</Link>
            <Link to="/terms" className="inline-flex min-h-11 items-center hover:text-brand-ink">Terms</Link>
            <span>© {new Date().getFullYear()} WellPled</span>
          </div>
        </div>
      </footer>
    </div>
  )
}
