import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, LifeBuoy, Send } from 'lucide-react'

import {
  createOperatingSupportRequest,
  getPublicSupportPolicy,
  listOperatingSupportRequests,
} from '../../api'

const SEVERITIES = ['S1', 'S2', 'S3', 'S4']

const CHANNELS = [
  'workspace',
  'email',
  'phone',
]

const initialForm = {
  severity: 'S3',
  channel: 'workspace',
  subject: '',
  safe_summary: '',
}

const statusStyle = {
  open: 'border-amber-700/25 bg-amber-50 text-amber-900',
  acknowledged: 'border-blue-700/25 bg-blue-50 text-blue-900',
  mitigated: 'border-indigo-700/25 bg-indigo-50 text-indigo-900',
  resolved: 'border-emerald-700/25 bg-emerald-50 text-emerald-900',
}

function formatTimestamp(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

/** Surface the backend's own message; it names the field that was rejected. */
function errorMessage(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return error?.message || 'The support request could not be filed. Please try again.'
}

export default function SupportTab() {
  const [policy, setPolicy] = useState(null)
  const [requests, setRequests] = useState([])
  const [form, setForm] = useState(initialForm)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [filed, setFiled] = useState(null)

  const severityDetail = useMemo(
    () => policy?.severities?.find((item) => item.severity === form.severity) || null,
    [policy, form.severity],
  )

  const loadRequests = () =>
    listOperatingSupportRequests()
      .then((data) => setRequests(data?.items || []))
      .catch(() => { /* the form stays usable even if history cannot load */ })

  useEffect(() => {
    let active = true
    getPublicSupportPolicy()
      .then((next) => { if (active) setPolicy(next) })
      .catch(() => { /* severity picker falls back to bare S1-S4 labels */ })
    loadRequests()
    return () => { active = false }
  }, [])

  const update = (event) => {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }))
  }

  const submit = async (event) => {
    event.preventDefault()
    setStatus('submitting')
    setError('')
    try {
      const created = await createOperatingSupportRequest(form)
      setFiled(created)
      setForm(initialForm)
      setStatus('idle')
      await loadRequests()
    } catch (submissionError) {
      setError(errorMessage(submissionError))
      setStatus('error')
    }
  }

  return (
    <div className="space-y-8">
      <section>
        <div className="flex items-start gap-3">
          <LifeBuoy size={22} className="mt-1 shrink-0 text-brand-accent-2" aria-hidden="true" />
          <div>
            <h2 className="font-serif text-2xl font-bold">File a support request</h2>
            <p className="mt-1 text-sm text-brand-ink-2">
              A classified request records the acknowledgement objective and the policy
              version it was classified under.
              {policy?.coverage?.standard_hours
                ? ` Standard coverage: ${policy.coverage.standard_hours}.`
                : ''}
            </p>
          </div>
        </div>

        {filed && (
          <div role="status" className="mt-5 rounded-xl border border-emerald-700/25 bg-emerald-50 p-5 text-emerald-900">
            <p className="font-semibold">Request {filed.severity} filed.</p>
            <p className="mt-1 text-sm">
              Acknowledgement objective {filed.acknowledgement_objective_minutes} minutes —
              due by {formatTimestamp(filed.acknowledgement_due_at)}.
              Classified under policy version {filed.policy_version}.
            </p>
          </div>
        )}

        <form onSubmit={submit} className="mt-6 rounded-2xl border border-brand-line bg-brand-surface p-6">
          <div className="grid gap-5 sm:grid-cols-2">
            <label className="text-sm font-semibold text-brand-ink">Severity
              <select
                name="severity"
                value={form.severity}
                onChange={update}
                className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal"
              >
                {SEVERITIES.map((severity) => <option key={severity} value={severity}>{severity}</option>)}
              </select>
            </label>
            <label className="text-sm font-semibold text-brand-ink">Channel
              <select
                name="channel"
                value={form.channel}
                onChange={update}
                className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal capitalize"
              >
                {CHANNELS.map((channel) => <option key={channel} value={channel}>{channel}</option>)}
              </select>
            </label>
          </div>

          {severityDetail && (
            <div className="mt-4 rounded-xl bg-brand-bg-soft p-4 text-sm leading-6 text-brand-ink-2">
              <p>{severityDetail.definition}</p>
              <p className="mt-2 text-brand-muted">
                Acknowledgement objective: {severityDetail.acknowledgement_objective_minutes} minutes.
                First owner: {severityDetail.initial_owner}.
              </p>
            </div>
          )}

          <label className="mt-5 block text-sm font-semibold text-brand-ink">Subject
            <input
              name="subject"
              required
              maxLength="255"
              value={form.subject}
              onChange={update}
              className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal"
            />
          </label>

          <label className="mt-5 block text-sm font-semibold text-brand-ink">Summary
            <textarea
              name="safe_summary"
              required
              rows="5"
              maxLength="4000"
              value={form.safe_summary}
              onChange={update}
              placeholder="What you expected, what happened, roughly when, and how many people are affected."
              className="mt-2 w-full rounded-lg border border-brand-line bg-white px-3.5 py-3 font-normal"
            />
          </label>

          <p className="mt-3 flex gap-2 text-xs leading-relaxed text-brand-muted">
            <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
            Support records are stored without secrets or unnecessary client content. Do
            not paste passwords, API keys, tokens, authorization codes, or privileged
            client material — submissions containing them are rejected.
          </p>

          {error && (
            <p role="alert" className="mt-4 rounded-lg border border-brand-rose/30 bg-brand-rose/10 px-4 py-3 text-sm text-brand-rose">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={status === 'submitting'}
            className="mt-5 inline-flex min-h-11 items-center gap-2 rounded-lg bg-brand-accent px-5 font-semibold text-white disabled:opacity-60"
          >
            <Send size={16} aria-hidden="true" />
            {status === 'submitting' ? 'Filing…' : 'File request'}
          </button>
        </form>

        {policy?.objective_boundary && (
          <p className="mt-4 text-xs leading-relaxed text-brand-muted">{policy.objective_boundary}</p>
        )}
      </section>

      <section>
        <h2 className="font-serif text-2xl font-bold">Your firm’s requests</h2>
        {requests.length === 0 ? (
          <p className="mt-3 rounded-xl border border-brand-line bg-brand-surface p-6 text-sm text-brand-ink-2">
            No support requests have been filed from this workspace yet.
          </p>
        ) : (
          <div className="mt-4 overflow-x-auto rounded-2xl border border-brand-line bg-brand-surface">
            <table className="w-full min-w-[46rem] text-left text-sm">
              <thead>
                <tr className="border-b border-brand-line bg-brand-bg-soft/60">
                  <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-wide text-brand-muted">Severity</th>
                  <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-wide text-brand-muted">Subject</th>
                  <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-wide text-brand-muted">Status</th>
                  <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-wide text-brand-muted">Acknowledgement due</th>
                  <th scope="col" className="px-5 py-3 text-xs font-bold uppercase tracking-wide text-brand-muted">Filed</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((item) => (
                  <tr key={item.id} className="border-b border-brand-line last:border-0 align-top">
                    <th scope="row" className="px-5 py-4 font-semibold">{item.severity}</th>
                    <td className="px-5 py-4 text-brand-ink-2">{item.subject}</td>
                    <td className="px-5 py-4">
                      <span className={`rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${statusStyle[item.status] || statusStyle.open}`}>
                        {item.status}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-brand-ink-2">{formatTimestamp(item.acknowledgement_due_at)}</td>
                    <td className="px-5 py-4 text-brand-ink-2">{formatTimestamp(item.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
