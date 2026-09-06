import { useCallback, useEffect, useState } from 'react'
import { getClientIntake, submitClientIntake } from '../api'

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
  if (!packet && !error) return null
  return <section className="border rounded-xl bg-white p-4 my-4 space-y-3" aria-label="Your intake checklist">
    <div className="flex justify-between"><h2 className="text-lg font-bold">Your intake checklist</h2><button type="button" onClick={load}>Refresh checklist</button></div>
    {packet && <>
      <p>Fee agreement: {packet.requirements.fee_agreement.completed ? 'Complete' : 'Awaiting your signature'}</p>
      {!packet.requirements.fee_agreement.completed && packet.status === 'awaiting_documents' && <button type="button" className="underline" onClick={onSign}>Review and sign fee agreement</button>}
      <p>Questionnaire: {packet.requirements.questionnaire.completed ? 'Complete' : 'Please complete the questions below'}</p>
      {!packet.requirements.questionnaire.completed && packet.status === 'awaiting_documents' && <form className="space-y-3" onSubmit={submit}>
        {packet.questions.map(q => <label className="block" key={q.key}>{q.label}{q.required ? ' *' : ''}<textarea className="block w-full border rounded p-2" required={q.required} maxLength={20000} value={answers[q.key] || ''} onChange={e => setAnswers({ ...answers, [q.key]: e.target.value })} /></label>)}
        <button disabled={busy} className="border rounded p-2" type="submit">{busy ? 'Saving…' : 'Submit completed questionnaire'}</button>
      </form>}
      {packet.completed_at && !packet.meeting && <p>Thank you. Your paperwork is complete. Your legal team will contact you to schedule your first meeting.</p>}
      {packet.meeting && <p>{packet.meeting.kind === 'in_person' ? 'In-person meeting' : 'Conference call'}: {new Date(packet.meeting.starts_at).toLocaleString()} — {packet.meeting.details}</p>}
      {packet.status === 'cancelled' && <p>This intake is closed. Contact your legal team if you need assistance.</p>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>
}
