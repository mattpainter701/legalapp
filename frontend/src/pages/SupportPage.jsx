import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Clock, Mail, ShieldCheck } from 'lucide-react'

import MarketingPageLayout from '../components/MarketingChrome'
import { getPublicSupportPolicy } from '../api'

const CONTACT_URL = import.meta.env.VITE_CONTACT_URL || 'mailto:support@getlawhand.com'
const CONTACT_LABEL = CONTACT_URL.startsWith('mailto:')
  ? CONTACT_URL.slice('mailto:'.length).split('?')[0]
  : CONTACT_URL

/**
 * Acknowledgement objectives are published in minutes. Render the unit a
 * reader thinks in without rounding away the S1 hour.
 */
function objectiveLabel(minutes) {
  if (!Number.isFinite(minutes)) return 'Not published'
  if (minutes < 60) return `${minutes} minutes`
  const hours = minutes / 60
  const rendered = Number.isInteger(hours) ? hours : hours.toFixed(1)
  return `${rendered} covered ${hours === 1 ? 'hour' : 'hours'}`
}

const SEND_GUIDANCE = [
  'The firm name and the workspace URL you were using.',
  'What you expected to happen and what happened instead.',
  'The approximate time, with your time zone, and how many people are affected.',
  'Whether a workaround exists, so the request can be classified accurately.',
]

const WITHHOLD_GUIDANCE = [
  'Privileged or confidential client material.',
  'Passwords, API keys, tokens, or authorization codes.',
  'Full document contents — a description and an identifier are enough to start.',
]

