import { useCallback, useEffect, useState } from 'react'
import { Download, PenLine, Upload } from 'lucide-react'
import api, { getClientIntake, submitClientIntake, uploadClientPortalDocument, downloadClientPortalDocumentUrl } from '../api'

// Records are things the client already has — a photo of an ID, a scanned
// statement, a Word file from another firm — so accept the everyday formats.
const RECORD_ACCEPT = 'application/pdf,image/*,.pdf,.png,.jpg,.jpeg,.heic,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document'
// A completed form comes back as the PDF it was sent as, or a photo of it.
const FORM_ACCEPT = 'application/pdf,image/*,.pdf,.png,.jpg,.jpeg,.heic'

const PRIMARY_BUTTON = 'inline-flex items-center gap-1.5 px-4 py-2 bg-brand-ink text-white text-sm font-sans font-semibold rounded-lg hover:bg-brand-ink-2 transition-all disabled:opacity-50'
const LINK_CLASS = 'inline-flex items-center gap-1.5 text-sm text-brand-accent hover:underline'

const TONES = {
  amber: 'bg-brand-amber/10 text-brand-amber',
  green: 'bg-brand-green/10 text-brand-green',
  rose: 'bg-brand-rose/10 text-brand-rose',
  muted: 'bg-brand-bg-soft text-brand-ink-2',
}

export function formStatus(item) {
  // A `document` form is filled in and returned but not signed, so its
  // wording never asks for a signature the firm did not request.
  const signed = item.kind !== 'document'
  if (item.completed) return { label: signed ? 'Signed ✓' : 'Completed ✓', tone: 'green' }
  if (item.declined) return { label: 'Declined', tone: 'rose' }
  if (item.submitted_document_id) return { label: 'Awaiting review', tone: 'muted' }
  return { label: signed ? 'Needs your signature' : 'Needs completing', tone: 'amber' }
}

export function recordStatus(item) {
  if (item.completed) return { label: 'Accepted', tone: 'green' }
  if (item.submitted_document_id) return { label: 'Received — under review', tone: 'muted' }
  return { label: 'Needed', tone: 'amber' }
}

function fmtDue(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `Due ${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`
}

