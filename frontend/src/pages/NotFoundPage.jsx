import { Link } from 'react-router-dom'
import { ArrowRight, Compass, LifeBuoy } from 'lucide-react'
import MarketingPageLayout from '../components/MarketingChrome'

const DESTINATIONS = [
  { to: '/product', title: 'The platform', body: 'Intake, matters, documents, deadlines, billing, and practice-area skills in one workspace.' },
  { to: '/product/chat', title: 'Matter-aware AI chat', body: 'Research, review, summarize, and draft against the active matter and authorized sources.' },
  { to: '/product/mcp', title: 'LawHand MCP', body: 'Controlled integrations with scoped keys, allowlisted tools, and bounded usage.' },
  { to: '/pricing', title: 'Pricing', body: 'One platform seat price, the MCP preview price, and answers to rollout questions.' },
]

export default function NotFoundPage() {
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:matt@cybersafeadvisor.com'

  return (
    <MarketingPageLayout>
      <section className="mx-auto max-w-4xl px-6 pb-12 pt-16 text-center md:pb-16 md:pt-24">
        <span className="inline-flex items-center gap-2 rounded-full border border-brand-line bg-brand-surface px-3 py-1.5 font-sans text-[11px] font-bold uppercase tracking-[0.14em] text-brand-accent-2">
          <Compass size={14} aria-hidden="true" /> Error 404
        </span>
        <h1 className="mx-auto mt-5 max-w-2xl font-serif text-[40px] font-bold leading-[1.06] tracking-tight md:text-[56px]">
          That page is not part of the record.
        </h1>
        <p className="mx-auto mt-5 max-w-xl font-sans text-[17px] leading-relaxed text-brand-ink-2">
          The address you followed does not match a LawHand page. It may have moved, or the link may be incomplete.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link
            to="/"
            className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-brand-accent px-6 font-sans text-[14px] font-semibold text-white shadow-sm transition-all hover:-translate-y-px hover:bg-brand-accent-2"
          >
            Back to home <ArrowRight size={16} aria-hidden="true" />
          </Link>
          <Link
            to="/login"
            className="inline-flex min-h-12 items-center rounded-lg border border-brand-line-2 bg-brand-surface px-6 font-sans text-[14px] font-semibold text-brand-ink transition-colors hover:border-brand-ink"
          >
            Sign in to your workspace
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 pb-20 md:pb-28">
        <div className="grid gap-4 sm:grid-cols-2">
          {DESTINATIONS.map(({ to, title, body }) => (
            <Link
              key={to}
              to={to}
              className="group rounded-2xl border border-brand-line bg-brand-surface p-6 transition-all hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-md motion-reduce:transition-none motion-reduce:hover:translate-y-0"
            >
              <h2 className="flex items-center justify-between gap-3 font-serif text-[18px] font-bold">
                {title}
                <ArrowRight size={16} aria-hidden="true" className="shrink-0 text-brand-accent-2 transition-transform group-hover:translate-x-0.5" />
              </h2>
              <p className="mt-2 font-sans text-[14px] leading-relaxed text-brand-ink-2">{body}</p>
            </Link>
          ))}
        </div>
        <div className="mt-6 flex flex-col gap-3 rounded-2xl border border-brand-line bg-brand-bg-soft px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="inline-flex items-center gap-2.5 font-sans text-[13px] leading-relaxed text-brand-ink-2">
            <LifeBuoy size={17} className="shrink-0 text-brand-accent-2" aria-hidden="true" />
            Followed a link from us that should work? Tell us where it came from.
          </p>
          <a href={contactUrl} className="inline-flex shrink-0 items-center gap-2 font-sans text-[12px] font-bold text-brand-accent-2 hover:underline">
            Report a broken link <ArrowRight size={14} aria-hidden="true" />
          </a>
        </div>
      </section>
    </MarketingPageLayout>
  )
}
