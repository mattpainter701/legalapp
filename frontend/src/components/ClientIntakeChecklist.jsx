import { useCallback, useEffect, useState } from 'react'
import api, { getClientIntake, submitClientIntake, uploadClientPortalDocument, downloadClientPortalDocumentUrl } from '../api'

export default function ClientIntakeChecklist({ onSign }) {
  const [packet, setPacket] = useState(null)
  const [answers, setAnswers] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try { const result = await getClientIntake(); setPacket(result); setAnswers(previous => Object.keys(previous).length ? previous : result.answers); setError('') }
    catch (e) { if (e.response?.status !== 404) setError('Your intake checklist could not load. Please retry.') }
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
  return <section className="bg-brand-surface border border-brand-line rounded-xl p-5 my-4 space-y-4 font-sans" aria-label="Your intake checklist">
    <div className="flex items-center justify-between gap-3">
      <h2 className="font-serif font-bold text-lg text-brand-ink">Your intake checklist</h2>
      <button
        type="button"
        onClick={load}
        className="inline-flex items-center gap-1.5 text-xs font-sans font-medium text-brand-ink-2 hover:text-brand-ink border border-brand-line rounded-lg px-3 py-1.5 transition-colors"
      >
        Refresh checklist
      </button>
    </div>
    {packet && <div className="space-y-4">
      <div className="space-y-1">
        <p className="text-sm text-brand-ink">Fee agreement: {packet.requirements.fee_agreement.completed ? 'Complete' : 'Awaiting your signature'}</p>
        {!packet.requirements.fee_agreement.completed && packet.status === 'awaiting_documents' && (
          <button type="button" className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-semibold rounded-lg hover:bg-brand-ink-2 transition-all" onClick={onSign}>
            Review and sign fee agreement
          </button>
        )}
      </div>
      <div className="space-y-2">
        <p className="text-sm text-brand-ink">Questionnaire: {packet.requirements.questionnaire.completed ? 'Complete' : 'Please complete the questions below'}</p>
        {!packet.requirements.questionnaire.completed && packet.status === 'awaiting_documents' && <form className="space-y-3" onSubmit={submit}>
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
          <button disabled={busy} className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-semibold rounded-lg hover:bg-brand-ink-2 transition-all disabled:opacity-50" type="submit">
            {busy ? 'Saving…' : 'Submit completed questionnaire'}
          </button>
        </form>}
      </div>
      {Object.entries(packet.requirements).filter(([, item]) => item.kind).map(([key, item]) => (
        <div key={key} className="border border-brand-line rounded-xl p-4 space-y-2">
          <strong className="block text-sm text-brand-ink">{item.label}</strong>
          <p className="text-xs text-brand-ink-2">{item.completed ? 'Complete' : item.submitted_document_id ? 'Submitted — awaiting staff review' : 'Outstanding'}</p>
          {item.kind === 'signature'
            ? <button type="button" className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-semibold rounded-lg hover:bg-brand-ink-2 transition-all" onClick={onSign}>Review and sign</button>
            : <>
              {item.document_id && <a href={downloadClientPortalDocumentUrl(item.document_id)} className="inline-flex items-center gap-1.5 text-sm text-brand-accent hover:underline">Download form</a>}
              {!item.completed && (
                <label className="block text-sm text-brand-ink">
                  {item.kind === 'upload' ? `Upload ${item.label || 'requested record'}` : 'Upload completed document'}
                  <input className="mt-1 block text-sm" type="file" disabled={busy} onChange={event => uploadRequirement(key, event.target.files?.[0])} />
                </label>
              )}
            </>}
        </div>
      ))}
      {packet.completed_at && !packet.meeting && <p className="text-sm text-brand-green">Thank you. Your paperwork is complete. Your legal team will contact you to schedule your first meeting.</p>}
      {packet.meeting && <p className="text-sm text-brand-ink">{packet.meeting.kind === 'in_person' ? 'In-person meeting' : 'Conference call'}: {new Date(packet.meeting.starts_at).toLocaleString()} — {packet.meeting.details}</p>}
      {packet.status === 'cancelled' && <p className="text-sm text-brand-ink-2">This intake is closed. Contact your legal team if you need assistance.</p>}
    </div>}
    {error && <p role="alert" className="text-sm text-brand-rose">{error}</p>}
  </section>
}
