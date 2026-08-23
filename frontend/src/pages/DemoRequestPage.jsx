import { useRef, useState } from 'react'
import { CheckCircle2, Mail, ShieldCheck } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import MarketingPageLayout from '../components/MarketingChrome'
import { submitDemoRequest } from '../api'
import { campaignProperties, trackMarketingEvent } from '../marketingAnalytics'

const initialForm = {
  name: '',
  email: '',
  firm_name: '',
  phone: '',
  team_size: '',
  message: '',
  website: '',
}

export default function DemoRequestPage() {
  const [searchParams] = useSearchParams()
  const [form, setForm] = useState(initialForm)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const started = useRef(false)

  const begin = () => {
    if (!started.current) {
      started.current = true
      trackMarketingEvent('demo_form_started', { placement: searchParams.get('source') || 'direct' })
    }
  }

  const update = (event) => {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }))
  }

  const submit = async (event) => {
    event.preventDefault()
    setStatus('submitting')
    setError('')
    try {
      await submitDemoRequest({
        ...form,
        source_path: searchParams.get('from') || '/demo',
        campaign: {
          ...campaignProperties(),
          placement: searchParams.get('source') || 'direct',
          referrer: document.referrer || '',
        },
      })
      trackMarketingEvent('demo_form_submitted', { placement: searchParams.get('source') || 'direct' })
      setStatus('submitted')
      setForm(initialForm)
    } catch (submissionError) {
      setStatus('error')
      setError(submissionError?.message || 'We could not submit your request. Please try again.')
    }
  }

  if (status === 'submitted') {
    return (
      <MarketingPageLayout>
        <section className="mx-auto max-w-3xl px-6 py-20 text-center md:py-28">
          <CheckCircle2 className="mx-auto text-brand-green" size={48} aria-hidden="true" />
          <h1 className="mt-6 font-serif text-4xl font-bold">Your request is in hand.</h1>
          <p className="mx-auto mt-4 max-w-xl text-lg leading-relaxed text-brand-ink-2">
            Thanks for reaching out. We’ll review your firm’s needs and reply to the email you provided.
          </p>
          <Link to="/" className="mt-8 inline-flex min-h-12 items-center rounded-lg bg-brand-ink px-6 font-semibold text-white">Return home</Link>
        </section>
      </MarketingPageLayout>
    )
  }

  return (
    <MarketingPageLayout>
      <section className="mx-auto grid max-w-6xl gap-12 px-6 py-16 md:py-24 lg:grid-cols-[0.85fr_1.15fr] lg:items-start">
        <div>
          <span className="text-xs font-bold uppercase tracking-[0.16em] text-brand-accent-2">Book a LawHand demo</span>
          <h1 className="mt-4 font-serif text-4xl font-bold leading-tight md:text-5xl">Show us where the work gets stuck.</h1>
          <p className="mt-6 text-lg leading-relaxed text-brand-ink-2">
            Tell us a little about your firm. We’ll focus the conversation on the workflows, sources, and controls that matter to your team.
          </p>
          <div className="mt-8 space-y-4 text-sm text-brand-ink-2">
            <p className="flex gap-3"><ShieldCheck className="mt-0.5 shrink-0 text-brand-green" size={19} />No generic sales deck—your workflow drives the demo.</p>
            <p className="flex gap-3"><Mail className="mt-0.5 shrink-0 text-brand-accent-2" size={19} />Prefer email? Write to <a className="font-semibold underline" href="mailto:support@getlawhand.com">support@getlawhand.com</a>.</p>
          </div>
        </div>

        <form onSubmit={submit} onFocus={begin} className="rounded-3xl border border-brand-line bg-brand-surface p-6 shadow-xl sm:p-8">
          <div className="grid gap-5 sm:grid-cols-2">
            <label className="text-sm font-semibold text-brand-ink">Name
              <input name="name" required minLength="2" maxLength="200" autoComplete="name" value={form.name} onChange={update} className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal" />
            </label>
            <label className="text-sm font-semibold text-brand-ink">Work email
              <input name="email" type="email" required maxLength="320" autoComplete="email" value={form.email} onChange={update} className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal" />
            </label>
            <label className="text-sm font-semibold text-brand-ink">Firm or organization
              <input name="firm_name" required minLength="2" maxLength="300" autoComplete="organization" value={form.firm_name} onChange={update} className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal" />
            </label>
            <label className="text-sm font-semibold text-brand-ink">Phone <span className="font-normal text-brand-muted">(optional)</span>
              <input name="phone" type="tel" maxLength="60" autoComplete="tel" value={form.phone} onChange={update} className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal" />
            </label>
            <label className="text-sm font-semibold text-brand-ink sm:col-span-2">Team size
              <select name="team_size" value={form.team_size} onChange={update} className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal">
                <option value="">Select a range</option>
                <option value="1-5">1–5 people</option>
                <option value="6-20">6–20 people</option>
                <option value="21-50">21–50 people</option>
                <option value="51+">51+ people</option>
              </select>
            </label>
            <label className="text-sm font-semibold text-brand-ink sm:col-span-2">What would you like to improve?
              <textarea name="message" rows="5" maxLength="3000" value={form.message} onChange={update} placeholder="Intake, matter handoffs, document review, research, deadlines…" className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal" />
            </label>
          </div>
          <label className="absolute -left-[10000px]" aria-hidden="true">Website
            <input name="website" tabIndex="-1" autoComplete="off" value={form.website} onChange={update} />
          </label>
          {error && <p role="alert" className="mt-5 rounded-lg border border-brand-rose/30 bg-brand-rose/10 px-4 py-3 text-sm text-brand-rose">{error}</p>}
          <button type="submit" disabled={status === 'submitting'} className="mt-6 inline-flex min-h-12 w-full items-center justify-center rounded-lg bg-brand-accent px-6 font-semibold text-white disabled:opacity-60">
            {status === 'submitting' ? 'Sending…' : 'Request a focused demo'}
          </button>
          <p className="mt-4 text-xs leading-relaxed text-brand-muted">By submitting, you agree that LawHand may contact you about this request. Please do not include confidential client information.</p>
        </form>
      </section>
    </MarketingPageLayout>
  )
}