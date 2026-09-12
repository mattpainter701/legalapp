import { useState } from 'react'
import { getIntakeStarterPack } from '../api'
import { dueDateToIso } from './casesetup/paperwork'

export const defaultIntakeSetup = {
  agreement_document_id: '', selected_documents: [], upload_requirements: '', include_questionnaire: true, portal_after_signing: true,
  agreement_due: '', questionnaire_due: '', uploads_due: '',
  email: '', channels: ['email'], timezone: 'America/Chicago', owner_id: '', sms_permission_verified: false, sms_case_updates_verified: false,
  questions: 'Please describe your legal matter.\nWho are the other people or organizations involved?\nWhat important dates should your legal team know about? Enter none if unknown.',
}

export function intakeOptions(setup, clientEmail = '') {
  return {
    email: setup.email || clientEmail, channels: setup.channels, timezone: setup.timezone,
    owner_id: setup.owner_id || null, sms_permission_verified: setup.sms_permission_verified,
    questions: setup.include_questionnaire !== false
      ? setup.questions.split('\n').map(s => s.trim()).filter(Boolean).map((label, i) => ({ key: `question_${i + 1}`, label, required: true }))
      : [],
    agreement_document_id: setup.agreement_document_id || null,
    agreement_due_at: dueDateToIso(setup.agreement_due, setup.timezone),
    questionnaire_due_at: setup.include_questionnaire !== false ? dueDateToIso(setup.questionnaire_due, setup.timezone) : null,
    selected_documents: (setup.selected_documents || []).map(item => ({
      ...item,
      due_at: dueDateToIso(item.due, setup.timezone),
    })),
    include_questionnaire: setup.include_questionnaire !== false,
    portal_after_signing: setup.portal_after_signing === true,
    upload_requirements: (setup.upload_requirements || '').split('\n').map(value => value.trim()).filter(Boolean).map((label, index) => ({ key: `upload_${index + 1}`, label, required: true, due_at: dueDateToIso(setup.uploads_due, setup.timezone) })),
    confirm_send: true,
  }
}