function StatusPill({ status }) {
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold whitespace-nowrap ${TONES[status.tone]}`}>
      {status.label}
    </span>
  )
}

function Row({ label, due, status, children }) {
  return (
    <li className="flex flex-col gap-3 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium text-brand-ink">{label}</p>
        {due && <p className="text-xs text-brand-ink-2 mt-0.5">{due}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <StatusPill status={status} />
        {children}
      </div>
    </li>
  )
}

function Group({ heading, explanation, children }) {
  return (
    <div className="border border-brand-line rounded-xl p-4">
      <p className="text-xs uppercase tracking-wide text-brand-ink-2 font-sans">{heading}</p>
      {explanation && <p className="text-xs text-brand-ink-2 mt-1">{explanation}</p>}
      <ul className="divide-y divide-brand-line mt-3">{children}</ul>
    </div>
  )
}

export default function ClientIntakeChecklist({ onSign }) {
  const [packet, setPacket] = useState(null)
  const [answers, setAnswers] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try { const result = await getClientIntake(); setPacket(result); setAnswers(previous => Object.keys(previous).length ? previous : result.answers || {}); setError('') }
    catch (e) { if (e.response?.status !== 404) setError('Your paperwork checklist could not load. Please retry.') }
  }, [])
  useEffect(() => { load() }, [load])
  async function submit(e) {
    e.preventDefault(); setBusy(true); setError('')
    try { setPacket(await submitClientIntake(answers)) }
    catch (e) { setError(typeof e.response?.data?.detail === 'string' ? e.response.data.detail : 'Your questionnaire was not saved. Please retry.') }
    finally { setBusy(false) }
  }
  async function uploadRequirement(key, file) {
    if (!file) return
    setBusy(true); setError('')
    try { const doc = await uploadClientPortalDocument(file, `Intake: ${key}`); const result = await api.post(`/portal/client/intake/requirements/${key}/submission`, { document_id: doc.id }); setPacket(result.data) }
    catch { setError('Could not submit the document. Please retry.') }
    finally { setBusy(false) }
  }
  if (!packet && !error) return null

  const requirements = packet?.requirements || {}
  const entries = Object.entries(requirements)
  // The fee agreement predates per-form requirements: it has no `kind`, and a
  // packet sent without an agreement has no entry at all.
  const forms = [
    ...(requirements.fee_agreement ? [{ key: 'fee_agreement', label: 'Fee agreement', kind: 'signature', ...requirements.fee_agreement }] : []),
    ...entries.filter(([, item]) => item?.kind === 'signature' || item?.kind === 'document').map(([key, item]) => ({ key, ...item })),
  ]
  const records = entries.filter(([, item]) => item?.kind === 'upload').map(([key, item]) => ({ key, ...item }))
  const actionable = packet?.status === 'awaiting_documents'
  // Old packets carried a free-text question list; new ones supply the
  // questionnaire as a PDF form instead, so the answers form only appears when
  // there are questions and they are still unanswered.
  const legacyQuestions = Array.isArray(packet?.questions) && packet.questions.length > 0 && !requirements.questionnaire?.completed

  return <section className="bg-brand-surface border border-brand-line rounded-xl p-5 my-4 space-y-4 font-sans" aria-label="Your paperwork">
    <div className="flex items-start justify-between gap-3">
      <div>
        <h2 className="font-serif font-bold text-lg text-brand-ink">Your paperwork</h2>
        <p className="text-sm text-brand-ink-2 mt-1">Your legal team needs the items below. Forms are completed and signed here; records are uploaded.</p>
      </div>
      <button
        type="button"
        onClick={load}
        className="inline-flex shrink-0 items-center gap-1.5 text-xs font-sans font-medium text-brand-ink-2 hover:text-brand-ink border border-brand-line rounded-lg px-3 py-1.5 transition-colors"
      >
        Refresh checklist
      </button>
    </div>
    {packet && <div className="space-y-4">
      {forms.length > 0 && (
        <Group heading="Forms to complete and sign">
          {forms.map((item) => {
            const status = formStatus(item)
            const open = actionable && !item.completed && !item.submitted_document_id && !item.declined
            return (
              <Row key={item.key} label={item.label || 'Form'} due={!item.completed ? fmtDue(item.due_at) : ''} status={status}>
                {item.kind === 'signature'
                  ? open && (
                    <button type="button" className={PRIMARY_BUTTON} onClick={onSign}>
                      <PenLine size={14} /> Open and sign
                    </button>
                  )
                  : <>
                    {item.document_id && (
                      <a href={downloadClientPortalDocumentUrl(item.document_id)} className={LINK_CLASS}>
                        <Download size={14} /> Download form
                      </a>
                    )}
                    {open && (
                      <label className="flex items-center gap-1.5 text-sm text-brand-ink">
                        <Upload size={14} className="text-brand-ink-2" /> Upload completed form
                        <input className="text-sm" type="file" accept={FORM_ACCEPT} disabled={busy} onChange={event => uploadRequirement(item.key, event.target.files?.[0])} />
                      </label>
                    )}
                  </>}
              </Row>
            )
          })}
        </Group>
      )}
      {legacyQuestions && actionable && (
        <Group heading="Client questionnaire" explanation="Answer the questions below and submit them to your legal team.">
          <li className="pt-0">
            <form className="space-y-3" onSubmit={submit}>
              {packet.questions.map(q => (
                <label className="block text-sm text-brand-ink" key={q.key}>
                  {q.label}{q.required ? ' *' : ''}
                  <textarea
                    className="mt-1 block w-full border border-brand-line rounded-xl px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                    required={q.required}
                    maxLength={20000}
                    value={answers[q.key] || ''}
                    onChange={e => setAnswers({ ...answers, [q.key]: e.target.value })}
                  />
                </label>
              ))}
              <button disabled={busy} className={PRIMARY_BUTTON} type="submit">
                {busy ? 'Saving…' : 'Submit completed questionnaire'}
              </button>
            </form>
          </li>
        </Group>
      )}
      {records.length > 0 && (
        <Group heading="Records to send us" explanation="These are documents you already have — upload a photo or PDF of each.">
          {records.map((item) => (
            <Row key={item.key} label={item.label || 'Requested record'} due={!item.completed ? fmtDue(item.due_at) : ''} status={recordStatus(item)}>
              {actionable && !item.completed && (
                <label className="flex items-center gap-1.5 text-sm text-brand-ink">
                  <Upload size={14} className="text-brand-ink-2" /> Upload {item.label || 'requested record'}
                  <input className="text-sm" type="file" accept={RECORD_ACCEPT} disabled={busy} onChange={event => uploadRequirement(item.key, event.target.files?.[0])} />
                </label>
              )}
            </Row>
          ))}
        </Group>
      )}
      {packet.completed_at && !packet.meeting && <p className="text-sm text-brand-green">Thank you. Your paperwork is complete. Your legal team will contact you to schedule your first meeting.</p>}
      {packet.meeting && <p className="text-sm text-brand-ink">{packet.meeting.kind === 'in_person' ? 'In-person meeting' : 'Conference call'}: {new Date(packet.meeting.starts_at).toLocaleString()} — {packet.meeting.details}</p>}
      {packet.status === 'cancelled' && <p className="text-sm text-brand-ink-2">This intake is closed. Contact your legal team if you need assistance.</p>}
    </div>}
    {error && <p role="alert" className="text-sm text-brand-rose">{error}</p>}
  </section>
}
