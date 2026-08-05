import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ShieldCheck, BadgeCheck, Files, Lock, Landmark, Building2, UserCircle,
  Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake, ArrowRight,
  Search, FileText, Plug, FolderInput, MonitorSmartphone, Sparkles,
  PhoneIncoming, ListChecks, CheckCircle2, Clock3, ChevronDown,
  MessageSquareText, KeyRound, Braces, AlertTriangle, Layers3,
} from 'lucide-react'
import balancedAccessImg from '../assets/home/lawhand-controlled-handoff-editorial-v2-1280.webp'
import balancedAccessSmallImg from '../assets/home/lawhand-controlled-handoff-editorial-v2-720.webp'
import secureArchiveImg from '../assets/home/secure-source-archive-cta-v1-1280.webp'
import secureArchiveSmallImg from '../assets/home/secure-source-archive-cta-v1-720.webp'
import { MarketingFooter, MarketingHeader } from '../components/MarketingChrome'
import MarketingChatWorkspace from '../components/MarketingChatWorkspace'

const SKILLS = [
  {
    id: 'commercial', icon: Files, name: 'Commercial Legal', description: 'Contract review, NDA triage, SaaS analysis, renewal tracking',
    example: 'Apex Cloud · SaaS agreement', status: '2 items need review', signal: '14 clauses checked',
    artifacts: [['Limitation of liability', 'Attorney review'], ['Data processing addendum', 'Playbook match'], ['Renewal terms', '45-day notice']],
    features: ['Compare clauses to the firm playbook', 'Flag missing terms and material deviations', 'Capture obligations, owners, and renewal dates'],
    language: 'Clause library · fallback language · approval thresholds',
  },
  {
    id: 'privacy', icon: Lock, name: 'Privacy Legal', description: 'DPA review, DSAR responses, Privacy Impact Assessments',
    example: 'Atlas Analytics · privacy review', status: 'Response due in 9 days', signal: '6 systems mapped',
    artifacts: [['DPA transfer terms', 'Gap found'], ['DSAR identity check', 'Complete'], ['Processing inventory', '6 systems']],
    features: ['Run DPA checks by jurisdiction and data type', 'Coordinate DSAR identity, search, and response steps', 'Keep PIA evidence and mitigation owners together'],
    language: 'Data subjects · subprocessors · retention · transfer basis',
  },
  {
    id: 'litigation', icon: Landmark, name: 'Litigation Legal', description: 'Matter intake, portfolio management, demand letters, claim charts',
    example: 'Rivera v. Northwind · portfolio', status: 'Deadline in 4 days', signal: '8 claims mapped',
    artifacts: [['Demand response', 'Draft ready'], ['Claim chart', '8 of 11 mapped'], ['Discovery cutoff', 'Oct 14']],
    features: ['Move intake facts into a structured matter', 'Link allegations, evidence, and authority', 'Track portfolio posture, dates, and next actions'],
    language: 'Claims · defenses · elements · evidence · deadlines',
  },
  {
    id: 'corporate', icon: Building2, name: 'Corporate Legal', description: 'M&A diligence, closing checklists, entity compliance',
    example: 'Project Juniper · acquisition', status: '78% closing ready', signal: '42 documents reviewed',
    artifacts: [['Material contracts', '3 exceptions'], ['Closing checklist', '31 of 40'], ['Entity records', 'Current']],
    features: ['Organize diligence findings by workstream', 'Turn issues into owners and closing conditions', 'Maintain entity records and recurring compliance'],
    language: 'Diligence · disclosures · conditions · consents · filings',
  },
  {
    id: 'employment', icon: UserCircle, name: 'Employment Legal', description: 'Hire/termination review, worker classification, leave tracking',
    example: 'Workforce request · California', status: 'Classification review', signal: '3 decision checks',
    artifacts: [['Role classification', 'Needs facts'], ['Termination packet', 'Review ready'], ['Leave timeline', '12 weeks']],
    features: ['Route hire and separation facts through consistent checks', 'Document classification factors and decisions', 'Track leave events, notices, and return dates'],
    language: 'Worker status · protected leave · notice · final pay',
  },
  {
    id: 'product', icon: Rocket, name: 'Product Legal', description: 'Launch reviews, marketing claims check, regulatory triage',
    example: 'Pulse AI · launch review', status: '2 launch blockers', signal: '5 teams aligned',
    artifacts: [['Marketing claims', '2 need support'], ['Terms update', 'Approved'], ['Launch gate', 'Conditional']],
    features: ['Collect one launch brief across product teams', 'Connect claims to substantiation and approvals', 'Route regulatory questions before release'],
    language: 'Claims · audience · data use · disclosures · launch gate',
  },
  {
    id: 'ip', icon: Lightbulb, name: 'IP Legal', description: 'Trademark clearance, freedom-to-operate, C&D letters',
    example: 'Northstar · clearance search', status: 'Moderate conflict risk', signal: '27 records screened',
    artifacts: [['Exact mark search', 'No match'], ['Similar marks', '4 for review'], ['Class coverage', '3 classes']],
    features: ['Keep search strategy and results reviewable', 'Compare marks, classes, owners, and status', 'Move findings into advice or enforcement drafts'],
    language: 'Similarity · classes · use evidence · claim scope',
  },
  {
    id: 'ai-governance', icon: Bot, name: 'AI Governance', description: 'AI use-case triage, impact assessments, vendor AI review',
    example: 'Support copilot · use-case review', status: 'Human oversight required', signal: 'Risk tier · medium',
    artifacts: [['Data inputs', 'Restricted data'], ['Vendor controls', '1 gap'], ['Impact review', 'In progress']],
    features: ['Triage use cases by people, data, and decision impact', 'Standardize impact assessments and control owners', 'Review vendor AI terms alongside technical claims'],
    language: 'Use case · model role · oversight · testing · monitoring',
  },
  {
    id: 'regulatory', icon: ClipboardList, name: 'Regulatory Legal', description: 'Regulatory monitoring, policy gap analysis, NPRM comments',
    example: 'Consumer rules · monitoring file', status: 'Comment window open', signal: '12 obligations tagged',
    artifacts: [['Rule change', 'Material'], ['Policy mapping', '3 gaps'], ['Comment draft', 'Due Sep 8']],
    features: ['Turn regulatory developments into scoped impact reviews', 'Map requirements to policies, controls, and owners', 'Build comment records from evidence and stakeholder input'],
    language: 'Authority · obligation · applicability · policy · comment',
  },
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
    metrics: [['24', 'Assets'], ['$4.8m', 'Gross estate'], ['3', 'Review items']],
    activity: [['Residence valuation', 'Reviewed'], ['Family trust allocation', 'Open'], ['Probate inventory', 'Draft']],
    features: ['Asset and liability inventory', 'Tax and probate checkpoints', 'Beneficiary-ready reporting'],
  },
  {
    id: 'mediation',
    icon: Handshake,
    name: 'Mediation management',
    description: 'A neutral two-party workspace \u2014 intake, briefs, settlement drafting, and case tracking with balanced access for each side.',
    example: 'Rivera v. Northwind',
    status: 'Proposal pending',
    roles: ['Mediator \u00b7 neutral view', 'Party A \u00b7 private workspace', 'Party B \u00b7 private workspace'],
    metrics: [['6', 'Open issues'], ['2', 'Proposals'], ['Sep 18', 'Next session']],
    activity: [['Party A brief', 'Private'], ['Damages range', 'Shared'], ['Draft settlement terms', 'Waiting']],
    features: ['Separate private submissions', 'Shared issue and proposal tracking', 'Settlement drafting and approvals'],
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
    icon: Search,
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
    body: 'Drag files in, or connect enterprise file shares so LawHand reads from the documents your firm already keeps.',
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

function PracticeSkillCard({ skill, isOpen, onToggle }) {
  const { id, icon: Icon, name, description, signal } = skill

  return (
    <button
      id={id + '-skill-toggle'}
      type="button"
      aria-expanded={isOpen}
      aria-controls={id + '-skill-panel'}
      onClick={onToggle}
      className={
        'group relative w-full overflow-hidden rounded-2xl border p-5 text-left shadow-sm transition-all ' +
        'motion-reduce:transition-none focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent-2 ' +
        (isOpen
          ? 'border-brand-accent/40 bg-brand-surface shadow-md'
          : 'border-brand-line bg-brand-surface hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-md motion-reduce:hover:translate-y-0')
      }
    >
      <div className={'absolute inset-x-0 top-0 h-1 bg-brand-accent transition-opacity ' + (isOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-40')} aria-hidden="true" />
      <div className="flex items-start gap-4">
        <span className={'flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border transition-colors ' + (isOpen ? 'border-brand-accent bg-brand-accent text-white' : 'border-brand-line bg-brand-bg-soft text-brand-ink')}>
          <Icon size={21} strokeWidth={1.6} aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-start justify-between gap-3">
            <span className="font-serif text-[16px] font-bold leading-tight text-brand-ink">{name}</span>
            <ChevronDown size={16} aria-hidden="true" className={'mt-0.5 shrink-0 text-brand-muted transition-transform ' + (isOpen ? 'rotate-180' : '')} />
          </span>
          <span className="mt-1.5 block font-sans text-[13px] leading-relaxed text-brand-ink-2">{description}</span>
        </span>
      </div>
      <span className="mt-5 flex items-center justify-between gap-3 border-t border-brand-line pt-3.5">
        <span className="inline-flex items-center gap-2 font-sans text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-accent-2">
          <span className={'h-2 w-2 rounded-full ' + (isOpen ? 'bg-brand-accent' : 'bg-brand-green')} aria-hidden="true" />
          {signal}
        </span>
        <span className="font-sans text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">
          {isOpen ? 'Close' : 'See inside'}
        </span>
      </span>
    </button>
  )
}

function PracticeSkillPanel({ skill, isOpen }) {
  const { id, name, example, status, artifacts, features, language } = skill

  return (
    <div
      id={id + '-skill-panel'}
      role="region"
      aria-labelledby={id + '-skill-toggle'}
      hidden={!isOpen}
      className="mt-6"
    >
      <div className="relative overflow-hidden rounded-3xl border border-brand-ink/10 bg-brand-ink p-1 shadow-xl">
        <div className="absolute -right-16 -top-24 h-64 w-64 rounded-full bg-brand-accent/35 blur-3xl" aria-hidden="true" />
        <div className="relative grid overflow-hidden rounded-[20px] bg-brand-surface lg:grid-cols-[1.18fr_0.82fr]">
          <div className="border-b border-brand-line bg-brand-bg-soft p-5 sm:p-7 lg:border-b-0 lg:border-r">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="font-sans text-[9px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Illustrative {name} workspace</p>
                <h3 className="mt-1.5 font-serif text-[20px] font-bold">{example}</h3>
              </div>
              <span className="inline-flex w-fit items-center gap-2 rounded-full border border-brand-amber/25 bg-brand-amber/10 px-3 py-1.5 font-sans text-[10.5px] font-bold text-brand-ink-2">
                <AlertTriangle size={12} className="text-brand-amber" aria-hidden="true" /> {status}
              </span>
            </div>

            <div className="mt-6 overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm">
              <div className="grid grid-cols-[1fr_auto] border-b border-brand-line px-4 py-3 text-[9px] font-bold uppercase tracking-[0.14em] text-brand-muted">
                <span>Review item</span><span>Status</span>
              </div>
              {artifacts.map(([item, itemStatus], index) => (
                <div key={item} className={'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-4 py-3.5 ' + (index < artifacts.length - 1 ? 'border-b border-brand-line' : '')}>
                  <span className="flex min-w-0 items-center gap-3 font-sans text-[12.5px] font-semibold text-brand-ink">
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-bg-soft text-brand-accent-2"><FileText size={13} aria-hidden="true" /></span>
                    <span className="truncate">{item}</span>
                  </span>
                  <span className="rounded-full border border-brand-line bg-brand-surface-2 px-2.5 py-1 font-sans text-[9.5px] font-bold text-brand-ink-2">{itemStatus}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="flex flex-col p-5 sm:p-7">
            <div className="flex items-center gap-2 text-brand-accent-2">
              <Layers3 size={17} aria-hidden="true" />
              <p className="font-sans text-[10px] font-bold uppercase tracking-[0.15em]">What the skill adds</p>
            </div>
            <ul className="mt-5 space-y-4">
              {features.map((feature) => (
                <li key={feature} className="flex items-start gap-3 font-sans text-[13px] font-semibold leading-relaxed text-brand-ink-2">
                  <CheckCircle2 size={17} className="mt-0.5 shrink-0 text-brand-green" aria-hidden="true" />
                  {feature}
                </li>
              ))}
            </ul>
            <div className="mt-6 rounded-2xl border border-brand-line bg-brand-bg-soft px-4 py-3.5 lg:mt-auto">
              <p className="font-sans text-[9px] font-bold uppercase tracking-[0.14em] text-brand-muted">Feels native to the work</p>
              <p className="mt-1.5 font-sans text-[11.5px] font-semibold leading-relaxed text-brand-ink-2">{language}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function AddonCard({ addon, isOpen, onToggle }) {
  const { id, icon: Icon, name, description, example, status, roles, metrics, activity, features } = addon

  return (
    <article className={'overflow-hidden rounded-3xl border bg-brand-surface shadow-sm transition-shadow ' + (isOpen ? 'border-brand-line-2 shadow-lg' : 'border-brand-line')}>
      <button
        id={id + '-workflow-toggle'}
        type="button"
        aria-expanded={isOpen}
        aria-controls={id + '-workflow-panel'}
        onClick={onToggle}
        className="group w-full p-6 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-accent-2 sm:p-7"
      >
        <span className="flex items-start gap-4">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-brand-line bg-brand-bg-soft text-brand-accent-2">
            <Icon size={23} strokeWidth={1.5} aria-hidden="true" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-start justify-between gap-3">
              <span className="font-serif text-[19px] font-bold leading-tight text-brand-ink">{name}</span>
              <ChevronDown size={17} aria-hidden="true" className={'mt-0.5 shrink-0 text-brand-muted transition-transform ' + (isOpen ? 'rotate-180' : '')} />
            </span>
            <span className="mt-2 block font-sans text-[13.5px] leading-relaxed text-brand-ink-2">{description}</span>
          </span>
        </span>

        <span className="mt-6 block overflow-hidden rounded-2xl border border-brand-line bg-brand-bg-soft text-brand-ink shadow-inner">
          <span className="flex items-center justify-between gap-3 border-b border-brand-line bg-brand-ink px-4 py-3 text-white">
            <span>
              <span className="block font-sans text-[8px] font-bold uppercase tracking-[0.15em] text-white/45">Example workspace</span>
              <span className="mt-0.5 block font-serif text-[13px] font-bold text-white">{example}</span>
            </span>
            <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1 font-sans text-[9px] font-bold text-white/85">{status}</span>
          </span>
          <span className="grid grid-cols-3 border-b border-brand-line bg-brand-surface">
            {metrics.map(([value, label], index) => (
              <span key={label} className={'px-3 py-3 text-center ' + (index ? 'border-l border-brand-line' : '')}>
                <span className="block font-serif text-[16px] font-bold text-brand-ink">{value}</span>
                <span className="mt-0.5 block font-sans text-[8.5px] font-bold uppercase tracking-[0.1em] text-brand-muted">{label}</span>
              </span>
            ))}
          </span>
          <span className="block px-4 py-2.5">
            {activity.slice(0, 2).map(([item, itemStatus]) => (
              <span key={item} className="flex items-center justify-between gap-3 py-1.5 font-sans text-[10.5px]">
                <span className="inline-flex min-w-0 items-center gap-2 font-semibold"><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-accent" aria-hidden="true" />{item}</span>
                <span className="shrink-0 text-brand-muted">{itemStatus}</span>
              </span>
            ))}
          </span>
        </span>

        <span className="mt-4 flex items-center justify-between">
          <span className="font-sans text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-accent-2">{isOpen ? 'Close details' : 'Explore workflow'}</span>
          <span className="font-sans text-[10px] text-brand-muted">Records · roles · controls</span>
        </span>
      </button>

      <div id={id + '-workflow-panel'} role="region" aria-labelledby={id + '-workflow-toggle'} hidden={!isOpen} className="border-t border-brand-line px-6 pb-6 pt-5 sm:px-7 sm:pb-7">
        <div className="grid gap-5 sm:grid-cols-[1fr_0.95fr]">
          <div>
            <p className="font-sans text-[9.5px] font-bold uppercase tracking-[0.14em] text-brand-muted">Included surfaces</p>
            <ul className="mt-3 space-y-2.5">
              {features.map((feature) => (
                <li key={feature} className="flex items-start gap-2 font-sans text-[12px] font-semibold leading-relaxed text-brand-ink-2">
                  <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-brand-green" aria-hidden="true" />{feature}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="font-sans text-[9.5px] font-bold uppercase tracking-[0.14em] text-brand-muted">Access follows the role</p>
            <div className="mt-3 flex flex-wrap gap-2" aria-label="Workflow roles">
              {roles.map((role) => <span key={role} className="rounded-full border border-brand-line bg-brand-bg-soft px-2.5 py-1.5 font-sans text-[9.5px] font-semibold text-brand-ink-2">{role}</span>)}
            </div>
          </div>
        </div>
      </div>
    </article>
  )
}

export default function HomePage() {
  const [expandedSkill, setExpandedSkill] = useState('commercial')
  const [expandedAddon, setExpandedAddon] = useState(null)
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:matt@cybersafeadvisor.com'
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
      <MarketingHeader onSectionClick={scrollTo} />

      <main id="main-content" tabIndex="-1">
      {/* ── Hero ──────────────────────────────────────────────────── */}
      <section className="max-w-6xl mx-auto px-5 sm:px-6 pt-10 pb-14 md:pt-20 md:pb-20">
        <div className="grid lg:grid-cols-[1.02fr_0.98fr] gap-10 xl:gap-16 items-center">
          <div>
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">
              One source of truth for every matter
            </span>
            <h1 className="max-w-xl font-serif font-medium text-[52px] sm:text-[64px] md:text-[76px] leading-[0.98] tracking-[-0.06em] mt-4">
              The whole matter, in hand.
            </h1>
            <p className="text-brand-ink-2 font-sans text-[17px] sm:text-[18px] leading-relaxed mt-5 max-w-xl">
              Every fact, deadline, document, and decision—connected in one living record.
            </p>
            <div className="grid sm:flex sm:flex-wrap items-center gap-3 mt-7">
              <a href={contactUrl} className="inline-flex min-h-12 items-center justify-center gap-2 px-6 py-3.5 bg-brand-accent text-white font-sans font-semibold rounded-lg hover:bg-brand-accent-2 transition-all shadow-sm hover:-translate-y-[1px]">
                Book a demo <ArrowRight size={18} />
              </a>
              <a href="#how" onClick={scrollTo('how')} className="inline-flex min-h-12 items-center justify-center gap-2 px-6 py-3.5 bg-brand-surface border border-brand-line text-brand-ink font-sans font-semibold rounded-xl hover:border-brand-ink hover:bg-brand-bg-soft transition-all shadow-sm">
                See how it works
              </a>
            </div>
            <div className="flex flex-col sm:flex-row sm:flex-wrap gap-x-6 gap-y-2 mt-7 text-[13px] font-sans text-brand-muted">
              <span className="inline-flex items-center gap-2"><CheckCircle2 size={15} className="text-brand-accent" /> Unify every source</span>
              <span className="inline-flex items-center gap-2"><CheckCircle2 size={15} className="text-brand-accent" /> Keep work moving</span>
              <span className="inline-flex items-center gap-2"><CheckCircle2 size={15} className="text-brand-accent" /> Built for trust</span>
            </div>
          </div>

          {/* Product preview card */}
          <div className="relative mt-2 lg:mt-0">
            <div className="absolute -inset-4 bg-brand-accent/5 rounded-[28px] blur-xl" aria-hidden />
            <div className="relative bg-brand-surface border border-brand-line rounded-2xl shadow-xl overflow-hidden">
              <div className="flex items-center justify-between px-5 py-3.5 border-b border-brand-line bg-brand-bg-soft/40">
                <div className="flex items-center gap-2">
                  <img src="/brand/lawhand/lawhand-mark.svg" alt="" aria-hidden="true" className="h-5 w-5 rounded" />
                  <span className="font-serif font-semibold text-[14px]">Matter record</span>
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
      <section id="ai" className="max-w-6xl mx-auto px-6 py-16 md:py-24 scroll-mt-20">
        <div className="max-w-2xl">
          <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">LawHand intelligence</span>
          <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
            Work with the matter. Connect it to everything else.
          </h2>
          <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
            Use matter-aware AI chat inside LawHand, or bring controlled LawHand tools into an approved external system through MCP.
          </p>
        </div>

        <div className="grid lg:grid-cols-[1.18fr_0.82fr] gap-6 mt-10 items-stretch">
          <article className="relative overflow-hidden rounded-3xl border border-brand-line bg-brand-surface p-7 md:p-9 shadow-sm">
            <div className="absolute -right-16 -top-20 h-56 w-56 rounded-full bg-brand-accent/10 blur-3xl" aria-hidden="true" />
            <div className="relative grid gap-8 md:grid-cols-[0.88fr_1.12fr] md:items-center">
              <div>
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-accent text-white shadow-sm">
                  <MessageSquareText size={22} />
                </div>
                <h3 className="font-serif font-bold text-[25px] leading-tight mt-5">Matter-aware AI chat</h3>
                <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed mt-3">
                  Research, review, summarize, and draft with the active matter and authorized sources close at hand.
                </p>
                <Link to="/product/chat" className="inline-flex items-center gap-2 mt-6 text-[13.5px] font-sans font-bold text-brand-accent-2 hover:underline">
                  Explore LawHand Chat <ArrowRight size={15} />
                </Link>
              </div>
              <MarketingChatWorkspace compact />
            </div>
          </article>

          <article className="relative overflow-hidden rounded-3xl border border-brand-ink/10 bg-brand-ink p-7 md:p-9 text-white shadow-lg">
            <div className="absolute -bottom-20 -right-20 h-56 w-56 rounded-full bg-brand-accent/30 blur-3xl" aria-hidden="true" />
            <div className="relative flex h-full flex-col">
              <div className="flex items-start justify-between gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/10"><Braces size={22} /></div>
                <span className="rounded-full border border-brand-gold/30 bg-brand-gold/10 px-2.5 py-1 text-[9px] font-bold uppercase tracking-[0.12em] text-brand-gold">Private preview</span>
              </div>
              <h3 className="font-serif font-bold text-[25px] leading-tight mt-5 text-white">LawHand MCP</h3>
              <p className="text-white/65 font-sans text-[14.5px] leading-relaxed mt-3">
                Give approved tools scoped access to LawHand through product keys, explicit allowlists, bounded usage, and visible activity.
              </p>
              <div className="mt-7 grid grid-cols-2 gap-2">
                {[
                  ['Auth', 'Scoped key'],
                  ['Access', 'Tool allowlist'],
                  ['Price', '$0.45 / call'],
                  ['Limits', 'Bounded'],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2.5">
                    <p className="text-[8.5px] font-bold uppercase tracking-[0.12em] text-white/40">{label}</p>
                    <p className="mt-1 font-mono text-[10.5px] text-white/85">{value}</p>
                  </div>
                ))}
              </div>
              <Link to="/product/mcp" className="inline-flex items-center gap-2 mt-auto pt-7 text-[13.5px] font-sans font-bold text-white hover:text-brand-gold">
                Explore the MCP preview <ArrowRight size={15} />
              </Link>
            </div>
          </article>
        </div>
      </section>

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
            <p className="mt-5 shrink-0 font-sans text-[11px] font-bold uppercase tracking-[0.13em] text-brand-muted md:mt-0">
              Select a skill to see it at work
            </p>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {SKILLS.map((skill) => (
              <div key={skill.id} className={expandedSkill === skill.id ? 'sm:col-span-2 lg:col-span-3' : ''}>
                <PracticeSkillCard
                  skill={skill}
                  isOpen={expandedSkill === skill.id}
                  onToggle={() => setExpandedSkill(expandedSkill === skill.id ? null : skill.id)}
                />
                <PracticeSkillPanel skill={skill} isOpen={expandedSkill === skill.id} />
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
            Extend the workspace when the work demands it.
          </h2>
          <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
            LawHand has a broader module library than any one page should catalog. These two
            examples show how specialized records, roles, and controls can fit into the same core matter experience.
          </p>
        </div>
        <div className="grid md:grid-cols-2 gap-6 items-start">
          {ADDONS.map((addon) => (
            <AddonCard
              key={addon.id}
              addon={addon}
              isOpen={expandedAddon === addon.id}
              onToggle={() => setExpandedAddon(expandedAddon === addon.id ? null : addon.id)}
            />
          ))}
        </div>
        <div className="mt-6 flex flex-col gap-3 rounded-2xl border border-brand-line bg-brand-bg-soft px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="font-sans text-[13px] leading-relaxed text-brand-ink-2">
            <span className="font-bold text-brand-ink">The module library keeps growing.</span>{' '}
            We scope the records, roles, and review points around the work your firm actually handles.
          </p>
          <a href={contactUrl} className="inline-flex shrink-0 items-center gap-2 font-sans text-[12px] font-bold text-brand-accent-2 hover:underline">
            Discuss your workflow <ArrowRight size={14} />
          </a>
        </div>
      </section>

      {/* ── Both sides of the table ───────────────────────────────── */}
      <section className="max-w-6xl mx-auto px-6 py-16 md:py-24">
        <div className="grid lg:grid-cols-2 gap-14 items-center">
          <div>
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Access that follows the role</span>
            <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
              Attorneys keep control. Every participant gets the right view.
            </h2>
            <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4 max-w-xl">
              Module roles shape which records someone can see, update, or approve — from internal
              reviewers to clients, counterparties, fiduciaries, and neutral participants.
            </p>
            <div className="flex flex-wrap gap-2.5 mt-7">
              {['Attorney · full edit', 'Reviewer · approve', 'Client · share inputs', 'Participant · limited view', 'Neutral · shared view', 'Firm admin · policies & seats'].map((b) => (
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
              alt="An attorney passes an approved brief to a client while retaining the complete matter folio"
              loading="lazy"
              className="relative w-full aspect-[3/2] object-cover rounded-2xl shadow-xl border border-brand-line"
            />
          </div>
        </div>
        <div className="grid sm:grid-cols-3 gap-5 mt-14">
          {[
            { n: '01', t: 'Keep the record current', b: 'Structured updates and reminders surface work on the cadence each module requires.' },
            { n: '02', t: 'Make access legible', b: 'Participants see the documents, requests, and decisions assigned to their role.' },
            { n: '03', t: 'Preserve the review trail', b: 'Key values, sources, changes, and approvals stay connected to the matter record.' },
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
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Straightforward pricing</span>
            <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">One clear platform price. Controlled expansion.</h2>
            <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
              License the full LawHand workspace by seat, start with intake when that is the right first move,
              and add external MCP connections deliberately.
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
                <h3 className="font-serif font-bold text-[22px]">LawHand platform</h3>
                <span className="px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold text-brand-muted bg-brand-bg-soft border border-brand-line">per user</span>
              </div>
              <div className="mt-5 flex items-baseline gap-1.5">
                <span className="font-serif text-[52px] font-bold leading-none">$89</span>
                <span className="text-[14px] text-brand-muted font-sans">/ user / month</span>
              </div>
              <p className="text-brand-muted font-sans text-[12.5px] mt-2">Billed annually</p>
              <hr className="border-brand-line my-6" />
              <div className="space-y-3">
                {[
                  { name: 'Matter-aware AI chat', price: 'included', body: 'Research, review, summaries, and drafting connected to the active matter and authorized sources.' },
                  { name: 'Firm operations', price: 'included', body: 'Matters, contacts, documents, tasks, deadlines, billing, reporting, and approved integrations.' },
                  { name: 'Role-aware access', price: 'included', body: 'Tenant-isolated firm workspaces with controlled client and party access.' },
                  { name: 'Specialized workflows', price: 'optional', body: 'Add practice modules after scope, readiness, and access requirements are agreed.' },
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
                  <span className="font-semibold text-brand-ink">Configured to fit the firm.</span>{' '}
                  Integrations, onboarding, support, enabled modules, and model-provider usage are documented before production rollout.
                </p>
              </div>
              <Link to="/pricing" className="block w-full mt-7 py-3 rounded-lg text-center font-sans font-semibold text-[14px] bg-brand-accent text-white hover:bg-brand-accent-2 transition-all">
                View full pricing
              </Link>
              <p className="text-center text-brand-muted font-sans text-[12.5px] mt-4">
                Questions about onboarding or commercial terms?{' '}
                <a href={contactUrl} className="text-brand-accent-2 font-semibold hover:underline">Talk to us</a>.
              </p>
            </div>
          </div>

          <div className="max-w-5xl mx-auto mt-8 rounded-2xl border border-brand-line bg-brand-ink p-7 md:p-8 text-white">
            <div className="grid gap-6 md:grid-cols-[1fr_auto] md:items-center">
              <div>
                <div className="flex flex-wrap items-center gap-3">
                  <KeyRound size={20} className="text-brand-gold" />
                  <h3 className="font-serif text-[21px] font-bold text-white">LawHand MCP</h3>
                  <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-1 text-[9px] font-bold uppercase tracking-[0.12em] text-white/65">Private preview</span>
                </div>
                <p className="mt-3 max-w-2xl text-[13.5px] leading-relaxed text-white/65">
                  Connect approved external tools with scoped keys, explicit tool access, bounded usage, and administrative visibility.
                </p>
              </div>
              <div className="md:text-right">
                <div className="flex items-baseline gap-1.5 md:justify-end"><span className="font-serif text-[38px] font-bold">$0.45</span><span className="text-[12px] text-white/55">/ tool call</span></div>
                <Link to="/product/mcp" className="mt-2 inline-flex items-center gap-2 text-[12.5px] font-bold text-brand-gold hover:underline">Preview details <ArrowRight size={14} /></Link>
              </div>
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
              <a href={contactUrl} className="inline-flex items-center gap-2 px-6 py-3.5 bg-white text-brand-ink font-sans font-semibold rounded-lg hover:bg-brand-bg transition-all shadow-sm">
                Book a demo <ArrowRight size={18} />
              </a>
              <a href={contactUrl} className="inline-flex items-center gap-2 px-6 py-3.5 bg-transparent border border-white/25 text-white font-sans font-semibold rounded-xl hover:bg-white/10 transition-all">
                Request a 20-min walkthrough
              </a>
            </div>
          </div>
        </div>
      </section>
      </main>

      <MarketingFooter />
    </div>
  )
}
