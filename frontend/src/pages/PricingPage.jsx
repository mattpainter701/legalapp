import { Link } from 'react-router-dom'
import {
  ArrowRight, BadgeCheck, Check, KeyRound, MessageSquareText, PhoneIncoming,
  ShieldCheck, Sparkles,
} from 'lucide-react'
import MarketingPageLayout from '../components/MarketingChrome'
// Shared with the FAQPage structured data so a published answer and the answer
// search engines are shown can never diverge.
import { MCP_TOOL_CALL_PRICE_USD, PLATFORM_PRICE_USD, PRICING_FAQ } from '../seo/config'

const PLATFORM_FEATURES = [
  'Matter-aware AI chat for research, review, summaries, and drafting',
  'Matters, contacts, documents, tasks, deadlines, billing, and reporting',
  'Source-linked research with attorney-verification cues when configured',
  'Microsoft 365, Google Drive, upload, and supported file-share sources',
  'Practice-area skills and optional specialized workflow modules',
  'Role-aware client and party access with tenant-isolated workspaces',
]

export default function PricingPage() {
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:support@getlawhand.com'

  return (
    <MarketingPageLayout>
      <section className="mx-auto max-w-6xl px-6 pb-14 pt-16 text-center md:pb-20 md:pt-24">
        <span className="text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">LawHand pricing</span>
        <h1 className="mx-auto mt-5 max-w-3xl font-serif text-[44px] font-bold leading-[1.04] tracking-tight md:text-[60px]">One clear platform price. Controlled expansion.</h1>
        <p className="mx-auto mt-6 max-w-2xl text-[18px] leading-relaxed text-brand-ink-2">
          Begin with the full LawHand workspace at a predictable seat price, add specialized workflows deliberately, and connect external research tools through metered MCP access.
        </p>
      </section>

      <section className="mx-auto grid max-w-6xl gap-6 px-6 pb-16 md:pb-24 lg:grid-cols-[1.28fr_0.72fr]">
        <article className="relative overflow-hidden rounded-3xl border-[1.5px] border-brand-ink bg-brand-surface p-7 shadow-xl md:p-10">
          <div className="absolute -right-16 -top-20 h-56 w-56 rounded-full bg-brand-accent/10 blur-3xl" aria-hidden="true" />
          <div className="relative">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <span className="inline-flex items-center gap-2 rounded-full border border-brand-line bg-brand-bg-soft px-3 py-1 text-[10px] font-bold uppercase tracking-[0.13em] text-brand-accent-2"><BadgeCheck size={13} /> Core platform</span>
                <h2 className="mt-4 font-serif text-[28px] font-bold">LawHand</h2>
              </div>
              <div className="text-right">
                <div className="flex items-baseline justify-end gap-1.5">
                  <span className="font-serif text-[56px] font-bold leading-none">${PLATFORM_PRICE_USD}</span>
                  <span className="text-[14px] text-brand-muted">/ user / month</span>
                </div>
                <p className="mt-2 text-[12.5px] text-brand-muted">Billed annually</p>
              </div>
            </div>
            <hr className="my-7 border-brand-line" />
            <ul className="grid gap-x-7 gap-y-3.5 md:grid-cols-2">
              {PLATFORM_FEATURES.map((feature) => (
                <li key={feature} className="flex items-start gap-2.5 text-[14px] leading-relaxed text-brand-ink-2">
                  <Check size={17} className="mt-0.5 shrink-0 text-brand-green" />{feature}
                </li>
              ))}
            </ul>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <a href={contactUrl} className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-brand-accent px-6 text-[14px] font-semibold text-white transition-all hover:-translate-y-px hover:bg-brand-accent-2">Book a demo <ArrowRight size={16} /></a>
              <Link to="/product/chat" className="inline-flex min-h-12 items-center rounded-lg border border-brand-line-2 bg-white px-6 text-[14px] font-semibold hover:border-brand-ink">Explore AI Chat</Link>
            </div>
          </div>
        </article>

        <div className="grid gap-6">
          <article className="rounded-3xl border border-brand-line bg-brand-ink p-7 text-white shadow-lg">
            <div className="flex items-center justify-between gap-3">
              <KeyRound size={24} className="text-brand-gold" />
              <span className="rounded-full border border-white/15 bg-white/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.13em] text-white/70">Available</span>
            </div>
            <h2 className="mt-5 font-serif text-[24px] font-bold text-white">LawHand MCP</h2>
            <div className="mt-4 flex items-baseline gap-1.5">
              <span className="font-serif text-[44px] font-bold leading-none">${MCP_TOOL_CALL_PRICE_USD}</span>
              <span className="text-[13px] text-white/55">/ tool call</span>
            </div>
            <p className="mt-4 text-[13.5px] leading-relaxed text-white/65">Per successful tool call. Scoped keys, expiration, hard budgets, and administrative visibility.</p>
            <Link to="/product/mcp" className="mt-6 inline-flex min-h-11 items-center gap-2 rounded-lg border border-white/20 px-4 text-[13px] font-semibold text-white transition-colors hover:bg-white/10">Research details <ArrowRight size={15} /></Link>
          </article>

          <article className="rounded-3xl border border-brand-line bg-brand-bg-soft p-7">
            <PhoneIncoming size={24} className="text-brand-accent-2" />
            <h2 className="mt-4 font-serif text-[22px] font-bold">Call Intake</h2>
            <p className="mt-2 text-[13.5px] leading-relaxed text-brand-ink-2">Start with caller history, outcomes, assignments, and an optional verified Zoom Phone connection.</p>
            <a href={contactUrl} className="mt-5 inline-flex items-center gap-2 text-[13px] font-bold text-brand-accent-2 hover:underline">Configure an intake rollout <ArrowRight size={14} /></a>
          </article>
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-24">
          <div className="grid gap-5 md:grid-cols-3">
            {[
              { icon: MessageSquareText, title: 'Chat is part of the platform', body: 'Matter-aware AI chat is a first-class LawHand workflow—not a separate generic chatbot.' },
              { icon: Sparkles, title: 'Premium usage stays visible', body: 'Provider and model access can be configured with usage controls appropriate to the firm.' },
              { icon: ShieldCheck, title: 'Terms match the deployment', body: 'Integrations, onboarding, support, and enabled modules are documented before production access.' },
            ].map(({ icon: Icon, title, body }) => (
              <article key={title} className="rounded-2xl border border-brand-line bg-brand-surface p-6">
                <Icon size={22} className="text-brand-accent-2" />
                <h3 className="mt-4 font-serif text-[18px] font-bold">{title}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-brand-ink-2">{body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-4xl px-6 py-16 md:py-24">
        <div className="text-center">
          <span className="text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Questions before rollout</span>
          <h2 className="mt-3 font-serif text-[34px] font-bold">Pricing, without the fine-print maze.</h2>
        </div>
        <div className="mt-10 divide-y divide-brand-line rounded-2xl border border-brand-line bg-brand-surface px-6 md:px-8">
          {PRICING_FAQ.map(([question, answer]) => (
            <article key={question} className="py-6">
              <h3 className="font-serif text-[17px] font-bold">{question}</h3>
              <p className="mt-2 text-[14px] leading-relaxed text-brand-ink-2">{answer}</p>
            </article>
          ))}
        </div>
      </section>
    </MarketingPageLayout>
  )
}
