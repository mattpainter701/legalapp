import { Link } from 'react-router-dom'
import {
  Activity, ArrowRight, Bot, Braces, CheckCircle2, KeyRound, LockKeyhole,
  Network, SlidersHorizontal,
} from 'lucide-react'
import MarketingPageLayout from '../components/MarketingChrome'

const CONTROLS = [
  { icon: KeyRound, title: 'OAuth or API token', body: 'Hosted ChatGPT and Claude clients use OAuth. Header-capable API clients use a scoped LawHand Research token.' },
  { icon: SlidersHorizontal, title: 'Research-only scope', body: 'Search approved public legal authority without exposing workspace matters, documents, or client files.' },
  { icon: Activity, title: 'PAYG visibility', body: 'Review research calls, returned results, errors, and metered activity from the administrative workspace.' },
]

const CONNECTION_TYPES = [
  {
    icon: Bot,
    label: 'Restricted pilot',
    title: 'Workspace MCP',
    body: 'A user connects an approved assistant — Claude, ChatGPT, or Codex — to their own LawHand workspace through an explicit consent step. There is no shared credential: the assistant acts as that user, within that user’s permissions, for the scopes they approved.',
    points: [
      'A firm administrator decides who may connect at all',
      'The user reviews and revokes their own connections',
      'Grants expire and every call is audit logged',
    ],
  },
  {
    icon: KeyRound,
    label: 'Private preview',
    title: 'Scoped product keys',
    body: 'A named system connects with a credential issued for the tenant, limited to an explicit tool allowlist and a usage boundary. Use this when the caller is an application rather than a person.',
    points: [
      'Issued and revoked without touching a user session',
      'Allowed tools and monthly and per-minute limits',
      'Calls, results, errors, and usage visible to administrators',
    ],
  },
]

