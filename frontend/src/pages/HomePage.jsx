import React from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ShieldCheck, BadgeCheck, Scale, Lock, Landmark, Building2, UserCircle,
  Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake, ArrowRight,
  Gavel, FileText, FileSearch, CalendarClock, Plug, FolderInput, MonitorSmartphone,
  Sparkles,
} from 'lucide-react'
import balancedAccessImg from '../assets/home/balanced-access-record-editorial-v1-1280.webp'
import balancedAccessSmallImg from '../assets/home/balanced-access-record-editorial-v1-720.webp'
import secureArchiveImg from '../assets/home/secure-source-archive-cta-v1-1280.webp'
import secureArchiveSmallImg from '../assets/home/secure-source-archive-cta-v1-720.webp'

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
    icon: Vault,
    name: 'Trust & Estate management',
    description: 'Estate portfolios with role-aware access for trustees, grantors, and beneficiaries \u2014 asset tracking, tax analysis, and probate records organized for review.',
  },
  {
    icon: Handshake,
    name: 'Mediation management',
    description: 'A neutral two-party workspace \u2014 intake, briefs, settlement drafting, and case tracking with balanced access for each side.',
  },
]

const PILLARS = [
  {
    icon: ShieldCheck,
    title: 'Grounded in your sources',
    body: 'Research can draw from your firm\u2019s document library and public case law. Review linked sources and apply professional judgment before relying on an answer.',
  },
  {
    icon: BadgeCheck,
    title: 'Cited & verifiable',
    body: 'Research responses can include confidence labels and citations you can open and check before you rely on them.',
  },
  {
    icon: Scale,
    title: 'Attorney-reviewed',
    body: 'Drafting and analysis are framed as work product, gated for attorney review. Clarity assists the lawyer; it never replaces professional judgment.',
  },
]

