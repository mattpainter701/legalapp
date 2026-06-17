import React from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ShieldCheck, BadgeCheck, Scale, Lock, Landmark, Building2, UserCircle,
  Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake, ArrowRight, Check,
  Gavel, FileText, FileSearch, CalendarClock, Plug, FolderInput, Mic, MonitorSmartphone,
  Sparkles,
} from 'lucide-react'
import bothSidesImg from '../assets/home/both-sides.png'
import libraryImg from '../assets/home/library.jpg'

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
    description: 'Estate portfolios with role-aware access for trustees, grantors, and beneficiaries \u2014 asset tracking, tax analysis, and probate, kept court-ready.',
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
    body: 'Answers are drawn from your firm\u2019s document library and public case law \u2014 not invented. When the model reasons beyond a source, it says so.',
  },
  {
    icon: BadgeCheck,
    title: 'Cited & verifiable',
    body: 'Every claim is tagged by confidence \u2014 settled, verify, or general knowledge \u2014 with citations you can open and check before you rely on it.',
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
    body: 'Point Clarity at your firm\u2019s templates and prior work product. It drafts new documents in your house style \u2014 ready for an attorney\u2019s edits.',
  },
  {
    icon: FileSearch,
    title: 'Reviews documents for gaps',
    body: 'Drop in a contract, brief, or estate plan and Clarity flags missing clauses, inconsistencies, and risk \u2014 each note linked back to its source.',
  },
  {
    icon: CalendarClock,
    title: 'Manages schedules & deadlines',
    body: 'Track matters, renewals, and filing dates in one place. Clarity surfaces what\u2019s due and rolls deadlines forward on the cadence you set.',
  },
]

