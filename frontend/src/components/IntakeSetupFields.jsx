export const defaultIntakeSetup = {
  email: '', channels: ['email'], timezone: 'America/Chicago', owner_id: '', sms_permission_verified: false,
  questions: 'Please describe your legal matter.\nWho are the other people or organizations involved?\nWhat important dates should your legal team know about? Enter none if unknown.',
}

export function intakeOptions(setup, clientEmail = '') {
  return {
    email: setup.email || clientEmail, channels: setup.channels, timezone: setup.timezone,
    owner_id: setup.owner_id || null, sms_permission_verified: setup.sms_permission_verified,
    questions: setup.questions.split('\n').map(s => s.trim()).filter(Boolean).map((label, i) => ({ key: `question_${i + 1}`, label, required: true })),
    confirm_send: true,
  }
}

export default function IntakeSetupFields({ value, onChange, onFile, clientEmail = '', users = [] }) {
  const field = (key, next) => onChange({ ...value, [key]: next })
  const input = 'block w-full border border-brand-line rounded-lg p-2 bg-white text-brand-ink'
  return <fieldset className="space-y-3 border border-brand-line rounded-lg p-3">
    <legend className="font-semibold">Client intake packet</legend>
    <label className="block">Reviewed fee agreement PDF<input className={input} type="file" accept=".pdf,application/pdf" onChange={e => onFile(e.target.files?.[0] || null)} /></label>
    <p className="text-sm">Use the final agreement prepared in Template Studio or upload your reviewed PDF. The client will acknowledge it using the portal signature flow.</p>
    <label className="block">Client email<input className={input} type="email" value={value.email || clientEmail} onChange={e => field('email', e.target.value)} /></label>
    <div className="flex gap-4">{['email', 'sms'].map(channel => <label key={channel}><input type="checkbox" checked={value.channels.includes(channel)} onChange={e => field('channels', e.target.checked ? [...value.channels, channel] : value.channels.filter(c => c !== channel))} /> {channel === 'email' ? 'Email invitation and reminders' : 'SMS invitation and reminders'}</label>)}</div>
    {value.channels.includes('sms') && <label className="block text-sm"><input type="checkbox" checked={value.sms_permission_verified} onChange={e => field('sms_permission_verified', e.target.checked)} /> I verified this client’s mobile number and recorded permission for intake texts. Existing opt-outs remain in effect.</label>}
    <label className="block">Client timezone<input className={input} value={value.timezone} onChange={e => field('timezone', e.target.value)} placeholder="America/Chicago" /></label>
    {users.length > 0 && <label className="block">Responsible staff<select className={input} value={value.owner_id} onChange={e => field('owner_id', e.target.value)}><option value="">Assign to me</option>{users.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}</select></label>}
    <label className="block">Questionnaire — one required question per line<textarea className={input} rows={5} value={value.questions} onChange={e => field('questions', e.target.value)} /></label>
    <p className="text-sm">Documents are followed up after 7 days. Both completed requirements trigger a scheduling task due within 24 hours. SMS uses recorded permission and quiet hours; email uses the connected Microsoft or Google mailbox when available.</p>
  </fieldset>
}