export default function McpProductPage() {
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:support@getlawhand.com'

  return (
    <MarketingPageLayout>
      <section className="mx-auto grid max-w-6xl gap-14 px-6 pb-20 pt-16 md:pb-28 md:pt-24 lg:grid-cols-[1fr_0.92fr] lg:items-center">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-sans text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">LawHand Research MCP</span>
            <span className="rounded-full border border-brand-gold/30 bg-brand-gold/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.13em] text-brand-gold">Private preview</span>
          </div>
          <h1 className="mt-5 max-w-2xl font-serif text-[44px] font-bold leading-[1.04] tracking-tight md:text-[58px]">
            Bring approved public legal authority into the tools you already use.
          </h1>
          <p className="mt-6 max-w-xl font-sans text-[18px] leading-relaxed text-brand-ink-2">
            Connect ChatGPT, Claude, and other MCP clients to LawHand through Model Context Protocol for research-only retrieval, with OAuth or a scoped API token, PAYG metering, and a visible usage record.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a href={contactUrl} className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-brand-accent px-6 text-[14px] font-semibold text-white shadow-sm transition-all hover:-translate-y-px hover:bg-brand-accent-2">
              Join the private preview <ArrowRight size={17} />
            </a>
            <Link to="/pricing" className="inline-flex min-h-12 items-center rounded-lg border border-brand-line-2 bg-brand-surface px-6 text-[14px] font-semibold text-brand-ink hover:border-brand-ink">
              See pricing
            </Link>
          </div>
        </div>

        <div className="relative overflow-hidden rounded-3xl border border-brand-line bg-brand-ink p-7 text-white shadow-2xl md:p-9">
          <div className="absolute -right-20 -top-24 h-64 w-64 rounded-full bg-brand-accent/30 blur-3xl" aria-hidden="true" />
          <div className="relative">
            <div className="flex items-center justify-between border-b border-white/10 pb-5">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/10"><Braces size={22} /></div>
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-white/50">Connection</p>
                  <p className="mt-1 text-[14px] font-semibold">Streamable HTTP</p>
                </div>
              </div>
              <span className="h-2.5 w-2.5 rounded-full bg-brand-green shadow-[0_0_0_5px_rgba(74,111,93,0.18)]" aria-label="Available in private preview" />
            </div>
            <div className="mt-6 space-y-3">
              {[
                ['Authentication', 'OAuth 2.1 or lhrk_ token'],
                ['Tool access', 'Public authority only'],
                ['Limits', 'Monthly + per minute'],
                ['Official URL', 'research.getlawhand.com/api/mcp'],
              ].map(([label, value]) => (
                <div key={label} className="flex items-center justify-between gap-4 rounded-xl border border-white/10 bg-white/[0.06] px-4 py-3">
                  <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-white/45">{label}</span>
                  <span className="text-right font-mono text-[12px] text-white/85">{value}</span>
                </div>
              ))}
            </div>
            <div className="mt-6 flex items-end justify-between border-t border-white/10 pt-6">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-white/45">Intended public price</p>
                <p className="mt-2 font-serif text-[42px] font-bold leading-none">$0.45</p>
              </div>
              <span className="pb-1 text-[13px] text-white/60">per tool call</span>
            </div>
          </div>
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-24">
          <div className="max-w-2xl">
            <span className="text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Integration with guardrails</span>
            <h2 className="mt-3 font-serif text-[34px] font-bold leading-tight">Expose the work you intend—not the whole workspace.</h2>
          </div>
          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {CONTROLS.map(({ icon: Icon, title, body }) => (
              <article key={title} className="rounded-2xl border border-brand-line bg-brand-surface p-6">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-brand-line bg-brand-bg text-brand-accent-2"><Icon size={21} /></div>
                <h3 className="mt-5 font-serif text-[18px] font-bold">{title}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-brand-ink-2">{body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="max-w-2xl">
          <span className="text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Two ways to connect</span>
          <h2 className="mt-3 font-serif text-[34px] font-bold leading-tight">One person’s workspace, or one approved system.</h2>
          <p className="mt-4 font-sans text-[15px] leading-relaxed text-brand-ink-2">
            The two connection types authenticate differently and reach different work. Both are
            enabled by the firm, and both are being released deliberately rather than opened by default.
          </p>
        </div>
        <div className="mt-10 grid gap-5 md:grid-cols-2">
          {CONNECTION_TYPES.map(({ icon: Icon, label, title, body, points }) => (
            <article key={title} className="flex flex-col rounded-2xl border border-brand-line bg-brand-surface p-7">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-brand-line bg-brand-bg text-brand-accent-2"><Icon size={21} /></div>
                <span className="rounded-full border border-brand-gold/30 bg-brand-gold/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.13em] text-brand-gold">{label}</span>
              </div>
              <h3 className="mt-5 font-serif text-[22px] font-bold">{title}</h3>
              <p className="mt-3 text-[14px] leading-relaxed text-brand-ink-2">{body}</p>
              <ul className="mt-5 grid gap-3 border-t border-brand-line pt-5">
                {points.map((point) => (
                  <li key={point} className="flex items-start gap-3 text-[14px] leading-relaxed text-brand-ink-2">
                    <CheckCircle2 size={17} className="mt-0.5 shrink-0 text-brand-green" aria-hidden="true" />{point}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 pb-16 md:pb-24">
        <div className="grid gap-8 lg:grid-cols-[0.78fr_1.22fr]">
          <div className="rounded-3xl border border-brand-line bg-brand-surface p-8">
            <Network size={28} className="text-brand-accent-2" />
            <h2 className="mt-5 font-serif text-[28px] font-bold">Built for controlled connections.</h2>
            <p className="mt-3 text-[15px] leading-relaxed text-brand-ink-2">The private preview is for teams ready to validate research access, client compatibility, and its boundaries with us.</p>
            <p className="mt-3 font-mono text-[12px] text-brand-muted">https://research.getlawhand.com/api/mcp</p>
            <p className="mt-2 text-[12px] leading-relaxed text-brand-muted">Use the full URL in saved configuration; the bare hostname remains supported as shorthand.</p>
          </div>
          <div className="rounded-3xl border border-brand-line bg-brand-bg-soft p-8">
            <p className="text-[11px] font-bold uppercase tracking-[0.15em] text-brand-accent-2">Preview readiness</p>
            <ul className="mt-5 grid gap-4 sm:grid-cols-2">
              {[
                'A named ChatGPT, Claude, or API client',
                'A defined public-authority research workflow',
                'An owner for access and usage review',
                'A production rollout after release gates pass',
              ].map((item) => (
                <li key={item} className="flex items-start gap-3 text-[14px] leading-relaxed text-brand-ink-2">
                  <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-brand-green" />{item}
                </li>
              ))}
            </ul>
            <div className="mt-7 flex items-center gap-3 rounded-xl border border-brand-gold/25 bg-brand-gold/10 px-4 py-3 text-[13px] text-brand-ink-2">
              <LockKeyhole size={18} className="shrink-0 text-brand-gold" /> Public key issuance remains gated while production release checks are completed.
            </div>
          </div>
        </div>
      </section>
    </MarketingPageLayout>
  )
}