const FEATURES = [
  {
    icon: Gavel,
    title: 'Backed by real court records',
    body: 'Answers draw on public case law and court records \u2014 cited and confidence-tagged so you can verify before you rely on them.',
  },
  {
    icon: Plug,
    title: 'Microsoft 365 & Google Docs',
    body: 'Work where you already work. Open, edit, and save documents straight to Microsoft 365 and Google Docs.',
  },
  {
    icon: FolderInput,
    title: 'Drag, drop & file-share access',
    body: 'Drag files in, or connect enterprise file shares so Clarity reads from the documents your firm already keeps.',
  },
  {
    icon: Mic,
    title: 'Voice transcription',
    body: 'Dictate notes, intake calls, and memos. Clarity transcribes and turns them into searchable, citable work product.',
  },
  {
    icon: MonitorSmartphone,
    title: 'Mobile, desktop & cloud portal',
    body: 'Native mobile and desktop apps plus a secure web portal \u2014 your workspace stays in sync wherever you are.',
  },
  {
    icon: Lock,
    title: 'Encrypted, secure storage',
    body: 'Documents are stored encrypted and isolated to your firm. Your data is never shared or used to train public models.',
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
  const scrollTo = (id) => (e) => {
    e.preventDefault()
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <div className="min-h-screen bg-brand-bg text-brand-ink">
      {/* ── Top nav ───────────────────────────────────────────────── */}
      <header className="sticky top-0 z-40 bg-brand-bg/85 backdrop-blur border-b border-brand-line">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-brand-bg-soft border border-brand-line rounded-lg flex items-center justify-center shadow-sm">
              <Logo size={16} />
            </div>
            <span className="font-serif font-bold text-[17px] tracking-tight">Clarity Legal</span>
          </div>
          <nav className="hidden md:flex items-center gap-7 text-[14px] font-sans font-medium text-brand-ink-2">
            <a href="#how" onClick={scrollTo('how')} className="hover:text-brand-ink transition-colors">How it works</a>
            <a href="#features" onClick={scrollTo('features')} className="hover:text-brand-ink transition-colors">Features</a>
            <a href="#skills" onClick={scrollTo('skills')} className="hover:text-brand-ink transition-colors">Practice areas</a>
            <a href="#modules" onClick={scrollTo('modules')} className="hover:text-brand-ink transition-colors">Add-ons</a>
            <a href="#pricing" onClick={scrollTo('pricing')} className="hover:text-brand-ink transition-colors">Pricing</a>
          </nav>
          <div className="flex items-center gap-3">
            <button onClick={goLogin} className="text-[14px] font-sans font-semibold text-brand-ink hover:text-brand-accent-2 transition-colors">
              Sign in
            </button>
            <button onClick={goLogin} className="hidden sm:inline-flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-[14px] font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px]">
              Start free trial
            </button>
          </div>
        </div>
      </header>

      {/* ── Hero ──────────────────────────────────────────────────── */}
      <section className="max-w-6xl mx-auto px-6 pt-16 pb-20 md:pt-24 md:pb-28">
        <div className="grid lg:grid-cols-[1.05fr_1fr] gap-16 items-center">
          <div>
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">
              Clarity Legal · Legal-safe AI for modern firms
            </span>
            <h1 className="font-serif font-bold text-[44px] md:text-[56px] leading-[1.05] tracking-tight mt-5">
              A legal-safe AI coworker for the{' '}
              <em className="italic text-brand-accent-2">most considered</em> work in law.
            </h1>
            <p className="text-brand-ink-2 font-sans text-[18px] leading-relaxed mt-6 max-w-xl">
              Clarity answers from public case law and court records, drafts from your own templates,
              and reviews documents for gaps — every claim cited and framed for attorney review. It
              connects to Microsoft 365, Google Docs, and your file shares, and follows you across
              mobile, desktop, and the web.
            </p>
            <div className="flex flex-wrap items-center gap-3 mt-8">
              <button onClick={goLogin} className="inline-flex items-center gap-2 px-6 py-3.5 bg-brand-ink text-white font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px]">
                Start a 14-day trial <ArrowRight size={18} />
              </button>
              <a href="#features" onClick={scrollTo('features')} className="inline-flex items-center gap-2 px-6 py-3.5 bg-brand-surface border border-brand-line text-brand-ink font-sans font-semibold rounded-xl hover:border-brand-ink hover:bg-brand-bg-soft transition-all shadow-sm">
                Tour the platform
              </a>
            </div>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mt-9 text-[13px] font-sans text-brand-muted">
              <span className="inline-flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-brand-accent" /> 14-day trial, no card required</span>
              <span className="inline-flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-brand-accent" /> Cited &amp; attorney-reviewed</span>
              <span className="inline-flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-brand-accent" /> SOC 2 Type II in progress</span>
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
                  <ShieldCheck size={12} /> Legal-safe
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

      {/* ── Trust ribbon ──────────────────────────────────────────── */}
      <section className="border-y border-brand-line bg-brand-bg-soft/40">
        <div className="max-w-6xl mx-auto px-6 py-8">
          <p className="text-center text-[11px] font-sans font-bold uppercase tracking-[0.16em] text-brand-muted mb-5">
            Trusted by considered firms
          </p>
          <div className="flex flex-wrap items-center justify-center gap-x-10 gap-y-3 text-brand-ink-2 font-serif text-[15px]">
            <span>Halloway &amp; Pierce LLP</span>
            <span>Reyes Family Law</span>
            <span>Okonjo · Trust Counsel</span>
            <span>Brennan Estate Group</span>
            <span>North &amp; Vale Mediators</span>
            <span>Whitfield Family Law</span>
          </div>
        </div>
      </section>

      {/* ── How it works ──────────────────────────────────────────── */}
      <section id="how" className="max-w-6xl mx-auto px-6 py-20 md:py-24 scroll-mt-20">
        <div className="max-w-2xl">
          <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Your AI coworker</span>
          <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
            It learns your firm, then does the heavy lifting.
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
              Everything your practice needs, in one secure workspace.
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

      {/* ── The legal-safe difference ─────────────────────────────── */}
      <section id="platform" className="max-w-6xl mx-auto px-6 py-20 md:py-24 scroll-mt-20">
        <div className="max-w-2xl">
          <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">The legal-safe difference</span>
          <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">
            Powerful AI, built to be trusted in a law practice.
          </h2>
          <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
            Generic chatbots guess. Clarity is engineered around the guarantees attorneys
            actually need before relying on a machine.
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
                Built-in expertise for the work you do.
              </h2>
              <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
                Clarity comes with ready-made skills for each practice area — guided workflows
                that know the documents, checks, and language of your field, all cited and
                framed for attorney review.
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
              src={bothSidesImg}
              alt="Two leather chairs facing each other across a table in a warm, light-filled law office"
              loading="lazy"
              className="relative w-full aspect-[4/3] object-cover rounded-2xl shadow-xl border border-brand-line"
            />
          </div>
        </div>
        <div className="grid sm:grid-cols-3 gap-5 mt-14">
          {[
            { n: '01', t: 'Keeps the record current', b: 'Reminders and roll-forwards run on the cadence you set, so nothing quietly goes stale.' },
            { n: '02', t: 'Holds hard conversations', b: 'In mediation, the platform is the neutral — naming items, owning the math, tracking approvals.' },
            { n: '03', t: 'Makes the trail court-ready', b: 'Every value, citation, and approval is logged. Generate a clean report in one click.' },
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
            <span className="text-[12px] font-sans font-bold uppercase tracking-[0.16em] text-brand-accent-2">Pricing</span>
            <h2 className="font-serif font-bold text-[34px] leading-tight mt-3">Pricing that fits how you work.</h2>
            <p className="text-brand-ink-2 font-sans text-[17px] leading-relaxed mt-4">
              Per-user seats for your firm, billed straight from your directory — plus
              pay-as-you-go credits when you query Clarity from your own tools.
            </p>
          </div>
          <div className="grid lg:grid-cols-2 gap-8 items-stretch max-w-5xl mx-auto">
            {/* Licensing model */}
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8">
              <h3 className="font-serif font-bold text-[22px] mb-2">Licensed straight from your directory</h3>
              <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed mb-7">
                Connect Microsoft Entra ID or Active Directory. We count the people who actually
                practice — administrators and service accounts never count against your license.
              </p>
              <ol className="space-y-5">
                {[
                  { icon: Plug, t: 'Connect your directory', b: 'Link Microsoft Entra ID or Active Directory in a few clicks.' },
                  { icon: UserCircle, t: 'We scan for non-admin users', b: 'Only everyday users are counted — admin and service accounts are excluded.' },
                  { icon: BadgeCheck, t: 'That\u2019s what\u2019s licensed', b: 'Your seat count always matches your directory. Nothing to reconcile by hand.' },
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
                <h3 className="font-serif font-bold text-[22px]">Clarity Legal</h3>
                <span className="px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold text-brand-muted bg-brand-bg-soft border border-brand-line">per user</span>
              </div>
              <div className="mt-5 flex items-baseline gap-1.5">
                <span className="font-serif text-[52px] leading-none">$89</span>
                <span className="text-[15px] text-brand-muted font-sans">/ user / month</span>
              </div>
              <p className="text-brand-muted font-sans text-[13.5px] mt-2">Billed annually · 14-day trial, no card required</p>
              <hr className="border-brand-line my-6" />
              <ul className="space-y-2.5">
                {[
                  'Standard AI models included — unlimited',
                  'Legal-safe research & drafting chat',
                  'Every practice-area skill included',
                  'Microsoft 365, Google Docs & file shares',
                  'Voice transcription & encrypted storage',
                  'Role-aware client & party access',
                  'Audit log & court-ready reports',
                ].map((f) => (
                  <li key={f} className="flex items-start gap-2.5 text-[14px] font-sans text-brand-ink-2">
                    <Check size={16} className="text-brand-accent shrink-0 mt-0.5" /> {f}
                  </li>
                ))}
              </ul>
              <div className="mt-5 flex items-start gap-2.5 rounded-xl bg-brand-bg-soft border border-brand-line px-4 py-3">
                <Sparkles size={16} className="text-brand-gold shrink-0 mt-0.5" />
                <p className="text-[13px] font-sans text-brand-ink-2 leading-relaxed">
                  <span className="font-semibold text-brand-ink">Premium models, pay as you go.</span>{' '}
                  Switch any user to premium frontier models from the admin panel. Usage is metered
                  and added to one monthly invoice — a card on file is required.
                </p>
              </div>
              <button onClick={goLogin} className="w-full mt-7 py-3 rounded-xl font-sans font-semibold text-[14px] bg-brand-ink text-white hover:bg-brand-ink-2 transition-all">
                Start free trial
              </button>
              <p className="text-center text-brand-muted font-sans text-[12.5px] mt-4">
                Trust &amp; Estate and Mediation modules optional. Need SSO, an SLA, or invoiced billing?{' '}
                <button onClick={goLogin} className="text-brand-accent-2 font-semibold hover:underline">Talk to us</button>.
              </p>
            </div>
          </div>

          {/* MCP usage-based pricing */}
          <div className="max-w-5xl mx-auto mt-8">
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-8 md:p-9">
              <div className="grid md:grid-cols-[1.45fr_1fr] gap-8 items-center">
                <div>
                  <span className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-[11px] font-sans font-bold uppercase tracking-wider bg-brand-gold/10 text-brand-gold border border-brand-gold/25">
                    Pay as you go
                  </span>
                  <h3 className="font-serif font-bold text-[22px] mt-3 flex items-center gap-2.5">
                    <Plug size={20} strokeWidth={1.75} className="text-brand-accent-2" />
                    Clarity MCP — query Clarity from your own tools
                  </h3>
                  <p className="text-brand-ink-2 font-sans text-[14.5px] leading-relaxed mt-2.5">
                    Connect Clarity to your own systems through our MCP. Buy credits, authenticate
                    with a username, and every tool call or query draws down your balance — no seat
                    required, pay only for what you use.
                  </p>
                  <div className="flex flex-wrap gap-2.5 mt-5">
                    {['Prepaid credits', 'Username authentication', 'Metered per tool call', 'No subscription'].map((b) => (
                      <span key={b} className="px-3 py-1.5 rounded-full text-[12.5px] font-sans font-medium text-brand-ink-2 bg-brand-bg-soft border border-brand-line">
                        {b}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="md:border-l md:border-brand-line md:pl-8">
                  <div className="flex items-baseline gap-1.5">
                    <span className="font-serif text-[46px] leading-none">$0.45</span>
                    <span className="text-[14px] text-brand-muted font-sans">/ tool call</span>
                  </div>
                  <p className="text-brand-muted font-sans text-[13px] leading-relaxed mt-2">
                    Drawn from prepaid credits — each query or tool call consumes one.
                  </p>
                  <button onClick={goLogin} className="inline-flex items-center gap-2 mt-5 px-5 py-2.5 bg-brand-ink text-white font-sans font-semibold text-[14px] rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm">
                    Buy credits <ArrowRight size={16} />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Security strip ────────────────────────────────────────── */}
      <section id="security" className="max-w-6xl mx-auto px-6 py-20 md:py-24 scroll-mt-20">
        <div className="bg-brand-ink rounded-3xl px-8 py-14 md:px-16 text-center relative overflow-hidden">
          <img src={libraryImg} alt="" aria-hidden loading="lazy" className="absolute inset-0 w-full h-full object-cover opacity-20" />
          <div className="absolute inset-0 bg-brand-ink/80" aria-hidden />
          <div className="relative">
            <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-[12px] font-sans font-semibold bg-white/10 text-white border border-white/15">
              <ShieldCheck size={14} /> Your firm’s data stays isolated
            </span>
            <h2 className="font-serif font-bold text-[34px] md:text-[40px] text-white leading-tight mt-6">
              14 days. No credit card. Real matters.
            </h2>
            <p className="text-white/70 font-sans text-[17px] leading-relaxed mt-4 max-w-2xl mx-auto">
              Spin up a workspace, bring in one matter, trust, or case, and decide on day 14.
              We’ll never autopay you in.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3 mt-8">
              <button onClick={goLogin} className="inline-flex items-center gap-2 px-6 py-3.5 bg-white text-brand-ink font-sans font-semibold rounded-xl hover:bg-brand-bg transition-all shadow-sm">
                Start the trial <ArrowRight size={18} />
              </button>
              <button onClick={goLogin} className="inline-flex items-center gap-2 px-6 py-3.5 bg-transparent border border-white/25 text-white font-sans font-semibold rounded-xl hover:bg-white/10 transition-all">
                Book a 20-min walkthrough
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ────────────────────────────────────────────────── */}
      <footer className="border-t border-brand-line">
        <div className="max-w-6xl mx-auto px-6 py-10 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <Logo size={18} />
            <span className="font-serif font-bold text-[15px]">Clarity Legal</span>
          </div>
          <p className="text-brand-gold font-serif italic text-[14px] tracking-wide">Secure. Private. Accurate.</p>
          <p className="text-brand-muted font-sans text-[12.5px]">© {new Date().getFullYear()} Clarity Legal</p>
        </div>
      </footer>
    </div>
  )
}
