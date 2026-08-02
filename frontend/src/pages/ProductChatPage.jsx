import React from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight, BadgeCheck, FileText, FolderOpen, MessageSquareText,
  Search, ShieldCheck, Sparkles,
} from 'lucide-react'
import MarketingPageLayout from '../components/MarketingChrome'

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
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:contact@perevagagroup.com'

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
          <div className="relative overflow-hidden rounded-3xl border border-brand-line bg-brand-surface shadow-2xl">
            <div className="flex items-center justify-between border-b border-brand-line bg-brand-ink px-5 py-4 text-white sm:px-6">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/55">Matter chat</p>
                <p className="mt-1 font-serif text-[16px] font-bold">Rivera v. Northwind</p>
              </div>
              <span className="rounded-full border border-white/15 bg-white/10 px-3 py-1 text-[10px] font-semibold text-white/80">3 sources connected</span>
            </div>
            <div className="space-y-4 p-5 sm:p-6">
              <div className="ml-auto max-w-[82%] rounded-2xl rounded-br-md bg-brand-accent px-4 py-3 text-[13px] leading-relaxed text-white">
                Compare the settlement positions and identify the issues that still need attorney review.
              </div>
              <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-brand-line bg-brand-bg-soft p-4">
                <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-brand-accent-2">
                  <Sparkles size={13} /> LawHand
                </div>
                <p className="mt-3 text-[13px] leading-relaxed text-brand-ink-2">
                  The positions overlap on payment timing and confidentiality. The remaining review points are release scope, tax treatment, and the proposed non-disparagement language.
                </p>
                <div className="mt-4 grid gap-2 sm:grid-cols-3">
                  {['Demand letter · p. 4', 'Mediator brief · p. 7', 'Draft term sheet'].map((source) => (
                    <span key={source} className="rounded-lg border border-brand-line bg-white px-2.5 py-2 text-[10px] font-semibold text-brand-ink-2">
                      {source}
                    </span>
                  ))}
                </div>
              </div>
              <div className="flex items-center gap-3 rounded-xl border border-brand-line bg-white px-4 py-3 text-[12px] text-brand-muted">
                <MessageSquareText size={17} className="text-brand-accent-2" />
                Ask a follow-up about this matter…
              </div>
            </div>
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
