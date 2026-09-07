import { Link } from 'react-router-dom'
import { ArrowRight, Building2, CheckCircle2, Info, Laptop, ShieldAlert } from 'lucide-react'

import MarketingPageLayout from '../components/MarketingChrome'
import {
  CLOUD_PROVIDERS,
  KNOWN_BOUNDARIES,
  ONBOARDING_STEPS,
  PLATFORM_REQUIREMENTS,
  REQUIREMENTS_REVIEW,
  providerScopes,
} from '../marketing/requirements'

function ScopeList({ label, scopes, note }) {
  if (!scopes.length) return null
  return (
    <div className="mt-5">
      <h4 className="text-[12px] font-bold uppercase tracking-[0.12em] text-brand-muted">{label}</h4>
      {note && <p className="mt-1 text-[13px] leading-relaxed text-brand-muted">{note}</p>}
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {scopes.map((scope) => (
          <li key={scope}>
            <code className="inline-block break-all rounded-md border border-brand-line bg-brand-bg-soft px-2 py-1 text-[12px] text-brand-ink-2">
              {scope}
            </code>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ProviderCard({ provider }) {
  const scopes = providerScopes(provider.id)
  return (
    <article className="rounded-3xl border border-brand-line bg-brand-surface p-7">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-bg-soft text-brand-accent-2">
          <Building2 size={18} aria-hidden="true" />
        </span>
        <div>
          <h3 className="font-serif text-2xl font-bold">{provider.name}</h3>
          <p className="mt-1 text-[13px] text-brand-muted">Identity: {provider.identity}</p>
        </div>
      </div>

      <div className="mt-6 rounded-xl border border-brand-accent/20 bg-brand-accent/5 p-4">
        <h4 className="text-[12px] font-bold uppercase tracking-[0.12em] text-brand-accent-2">Who must approve</h4>
        <p className="mt-2 leading-6 text-brand-ink">{provider.adminRole}</p>
      </div>

      <h4 className="mt-6 text-[12px] font-bold uppercase tracking-[0.12em] text-brand-muted">What LawHand connects to</h4>
      <ul className="mt-2 space-y-2">
        {provider.connects.map((item) => (
          <li key={item} className="flex gap-2.5 leading-6 text-brand-ink-2">
            <CheckCircle2 size={16} className="mt-1 shrink-0 text-brand-green" aria-hidden="true" />
            {item}
          </li>
        ))}
      </ul>

      <ScopeList label="Tenant-wide admin consent requests" scopes={scopes.admin} />
      <ScopeList label="Per-user connection requests" scopes={scopes.user} />
      <ScopeList
        label="Added only on Teams opt-in"
        scopes={scopes.teamsOptIn}
        note="Not requested unless the firm explicitly enables Microsoft Teams."
      />

      <p className="mt-6 border-t border-brand-line pt-5 text-[14px] leading-6 text-brand-ink-2">
        {provider.perUserAlternative}
      </p>
      <p className="mt-2 text-[13px] leading-6 text-brand-muted">{provider.optional}</p>
    </article>
  )
}

export default function RequirementsPage() {
  return (
    <MarketingPageLayout>
      <section className="border-b border-brand-line bg-brand-bg-soft/50">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <span className="text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">Requirements and integrations</span>
          <h1 className="mt-5 max-w-4xl font-serif text-[44px] font-bold leading-[1.04] tracking-tight md:text-[56px]">
            What your firm needs before day one.
          </h1>
          <p className="mt-6 max-w-3xl text-[18px] leading-relaxed text-brand-ink-2">
            Every capability LawHand advertises has a prerequisite behind it. This page
            states them plainly: what runs where, which administrator has to click approve,
            exactly what that approval grants, and where the current limits are.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              to="/request-demo"
              className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-brand-ink px-6 text-[15px] font-semibold text-white shadow-sm transition-all hover:-translate-y-px hover:bg-brand-ink-2"
            >
              Book a demo <ArrowRight size={16} aria-hidden="true" />
            </Link>
            <Link
              to="/support"
              className="inline-flex min-h-12 items-center rounded-lg border border-brand-line bg-brand-surface px-5 text-[15px] font-semibold transition-colors hover:bg-brand-bg-soft"
            >
              Support and response objectives
            </Link>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-14 md:py-20">
        <h2 className="font-serif text-[32px] font-bold tracking-tight md:text-[38px]">Platform requirements</h2>
        <p className="mt-4 max-w-2xl text-[16px] leading-relaxed text-brand-ink-2">
          There is nothing to install and nothing for the firm to host.
        </p>
        <div className="mt-8 grid gap-5 sm:grid-cols-2">
          {PLATFORM_REQUIREMENTS.map((item) => (
            <article key={item.id} className="rounded-2xl border border-brand-line bg-brand-surface p-6">
              <Laptop size={18} className="text-brand-accent-2" aria-hidden="true" />
              <h3 className="mt-3 font-serif text-xl font-bold">{item.title}</h3>
              <p className="mt-2 leading-7 text-brand-ink-2">{item.detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="cloud" className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto max-w-6xl px-6 py-14 md:py-20">
          <h2 className="font-serif text-[32px] font-bold tracking-tight md:text-[38px]">Microsoft 365 and Google Workspace</h2>
          <p className="mt-4 max-w-3xl text-[16px] leading-relaxed text-brand-ink-2">
            Connecting a cloud tenant is optional — LawHand runs without it — but mail,
            document storage, calendar, and user matching all depend on it. The scope
            tokens below are the ones your administrator will see on the consent screen.
          </p>
          <div className="mt-9 grid gap-6 lg:grid-cols-2">
            {CLOUD_PROVIDERS.map((provider) => <ProviderCard key={provider.id} provider={provider} />)}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-14 md:py-20">
        <h2 className="font-serif text-[32px] font-bold tracking-tight md:text-[38px]">How onboarding runs</h2>
        <ol className="mt-8 space-y-4">
          {ONBOARDING_STEPS.map((step, index) => (
            <li key={step.id} className="flex gap-5 rounded-2xl border border-brand-line bg-brand-surface p-6">
              <span
                aria-hidden="true"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-ink font-serif text-sm font-bold text-white"
              >
                {index + 1}
              </span>
              <div>
                <h3 className="font-serif text-xl font-bold">{step.title}</h3>
                <p className="mt-2 leading-7 text-brand-ink-2">{step.detail}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto max-w-6xl px-6 py-14 md:py-20">
          <div className="flex items-center gap-3">
            <ShieldAlert size={22} className="text-brand-accent-2" aria-hidden="true" />
            <h2 className="font-serif text-[32px] font-bold tracking-tight md:text-[38px]">Current limits worth knowing</h2>
          </div>
          <p className="mt-4 max-w-3xl text-[16px] leading-relaxed text-brand-ink-2">
            Published before you sign, not discovered afterward.
          </p>
          <div className="mt-8 grid gap-5 md:grid-cols-2">
            {KNOWN_BOUNDARIES.map((item) => (
              <article key={item.id} className="rounded-2xl border border-brand-line bg-brand-surface p-6">
                <h3 className="font-serif text-xl font-bold">{item.title}</h3>
                <p className="mt-2 leading-7 text-brand-ink-2">{item.detail}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-14 md:py-20">
        <div className="rounded-3xl border border-brand-line bg-brand-surface p-7 md:p-9">
          <div className="flex items-start gap-3">
            <Info size={20} className="mt-0.5 shrink-0 text-brand-accent-2" aria-hidden="true" />
            <div>
              <h2 className="font-serif text-2xl font-bold">Reviewing LawHand for your firm?</h2>
              <p className="mt-3 max-w-3xl leading-7 text-brand-ink-2">
                The trust center publishes the current operating controls, subprocessor
                registry, assurance state, and a downloadable security-review packet.
                Onboarding scope and any specialized commitments are confirmed in your
                order rather than on this page.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <Link to="/trust-center" className="inline-flex min-h-11 items-center gap-2 text-[15px] font-semibold text-brand-accent-2 hover:underline">
                  Trust center <ArrowRight size={16} aria-hidden="true" />
                </Link>
                <Link to="/pricing" className="inline-flex min-h-11 items-center gap-2 text-[15px] font-semibold text-brand-accent-2 hover:underline">
                  Pricing <ArrowRight size={16} aria-hidden="true" />
                </Link>
              </div>
              <p className="mt-6 text-[13px] text-brand-muted">
                Reviewed by {REQUIREMENTS_REVIEW.owner} on {REQUIREMENTS_REVIEW.reviewedAt}.
                Next review {REQUIREMENTS_REVIEW.nextReviewAt}.
              </p>
            </div>
          </div>
        </div>
      </section>
    </MarketingPageLayout>
  )
}