const HOW = [
  {
    icon: FileText,
    title: 'Drafts from your own templates',
    body: 'Use your firm\u2019s templates and approved source material to prepare drafts for attorney editing and review.',
  },
  {
    icon: FileSearch,
    title: 'Reviews documents for gaps',
    body: 'Document-review workflows can surface possible gaps and inconsistencies for an attorney to verify against the source.',
  },
  {
    icon: CalendarClock,
    title: 'Manages schedules & deadlines',
    body: 'Track matters, renewals, and due dates in one place. Scheduled reminders surface work on the configured cadence.',
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
    body: 'Drag files in, or connect enterprise file shares so Clarity reads from the documents your firm already keeps.',
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

function Logo({ size = 32 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z" fill="#14253B" />
      <path d="M13 15l2 2 4-4" stroke="#F7F3EC" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function Pill({ label, classes }) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider font-sans border ${classes}`}>
      {label}
    </span>
  )
}

export default function HomePage() {
  const navigate = useNavigate()
  const goLogin = () => navigate('/login')
  const startIntake = () => navigate('/signup?plan=intake-only')
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:contact@perevagagroup.com'
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
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-brand-bg-soft border border-brand-line rounded-lg flex items-center justify-center shadow-sm">
              <Logo size={16} />
            </div>
            <span className="font-serif font-bold text-[17px] tracking-tight">Clarity Legal</span>
          </div>
          <nav aria-label="Marketing" className="hidden md:flex items-center gap-7 text-[14px] font-sans font-medium text-brand-ink-2">
            <a href="#how" onClick={scrollTo('how')} className="hover:text-brand-ink transition-colors">How it works</a>
            <a href="#features" onClick={scrollTo('features')} className="hover:text-brand-ink transition-colors">Features</a>
            <a href="#skills" onClick={scrollTo('skills')} className="hover:text-brand-ink transition-colors">Practice areas</a>
            <a href="#modules" onClick={scrollTo('modules')} className="hover:text-brand-ink transition-colors">Add-ons</a>
            <a href="#pricing" onClick={scrollTo('pricing')} className="hover:text-brand-ink transition-colors">Plans</a>
          </nav>
          <div className="flex items-center gap-3">
            <button onClick={goLogin} className="text-[14px] font-sans font-semibold text-brand-ink hover:text-brand-accent-2 transition-colors">
              Sign in
            </button>
            <button onClick={startIntake} className="hidden sm:inline-flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-[14px] font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px]">
              Start with Call Intake
            </button>
          </div>
        </div>
      </header>

      <main id="main-content" tabIndex="-1">
      {/* ── Hero ──────────────────────────────────────────────────── */}
      <section className="max-w-6xl mx-auto px-6 pt-16 pb-20 md:pt-24 md:pb-28">
        <div className="grid lg:grid-cols-[1.05fr_1fr] gap-16 items-center">
          <div>
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">
              Clarity Legal · Legal work software for modern firms
            </span>
            <h1 className="font-serif font-bold text-[44px] md:text-[56px] leading-[1.05] tracking-tight mt-5">
              An AI-assisted workspace for the{' '}
              <em className="italic text-brand-accent-2">most considered</em> work in law.
            </h1>
            <p className="text-brand-ink-2 font-sans text-[18px] leading-relaxed mt-6 max-w-xl">
              Clarity can use configured public-law sources, draft from your own templates,
              and support document review — legal-research output is framed for attorney verification. It
              can connect to Microsoft 365, Google Drive, and configured file shares, and follows you across
              mobile, desktop, and the web.
            </p>
            <div className="flex flex-wrap items-center gap-3 mt-8">
              <button onClick={startIntake} className="inline-flex items-center gap-2 px-6 py-3.5 bg-brand-ink text-white font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px]">
                Start with Call Intake <ArrowRight size={18} />
              </button>
              <a href="#features" onClick={scrollTo('features')} className="inline-flex items-center gap-2 px-6 py-3.5 bg-brand-surface border border-brand-line text-brand-ink font-sans font-semibold rounded-xl hover:border-brand-ink hover:bg-brand-bg-soft transition-all shadow-sm">
                Tour the platform
              </a>
            </div>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mt-9 text-[13px] font-sans text-brand-muted">
              <span className="inline-flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-brand-accent" /> Call Intake and Tasks from day one</span>
              <span className="inline-flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-brand-accent" /> Source-tagged for attorney review</span>
              <span className="inline-flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-brand-accent" /> Firm data isolated by tenant</span>
            </div>
          </div>

          {/* Product preview card */}
          <div className="relative">
            <div className="absolute -inset-4 bg-brand-accent/5 rounded-[28px] blur-xl" aria-hidden />
            <div className="relative bg-brand-surface border border-brand-line rounded-2xl shadow-xl overflow-hidden">
              <div className="flex items-center justify-between px-5 py-3.5 border-b border-brand-line bg-brand-bg-soft/40">
                <div className="flex items-center gap-2">
                  <Logo size={16} />
                  <span className="font-serif font-semibold text-[14px]">Clarity Legal</span>
                </div>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold bg-brand-green/10 text-brand-green border border-brand-green/20">
                  <ShieldCheck size={12} /> Attorney review workflow
                </span>
              </div>
              <div className="px-5 py-5 space-y-4">
                <div className="flex justify-end">
                  <div className="bg-brand-ink text-white text-[13.5px] font-sans rounded-2xl rounded-tr-sm px-4 py-2.5 max-w-[80%]">
                    What are the elements of promissory estoppel?
                  </div>
                </div>
                <div className="bg-brand-surface border border-brand-line rounded-2xl rounded-tl-sm px-4 py-4 shadow-sm">
                  <p className="font-serif font-bold text-[15px] mb-2">Elements of Promissory Estoppel</p>
                  <p className="text-[13.5px] text-brand-ink-2 font-sans leading-relaxed">
                    Under the Restatement (Second) of Contracts § 90, the doctrine generally requires
                    a clear and definite promise, reasonable and foreseeable reliance, and injustice
                    avoidable only by enforcement. <Pill label="settled" classes="bg-brand-green/10 text-brand-green border-brand-green/20" />
                  </p>
                  <p className="text-[13.5px] text-brand-ink-2 font-sans leading-relaxed mt-2">
                    State formulations vary on the definiteness requirement.{' '}
                    <Pill label="verify" classes="bg-brand-amber/10 text-brand-amber border-brand-amber/20" />
                  </p>
                  <div className="flex items-center gap-2 mt-3 pt-3 border-t border-brand-line text-[12px] text-brand-muted font-sans">
                    <BadgeCheck size={14} className="text-brand-accent" /> 2 sources · cited &amp; verifiable
                  </div>
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
      <section id="how" className="max-w-6xl mx-auto px-6 py-20 md:py-24 scroll-mt-20">
        <div className="max-w-2xl">
          <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Your AI coworker</span>
          <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
            Uses your configured sources, with your team in control.
          </h2>
          <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
            Clarity reads your templates and documents for context, then drafts, reviews, and keeps
            the calendar — always as work product for an attorney to approve.
          </p>
        </div>
        <div className="grid md:grid-cols-3 gap-6 mt-12">
          {HOW.map(({ icon: Icon, title, body }) => (
            <div key={title} className="bg-brand-surface border border-brand-line rounded-2xl p-7 hover:shadow-md hover:border-brand-line-2 transition-all">
              <div className="w-12 h-12 rounded-xl bg-brand-bg-soft border border-brand-line flex items-center justify-center text-brand-accent-2 mb-5">
                <Icon size={24} strokeWidth={1.75} />
              </div>
              <h3 className="font-serif font-bold text-[19px] mb-2.5">{title}</h3>
              <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Platform features ─────────────────────────────────────── */}
      <section id="features" className="bg-brand-bg-soft/40 border-y border-brand-line scroll-mt-20">
        <div className="max-w-6xl mx-auto px-6 py-20 md:py-24">
          <div className="max-w-2xl mb-12">
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">One platform</span>
            <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
              Core legal operations, in one tenant-scoped workspace.
            </h2>
            <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
              From court-record-backed answers to the tools your team uses every day — Clarity connects
              to your documents and follows you across every device.
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
      <section id="platform" className="max-w-6xl mx-auto px-6 py-20 md:py-24 scroll-mt-20">
        <div className="max-w-2xl">
          <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Review-first workflows</span>
          <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
            AI-assisted workflows designed for lawyer review.
          </h2>
          <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
            General-purpose models can answer without your firm context. Clarity emphasizes
            linked sources, review steps, and clear boundaries before work is relied upon.
          </p>
        </div>
        <div className="grid md:grid-cols-3 gap-6 mt-12">
          {PILLARS.map(({ icon: Icon, title, body }) => (
            <div key={title} className="bg-brand-surface border border-brand-line rounded-2xl p-7 hover:shadow-md hover:border-brand-line-2 transition-all">
              <div className="w-12 h-12 rounded-xl bg-brand-bg-soft border border-brand-line flex items-center justify-center text-brand-accent-2 mb-5">
                <Icon size={24} strokeWidth={1.75} />
              </div>
              <h3 className="font-serif font-bold text-[19px] mb-2.5">{title}</h3>
              <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Practice-area skills ──────────────────────────────────── */}
      <section id="skills" className="bg-brand-bg-soft/40 border-y border-brand-line scroll-mt-20">
        <div className="max-w-6xl mx-auto px-6 py-20 md:py-24">
          <div className="md:flex items-end justify-between gap-8 mb-12">
            <div className="max-w-2xl">
              <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Practice-area skills</span>
              <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
                Configured workflows for the work you do.
              </h2>
              <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
                Clarity includes practice-area workflows with relevant document patterns,
                checks, and terminology. Generated work still requires source verification
                and attorney review.
              </p>
            </div>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {SKILLS.map(({ icon: Icon, name, description }) => (
              <div key={name} className="bg-brand-surface border border-brand-line rounded-2xl p-6 hover:shadow-md hover:border-brand-line-2 hover:-translate-y-1 transition-all">
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
      <section id="modules" className="max-w-6xl mx-auto px-6 py-20 md:py-24 scroll-mt-20">
        <div className="max-w-2xl mb-12">
          <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Optional add-on modules</span>
          <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
            Specialized management modules, only if you need them.
          </h2>
          <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
            Two practice areas need more than a skill — they need ongoing portfolios with
            role-aware access for clients and parties. Switch these on when your firm does
            that work.
          </p>
        </div>
        <div className="grid md:grid-cols-2 gap-6">
          {ADDONS.map(({ icon: Icon, name, description }) => (
            <div key={name} className="bg-brand-surface border border-brand-line rounded-2xl p-7 hover:shadow-md hover:border-brand-line-2 transition-all">
              <div className="flex items-start gap-4">
                <div className="w-12 h-12 rounded-xl bg-brand-bg-soft border border-brand-line flex items-center justify-center text-brand-accent-2 shrink-0">
                  <Icon size={24} strokeWidth={1.5} />
                </div>
                <div className="min-w-0">
                  <h3 className="font-serif font-bold text-[18px] leading-tight mb-1.5">{name}</h3>
                  <p className="text-brand-ink-2 font-sans text-[14px] leading-relaxed">{description}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Both sides of the table ───────────────────────────────── */}
      <section className="max-w-6xl mx-auto px-6 py-20 md:py-24">
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
        <div className="max-w-6xl mx-auto px-6 py-20 md:py-24">
          <div className="text-center max-w-2xl mx-auto mb-14">
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Plans</span>
            <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">Start focused, then expand deliberately.</h2>
            <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
              Begin with Call Intake and Tasks or plan a broader rollout. Scope and
              commercial terms are confirmed with your firm before production activation.
            </p>
          </div>
          <div className="grid lg:grid-cols-2 gap-8 items-stretch max-w-5xl mx-auto">
            {/* Licensing model */}
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8">
              <h3 className="font-serif font-bold text-[22px] mb-2">A practical Call Intake starting point</h3>
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
                <h3 className="font-serif font-bold text-[22px]">Clarity Legal rollout</h3>
                <span className="px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold text-brand-muted bg-brand-bg-soft border border-brand-line">tenant scoped</span>
              </div>
              <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed mt-5">
                Choose the operational scope first. We document integrations, onboarding,
                support, and commercial terms before anything is enabled in production.
              </p>
              <hr className="border-brand-line my-6" />
              <div className="space-y-3">
                {[
                  { name: 'Call Intake + Tasks', price: 'available now', body: 'Caller tracking, history, outcomes, staff assignment, and optional verified Zoom Phone intake.' },
                  { name: 'Matter platform', price: 'planned rollout', body: 'Matter, contact, document, task, billing, and approved integration workflows selected for your firm.' },
                  { name: 'AI model access', price: 'policy controlled', body: 'Model access and usage controls are configured for the users and providers your firm approves.' },
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
              <button onClick={startIntake} className="w-full mt-7 py-3 rounded-xl font-sans font-semibold text-[14px] bg-brand-ink text-white hover:bg-brand-ink-2 transition-all">
                Create a Call Intake workspace
              </button>
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
              <button onClick={startIntake} className="inline-flex items-center gap-2 px-6 py-3.5 bg-white text-brand-ink font-sans font-semibold rounded-xl hover:bg-brand-bg transition-all shadow-sm">
                Start with Call Intake <ArrowRight size={18} />
              </button>
              <a href={contactUrl} className="inline-flex items-center gap-2 px-6 py-3.5 bg-transparent border border-white/25 text-white font-sans font-semibold rounded-xl hover:bg-white/10 transition-all">
                Book a 20-min walkthrough
              </a>
            </div>
          </div>
        </div>
      </section>
      </main>

      {/* ── Footer ────────────────────────────────────────────────── */}
      <footer className="border-t border-brand-line">
        <div className="max-w-6xl mx-auto px-6 py-10 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <Logo size={18} />
            <span className="font-serif font-bold text-[15px]">Clarity Legal</span>
          </div>
          <p className="text-brand-gold font-serif italic text-[14px] tracking-wide">Built for deliberate legal work.</p>
          <div className="flex items-center gap-4 text-brand-muted font-sans text-[12.5px]">
            <Link to="/privacy" className="hover:text-brand-ink">Privacy</Link>
            <Link to="/terms" className="hover:text-brand-ink">Terms</Link>
            <span>© {new Date().getFullYear()} Clarity Legal</span>
          </div>
        </div>
      </footer>
    </div>
  )
}
