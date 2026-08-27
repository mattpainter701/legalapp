import { Link } from 'react-router-dom'
import {
  ArrowRight, BadgeCheck, FileText, FolderOpen,
  Search, ShieldCheck,
} from 'lucide-react'
import MarketingPageLayout from '../components/MarketingChrome'
import MarketingChatWorkspace from '../components/MarketingChatWorkspace'

const CAPABILITIES = [
  {
    icon: FolderOpen,
    title: 'Starts with the matter',
    body: 'Open chat from a matter and LawHand carries the matter relationship into the conversation instead of making your team reconstruct the file every time.',
  },
  {
    icon: Search,
    title: 'Shows its source trail',
    body: 'When connected sources are enabled, answers can include citations, confidence cues, and links back to the material an attorney should verify.',
  },
  {
    icon: FileText,
    title: 'Moves from answer to work product',
    body: 'Use the conversation to summarize, compare, review, and prepare a first draft while keeping the underlying matter close at hand.',
  },
]

export default function ProductChatPage() {
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:support@getlawhand.com'

  return (
    <MarketingPageLayout>
      <section className="mx-auto grid max-w-6xl gap-14 px-6 pb-20 pt-16 md:pb-28 md:pt-24 lg:grid-cols-[0.95fr_1.05fr] lg:items-center">
        <div>
          <span className="font-sans text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Matter-aware AI chat</span>
          <h1 className="mt-5 max-w-xl font-serif text-[44px] font-bold leading-[1.04] tracking-tight md:text-[60px]">
            Ask with the whole matter in hand.
          </h1>
          <p className="mt-6 max-w-xl font-sans text-[18px] leading-relaxed text-brand-ink-2">
            LawHand gives legal teams a focused AI workspace for research, review, summaries, and drafting—grounded in the matter and the sources your firm has authorized.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a href={contactUrl} className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-brand-accent px-6 font-sans text-[14px] font-semibold text-white shadow-sm transition-all hover:-translate-y-px hover:bg-brand-accent-2">
              See LawHand chat <ArrowRight size={17} aria-hidden="true" />
            </a>
            <Link to="/pricing" className="inline-flex min-h-12 items-center rounded-lg border border-brand-line-2 bg-brand-surface px-6 font-sans text-[14px] font-semibold text-brand-ink transition-colors hover:border-brand-ink">
              View pricing
            </Link>
          </div>
          <div className="mt-8 flex flex-wrap gap-x-5 gap-y-2 text-[12.5px] font-semibold text-brand-muted">
            {['Matter-linked', 'Source-aware', 'Attorney-reviewed'].map((label) => (
              <span key={label} className="inline-flex items-center gap-1.5"><BadgeCheck size={14} className="text-brand-accent-2" />{label}</span>
            ))}
          </div>
        </div>

        <div className="relative">
          <div className="absolute -inset-5 rounded-[34px] bg-brand-accent/10" aria-hidden="true" />
          <div className="relative">
            <MarketingChatWorkspace />
          </div>
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-24">
          <div className="max-w-2xl">
            <span className="font-sans text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Context before cleverness</span>
            <h2 className="mt-3 font-serif text-[34px] font-bold leading-tight">A legal conversation that can show its work.</h2>
          </div>
          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {CAPABILITIES.map(({ icon: Icon, title, body }) => (
              <article key={title} className="rounded-2xl border border-brand-line bg-brand-surface p-6">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-brand-line bg-brand-bg text-brand-accent-2">
                  <Icon size={21} strokeWidth={1.6} />
                </div>
                <h3 className="mt-5 font-serif text-[18px] font-bold">{title}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-brand-ink-2">{body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="grid overflow-hidden rounded-3xl border border-brand-ink/10 bg-brand-ink text-white shadow-xl lg:grid-cols-[1.25fr_0.75fr]">
          <div className="p-8 md:p-12">
            <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-white/55">Review stays in the loop</span>
            <h2 className="mt-4 max-w-2xl font-serif text-[34px] font-bold leading-tight text-white">LawHand assists the work. Your attorneys make the call.</h2>
            <p className="mt-4 max-w-2xl text-[16px] leading-relaxed text-white/70">
              Source links, confidence cues, access controls, and matter boundaries support verification. They do not replace professional judgment.
            </p>
          </div>
          <div className="flex items-center justify-center border-t border-white/10 bg-white/5 p-8 lg:border-l lg:border-t-0">
            <ShieldCheck size={84} strokeWidth={1} className="text-brand-gold" aria-hidden="true" />
          </div>
        </div>
      </section>
    </MarketingPageLayout>
  )
}