function SeverityTable({ severities }) {
  return (
    <div className="mt-6 overflow-x-auto rounded-2xl border border-brand-line bg-brand-surface">
      <table className="w-full min-w-[46rem] border-collapse text-left text-sm">
        <caption className="sr-only">
          LawHand support severity definitions and acknowledgement objectives
        </caption>
        <thead>
          <tr className="border-b border-brand-line bg-brand-bg-soft/60">
            <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-[0.12em] text-brand-muted">Severity</th>
            <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-[0.12em] text-brand-muted">What it covers</th>
            <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-[0.12em] text-brand-muted">Acknowledgement objective</th>
            <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-[0.12em] text-brand-muted">First owner</th>
          </tr>
        </thead>
        <tbody>
          {severities.map((item) => (
            <tr key={item.severity} className="border-b border-brand-line last:border-0 align-top">
              <th scope="row" className="whitespace-nowrap px-5 py-4 font-serif text-lg font-bold">{item.severity}</th>
              <td className="px-5 py-4 leading-6 text-brand-ink-2">
                {item.definition}
                <span className="mt-2 block text-[13px] text-brand-muted">{item.escalation}</span>
              </td>
              <td className="whitespace-nowrap px-5 py-4 font-semibold">
                {objectiveLabel(item.acknowledgement_objective_minutes)}
              </td>
              <td className="px-5 py-4 capitalize text-brand-ink-2">{item.initial_owner}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function SupportPage() {
  const [policy, setPolicy] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    getPublicSupportPolicy()
      .then((next) => { if (active) setPolicy(next) })
      .catch(() => {
        if (active) {
          setError('The published support policy is temporarily unavailable. Email us and we will confirm coverage in our reply.')
        }
      })
    return () => { active = false }
  }, [])

  return (
    <MarketingPageLayout>
      <section className="border-b border-brand-line bg-brand-bg-soft/50">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <span className="text-[12px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">LawHand support</span>
          <h1 className="mt-5 max-w-3xl font-serif text-[44px] font-bold leading-[1.04] tracking-tight md:text-[56px]">
            Tell us what broke. We will tell you when we picked it up.
          </h1>
          <p className="mt-6 max-w-2xl text-[18px] leading-relaxed text-brand-ink-2">
            Email reaches the same queue our published severity objectives govern. Firm
            administrators can also file a classified request from inside the workspace,
            which returns the acknowledgement clock in writing.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <a
              href={CONTACT_URL}
              className="inline-flex min-h-12 items-center gap-2 rounded-lg bg-brand-accent px-6 text-[15px] font-semibold text-white shadow-sm transition-all hover:-translate-y-px"
            >
              <Mail size={17} aria-hidden="true" /> {CONTACT_LABEL}
            </a>
            <Link
              to="/trust-center"
              className="inline-flex min-h-12 items-center gap-2 rounded-lg border border-brand-line bg-brand-surface px-5 text-[15px] font-semibold transition-colors hover:bg-brand-bg-soft"
            >
              Incident history <ArrowRight size={16} aria-hidden="true" />
            </Link>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-14 md:py-20">
        <h2 className="font-serif text-[32px] font-bold tracking-tight md:text-[38px]">Coverage and response objectives</h2>

        {error && (
          <p role="alert" className="mt-6 rounded-2xl border border-brand-rose/30 bg-brand-rose/10 px-5 py-4 text-sm text-brand-rose">
            {error}
          </p>
        )}

        {!error && !policy && (
          <p role="status" className="mt-6 rounded-2xl border border-brand-line bg-brand-surface p-6 text-brand-ink-2">
            Loading the published support policy…
          </p>
        )}

        {policy && (
          <>
            <div className="mt-6 grid gap-4 md:grid-cols-3">
              <div className="rounded-2xl border border-brand-line bg-brand-surface p-6">
                <Clock size={18} className="text-brand-accent-2" aria-hidden="true" />
                <h3 className="mt-3 text-[13px] font-bold uppercase tracking-[0.12em] text-brand-muted">Standard hours</h3>
                <p className="mt-2 leading-6 text-brand-ink-2">{policy.coverage?.standard_hours}</p>
              </div>
              <div className="rounded-2xl border border-brand-line bg-brand-surface p-6">
                <AlertTriangle size={18} className="text-brand-accent-2" aria-hidden="true" />
                <h3 className="mt-3 text-[13px] font-bold uppercase tracking-[0.12em] text-brand-muted">Outside those hours</h3>
                <p className="mt-2 leading-6 text-brand-ink-2">{policy.coverage?.after_hours}</p>
              </div>
              <div className="rounded-2xl border border-brand-line bg-brand-surface p-6">
                <ShieldCheck size={18} className="text-brand-accent-2" aria-hidden="true" />
                <h3 className="mt-3 text-[13px] font-bold uppercase tracking-[0.12em] text-brand-muted">Exceptions</h3>
                <p className="mt-2 leading-6 text-brand-ink-2">{policy.coverage?.exceptions}</p>
              </div>
            </div>

            {Array.isArray(policy.severities) && <SeverityTable severities={policy.severities} />}

            <p className="mt-4 text-[13px] text-brand-muted">
              Policy version {policy.version}. This page renders the same policy the
              downloadable security-review packet exports.
            </p>
          </>
        )}

        {/* Rendered from static copy so the boundary is stated even when the
            policy fetch fails. It must never depend on a successful request. */}
        <div className="mt-8 rounded-2xl border border-amber-700/25 bg-amber-50 p-6">
          <h3 className="font-serif text-xl font-bold text-amber-900">These are operating objectives, not an SLA</h3>
          <p className="mt-3 leading-7 text-amber-900/90">
            {policy?.objective_boundary
              || 'Acknowledgement and escalation targets are operating objectives, not an SLA, warranty, or service-credit promise unless incorporated into signed customer terms.'}
          </p>
          <p className="mt-3 leading-7 text-amber-900/90">
            An acknowledgement objective describes when we aim to pick a request up and
            own it. It is not a promise of a resolution time, and it does not create a
            service credit or damages remedy.
          </p>
        </div>
      </section>

      <section className="border-y border-brand-line bg-brand-bg-soft/45">
        <div className="mx-auto grid max-w-6xl gap-8 px-6 py-14 md:grid-cols-2 md:py-20">
          <div>
            <h2 className="font-serif text-[28px] font-bold tracking-tight">What to include</h2>
            <ul className="mt-5 space-y-3 text-brand-ink-2">
              {SEND_GUIDANCE.map((item) => (
                <li key={item} className="flex gap-3 leading-7">
                  <span aria-hidden="true" className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-accent" />
                  {item}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h2 className="font-serif text-[28px] font-bold tracking-tight">What to leave out</h2>
            <ul className="mt-5 space-y-3 text-brand-ink-2">
              {WITHHOLD_GUIDANCE.map((item) => (
                <li key={item} className="flex gap-3 leading-7">
                  <span aria-hidden="true" className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-rose" />
                  {item}
                </li>
              ))}
            </ul>
            <p className="mt-5 text-[13px] leading-relaxed text-brand-muted">
              Support records are kept without secrets or unnecessary customer content.
              Sending less makes a request faster to act on, not slower.
            </p>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-14 md:py-20">
        <div className="grid gap-5 md:grid-cols-2">
          <article className="rounded-2xl border border-brand-line bg-brand-surface p-7">
            <h2 className="font-serif text-xl font-bold">Already a customer?</h2>
            <p className="mt-3 leading-7 text-brand-ink-2">
              Firm administrators can file a severity-classified request from
              Administration → Support. The response records the acknowledgement clock
              and the policy version it was classified under.
            </p>
            <Link to="/login" className="mt-5 inline-flex min-h-11 items-center gap-2 text-[15px] font-semibold text-brand-accent-2 hover:underline">
              Sign in <ArrowRight size={16} aria-hidden="true" />
            </Link>
          </article>
          <article className="rounded-2xl border border-brand-line bg-brand-surface p-7">
            <h2 className="font-serif text-xl font-bold">Setting LawHand up?</h2>
            <p className="mt-3 leading-7 text-brand-ink-2">
              Platform requirements, the Microsoft 365 and Google Workspace matrix, the
              consent your administrator will be asked for, and the onboarding sequence
              are published in full.
            </p>
            <Link to="/requirements" className="mt-5 inline-flex min-h-11 items-center gap-2 text-[15px] font-semibold text-brand-accent-2 hover:underline">
              Requirements and integrations <ArrowRight size={16} aria-hidden="true" />
            </Link>
          </article>
        </div>
      </section>
    </MarketingPageLayout>
  )
}