export default function IntakeSetupFields({ value, onChange, onFile, clientEmail = '', users = [], documents = [], matterType = '', practiceArea = '', matterId = '' }) {
  const field = (key, next) => onChange({ ...value, [key]: next })
  const [packNote, setPackNote] = useState('')
  // The standard pack is loaded on request, never silently: it replaces
  // whatever is in the questionnaire and upload boxes, which staff may have
  // already tailored for this client.
  async function loadStarterPack() {
    setPackNote('Loading the standard questions…')
    try {
      const pack = await getIntakeStarterPack(matterId ? { matter_id: matterId } : { matter_type: matterType, practice_area: practiceArea })
      onChange({ ...value, questions: pack.questions.map(q => q.label).join('\n'), upload_requirements: pack.upload_requirements.map(u => u.label).join('\n') })
      setPackNote(`Loaded the ${pack.practice_label} questions and requested uploads. Review and edit them before sending.`)
    } catch { setPackNote('Could not load the standard questions. Enter them below.') }
  }
  const input = 'block w-full border border-brand-line rounded-lg p-2 bg-white text-brand-ink'
  return <fieldset className="space-y-3 border border-brand-line rounded-lg p-3">
    <legend className="font-semibold">Client intake packet</legend>
    {documents.length > 0 && <label className="block">Fee agreement from matter Documents<select className={input} value={value.agreement_document_id || ''} onChange={event => onChange({ ...value, agreement_document_id: event.target.value, selected_documents: (value.selected_documents || []).filter(item => item.document_id !== event.target.value) })}><option value="">Upload an attorney-reviewed PDF below</option>{documents.filter(doc => doc.content_type === 'application/pdf' || doc.filename?.toLowerCase().endsWith('.pdf')).map(doc => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}</select></label>}
    <label className="block">Reviewed fee agreement PDF<input className={input} type="file" accept=".pdf,application/pdf" onChange={e => onFile(e.target.files?.[0] || null)} /></label>
    <label className="block">Fee agreement due from client<input className={input} type="date" value={value.agreement_due || ''} onChange={e => field('agreement_due', e.target.value)} /></label>
    <p className="text-sm">Use the final fee agreement populated and reviewed by the attorney in matter Documents. The client will acknowledge it using the portal signature flow.</p>
    <label className="block">Client email<input className={input} type="email" value={value.email || clientEmail} onChange={e => field('email', e.target.value)} /></label>
    <div className="flex gap-4">{['email', 'sms'].map(channel => <label key={channel}><input type="checkbox" checked={value.channels.includes(channel)} onChange={e => field('channels', e.target.checked ? [...value.channels, channel] : value.channels.filter(c => c !== channel))} /> {channel === 'email' ? 'Email invitation and reminders' : 'SMS invitation and reminders'}</label>)}</div>
    {value.channels.includes('sms') && <><label className="block text-sm"><input type="checkbox" checked={value.sms_permission_verified} onChange={e => field('sms_permission_verified', e.target.checked)} /> I verified this client’s mobile number and recorded permission for intake texts. Existing opt-outs remain in effect.</label>
    <label className="block text-sm"><input type="checkbox" checked={value.sms_case_updates_verified === true} onChange={e => field('sms_case_updates_verified', e.target.checked)} /> The client also agreed to texts about this case after onboarding. Without this, texting stops when intake does.</label></>}
    <label className="block">Client timezone<input className={input} value={value.timezone} onChange={e => field('timezone', e.target.value)} placeholder="America/Chicago" /></label>
    {users.length > 0 && <label className="block">Responsible staff<select className={input} value={value.owner_id} onChange={e => field('owner_id', e.target.value)}><option value="">Assign to me</option>{users.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}</select></label>}
    <label className="block"><input type="checkbox" checked={value.include_questionnaire !== false} onChange={event => field('include_questionnaire', event.target.checked)} /> Include client questionnaire</label>
    {documents.length > 0 && <fieldset><legend>Additional forms from matter Documents</legend>{documents.filter(doc => doc.id !== value.agreement_document_id).map(doc => { const chosen = (value.selected_documents || []).find(item => item.document_id === doc.id); return <div key={doc.id} className="my-2"><label><input type="checkbox" checked={Boolean(chosen)} onChange={event => field('selected_documents', event.target.checked ? [...(value.selected_documents || []), { document_id: doc.id, label: doc.filename, requires_signature: doc.content_type === 'application/pdf' }] : value.selected_documents.filter(item => item.document_id !== doc.id))} /> {doc.filename}</label>{chosen && <label className="ml-3 text-sm"><input type="checkbox" checked={chosen.requires_signature} onChange={event => field('selected_documents', value.selected_documents.map(item => item.document_id === doc.id ? { ...item, requires_signature: event.target.checked } : item))} /> Track signature (PDF)</label>}</div> })}</fieldset>}
    <div className="space-y-1"><button type="button" className="underline" onClick={loadStarterPack}>Use the standard questions for this matter type</button>
      {packNote && <p role="status" className="text-sm">{packNote}</p>}</div>
    <label className="block">Requested uploads due from client<input className={input} type="date" value={value.uploads_due || ''} onChange={e => field('uploads_due', e.target.value)} /></label>
    <label className="block">Requested client uploads — one per line<textarea className={input} rows={3} value={value.upload_requirements || ''} onChange={event => field('upload_requirements', event.target.value)} placeholder={'The client intake form\nRecent statements'} /></label>
    {value.include_questionnaire !== false && <>
      <label className="block">Questionnaire due from client<input className={input} type="date" value={value.questionnaire_due || ''} onChange={e => field('questionnaire_due', e.target.value)} /></label>
      <label className="block">Questionnaire — one required question per line<textarea className={input} rows={5} value={value.questions} onChange={e => field('questions', e.target.value)} /></label>
    </>}
    <p className="text-sm">A due date creates an assigned follow-up task, due at 5pm in the client&rsquo;s timezone. Fee agreement signing triggers portal delivery and a staff follow-up due within 24 hours. Outstanding paperwork is followed up after 7 days. Completing required paperwork creates the separate meeting-scheduling task. SMS uses recorded permission and quiet hours; email uses the connected Microsoft or Google mailbox when available.</p>
  </fieldset>
}
