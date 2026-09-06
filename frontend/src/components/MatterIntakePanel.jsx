import { useCallback, useEffect, useState } from 'react'
import api from '../api'
import IntakeSetupFields, { defaultIntakeSetup, intakeOptions } from './IntakeSetupFields'

export async function startMatterIntake(matterId, options, file) {
  const data = new FormData(); data.append('options', JSON.stringify(options)); data.append('agreement', file)
  return (await api.post(`/matters/${matterId}/intake`, data)).data
}

const date = value => value ? new Date(value).toLocaleString() : 'Not yet'
const errorText = e => typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : 'Could not update intake. Check the fields and try again.'

export default function MatterIntakePanel({ matterId, documents = [] }) {
  const [packet, setPacket] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [setup, setSetup] = useState(defaultIntakeSetup)
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [receipt, setReceipt] = useState({ requirement: 'fee_agreement', document_id: '', note: '' })
  const [meeting, setMeeting] = useState({ kind: 'conference_call', starts_at: '', details: '' })
  const [retryKey, setRetryKey] = useState('')
  const input = 'block w-full border border-brand-line rounded-lg p-2 bg-white text-brand-ink'
  const load = useCallback(async () => {
    try { setPacket((await api.get(`/matters/${matterId}/intake`)).data); setError('') }
    catch (e) { if (e.response?.status !== 404) setError(errorText(e)) }
    finally { setLoading(false) }
  }, [matterId])
  useEffect(() => { load(); const timer = setInterval(load, 30000); return () => clearInterval(timer) }, [load])
  async function action(path, body) {
    setBusy(true); setError('')
    try { setPacket((await api.post(`/matters/${matterId}/intake/${path}`, body)).data) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  async function start() {
    setBusy(true); setError('')
    try { setPacket(await startMatterIntake(matterId, intakeOptions(setup), file)) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  if (loading) return <p role="status">Loading intake…</p>
  return <section className="space-y-4 p-4" aria-label="Matter intake">
    <div className="flex justify-between"><h3 className="font-semibold text-lg">Client intake</h3><button type="button" onClick={load}>Refresh intake</button></div>
    {!packet && <><IntakeSetupFields value={setup} onChange={setSetup} onFile={setFile} /><button type="button" className={input} disabled={busy || !file} onClick={start}>Start intake & send portal invitation</button></>}
    {packet && <>
      <p role="status">{packet.status.replaceAll('_', ' ')}</p>
      <ul>{Object.entries(packet.requirements).map(([key, state]) => <li key={key}>{key === 'fee_agreement' ? 'Fee agreement' : 'Questionnaire'}: {state.completed ? `Complete — ${date(state.completed_at)}` : 'Outstanding'}</li>)}</ul>
      <p>Initial packet sent: {date(packet.sent_at)}</p>
      {packet.scheduling_due_at && <p className="font-semibold">Contact client to schedule by {date(packet.scheduling_due_at)}</p>}
      <ul>{Object.entries(packet.delivery).map(([key, state]) => <li key={key}>{key.replace(':', ' · ')}: {state.state} {state.detail || ''}{['failed', 'blocked', 'unknown'].includes(state.state) && <button type="button" className="ml-2 underline" onClick={() => setRetryKey(key)}>Review delivery</button>}</li>)}</ul>
      {retryKey && <div className="border rounded p-3"><p>Check {retryKey} in the provider’s delivery records before retrying. An unknown result may already have reached the client.</p><button type="button" disabled={busy} onClick={async () => { await action('retry', { delivery_key: retryKey, confirm_not_sent: true }); setRetryKey('') }}>I verified it was not sent — retry</button><button type="button" onClick={() => setRetryKey('')}>Close</button></div>}
      {Object.keys(packet.answers).length > 0 && <details><summary>View completed questionnaire</summary>{packet.questions.map(q => <div className="py-2" key={q.key}><strong>{q.label}</strong><p className="whitespace-pre-wrap">{packet.answers[q.key]}</p></div>)}</details>}
      {packet.status === 'awaiting_documents' && <details><summary>Record a document received outside the portal</summary><div className="space-y-2 p-2">
        <label>Requirement<select className={input} value={receipt.requirement} onChange={e => setReceipt({ ...receipt, requirement: e.target.value })}><option value="fee_agreement">Signed fee agreement</option><option value="questionnaire">Completed questionnaire</option></select></label>
        <label>Received document<select className={input} value={receipt.document_id} onChange={e => setReceipt({ ...receipt, document_id: e.target.value })}><option value="">Choose an uploaded matter document</option>{documents.map(doc => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}</select></label>
        <label>Verification note<input className={input} value={receipt.note} onChange={e => setReceipt({ ...receipt, note: e.target.value })} /></label>
        <button type="button" disabled={busy || !receipt.document_id || !receipt.note} onClick={() => action('receipt', receipt)}>Confirm document is complete</button>
      </div></details>}
      {packet.status === 'documents_complete' && <fieldset className="space-y-2"><legend>Record initial meeting</legend>
        <label>Meeting type<select className={input} value={meeting.kind} onChange={e => setMeeting({ ...meeting, kind: e.target.value })}><option value="conference_call">Conference call</option><option value="in_person">In-person meeting</option></select></label>
        <label>Meeting date and time<input className={input} type="datetime-local" value={meeting.starts_at} onChange={e => setMeeting({ ...meeting, starts_at: e.target.value })} /></label>
        <label>Call details or office location<textarea className={input} value={meeting.details} onChange={e => setMeeting({ ...meeting, details: e.target.value })} /></label>
        <button type="button" disabled={busy || !meeting.starts_at || !meeting.details} onClick={() => action('meeting', { ...meeting, starts_at: new Date(meeting.starts_at).toISOString() })}>Save meeting & notify client</button>
      </fieldset>}
      {packet.meeting && <p>{packet.meeting.kind === 'in_person' ? 'In-person meeting' : 'Conference call'}: {date(packet.meeting.starts_at)} — {packet.meeting.details}</p>}
      {packet.status !== 'cancelled' && <p><button type="button" className="underline" disabled={busy} onClick={() => action('renew-invitation', {})}>Send renewed portal invitation</button> � replaces the previous invitation and portal sessions.</p>}
      {packet.status !== 'cancelled' && <button type="button" disabled={busy} onClick={() => action('cancel', {})}>Cancel intake follow-ups</button>}
    </>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
  </section>
}
