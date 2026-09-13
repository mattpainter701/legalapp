import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../api'

const root = '/firm-email-intake'
const button = 'rounded-lg border border-brand-line px-3 py-2 text-sm font-semibold disabled:opacity-50'
const field = 'mt-1 w-full rounded-lg border border-brand-line bg-brand-surface p-2 text-sm'
const errorText = (error) => typeof error?.response?.data?.detail === 'string'
  ? error.response.data.detail : 'Email intake could not be loaded. Please try again.'

export function contactFile(address) {
  return `BEGIN:VCARD\r\nVERSION:3.0\r\nFN:LawHand\r\nN:LawHand;;;;\r\nEMAIL;TYPE=INTERNET:${address}\r\nEND:VCARD\r\n`
}

function AddressActions({ address, onError }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(address)
      setCopied(true)
    } catch { onError('Could not copy. Select the address below and copy it manually.') }
  }
  function save() {
    const url = URL.createObjectURL(new Blob([contactFile(address)], { type: 'text/vcard;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'LawHand.vcf'
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <div className="space-y-2">
    <p className="break-all select-all text-sm">{address}</p>
    <div className="flex flex-wrap gap-2">
      <button className={button} onClick={copy}>{copied ? 'Copied' : 'Copy address'}</button>
      <button className={button} onClick={save}>Save LawHand contact</button>
    </div>
  </div>
}

function ReviewCard({ item, matters, staff, onDone }) {
  const suggestion = item.suggestion || {}
  const todo = suggestion.task || {}
  const [title, setTitle] = useState((todo.title || item.subject).slice(0, 300))
  const [matterId, setMatterId] = useState(suggestion.matters?.length === 1 ? suggestion.matters[0].id : '')
  const [assignee, setAssignee] = useState(todo.assigned_to_user_id || '')
  const [due, setDue] = useState(todo.due_date || '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  async function submit(event) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      const { data } = await api.post(`${root}/queue/${item.id}/accept`, {
        title, matter_id: matterId, assigned_to_user_id: assignee, due_date: due || null,
      })
      onDone(data)
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  async function reject() {
    setBusy(true); setError('')
    try { await api.post(`${root}/queue/${item.id}/reject`); onDone(null) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return <article className="rounded-xl border border-brand-line p-4">
    <h4 className="font-semibold break-words">{item.subject}</h4>
    <p className="text-sm text-brand-muted break-all">Forwarded by {suggestion.sender || item.sender}</p>
    <details className="my-3 text-sm"><summary className="cursor-pointer">Read email preview</summary>
      <p className="mt-2 whitespace-pre-wrap break-words">{item.body_preview || 'No text preview. The original email and attachments will be retained when filed.'}</p>
    </details>
    <form onSubmit={submit} className="space-y-3">
      <label className="block text-sm">To-do<input className={field} value={title} onChange={e => setTitle(e.target.value)} maxLength={300} required /></label>
      <label className="block text-sm">Matter<select className={field} value={matterId} onChange={e => setMatterId(e.target.value)} required>
        <option value="">Choose the matter</option>{matters.map(m => <option value={m.id} key={m.id}>{m.title}</option>)}
      </select></label>
      {!matterId && <p className="text-sm text-brand-muted">We need your help choosing the matter.</p>}
      <label className="block text-sm">Assign to<select className={field} value={assignee} onChange={e => setAssignee(e.target.value)} required>
        <option value="">Choose a colleague</option>{staff.map(u => <option value={u.id} key={u.id}>{u.name} ({u.email})</option>)}
      </select></label>
      {!assignee && todo.assignee_hint && <p className="text-sm">Confirm who “{todo.assignee_hint}” refers to.</p>}
      <label className="block text-sm">Due date (optional)<input className={field} type="date" value={due} onChange={e => setDue(e.target.value)} /></label>
      <p className="text-xs text-brand-muted">Check the matter, owner and date. This creates an ordinary to-do, not a verified court deadline.</p>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button className={button} disabled={busy || !matterId || !assignee || !title.trim()}>File + create to-do</button>
        <button type="button" className={button} disabled={busy} onClick={() => setRejecting(true)}>Reject</button>
      </div>
    </form>
    {rejecting && <div className="mt-3 text-sm" role="alert">
      <p>Rejecting permanently removes this queued email. It cannot be undone.</p>
      <button className={button} disabled={busy} onClick={reject}>Confirm rejection</button>{' '}
      <button className={button} disabled={busy} onClick={() => setRejecting(false)}>Keep email</button>
    </div>}
  </article>
}

export default function FirmEmailIntake({ admin = false }) {
  const [data, setData] = useState(null)
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [queue, setQueue] = useState(null)
  const [dismissed, setDismissed] = useState(false)
  const [confirmAction, setConfirmAction] = useState(null)
  const [created, setCreated] = useState(null)
  const load = useCallback(async () => {
    try {
      const response = await api.get(root)
      setData(response.data)
      if (response.data.alias) setTimezone(response.data.timezone)
    } catch (e) { setError(errorText(e)) }
  }, [])
  useEffect(() => { load() }, [load])
  async function loadQueue() {
    setError('')
    try { const response = await api.get(`${root}/queue`); setQueue(response.data) }
    catch (e) { setError(errorText(e)) }
  }
  async function configure(action) {
    setBusy(true); setError('')
    try {
      const response = await api.post(root, { action, timezone })
      setData(response.data); setConfirmAction(null)
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  async function done(result) { setCreated(result); await load(); await loadQueue() }
  if (!admin && !data?.alias && !data?.pending_count && !error) return null
  return <section aria-label="Email to-dos" className="mb-6 rounded-xl border border-brand-line bg-brand-surface p-4 md:p-5 space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">{admin ? 'Firm email intake' : 'Email to-dos'}</h3>
      <button className={button} onClick={() => { load(); loadQueue() }}>Needs review ({data?.pending_count || 0})</button>
    </div>
    {error && <div role="alert" className="text-sm text-red-700">{error} <button className={button} onClick={() => { setError(''); load() }}>Retry</button></div>}
    {!data && !error && <p role="status">Loading email intake…</p>}
    {(admin || !dismissed) && data?.alias && <div className="space-y-3">
      <p className="text-sm">Turn an email into a to-do. Forward it to your LawHand contact with <strong>[TASK]</strong> at the start of the subject.</p>
      <p className="text-sm">Example: <code>[TASK] Jane, review this tomorrow</code></p>
      <p className="text-sm text-brand-muted">You or a colleague then confirms the matter, owner and date in Needs review.</p>
      <AddressActions address={data.alias.address} onError={setError} />
      {!admin && <button className="text-sm underline" onClick={() => setDismissed(true)}>Dismiss tip</button>}
    </div>}
    {!admin && dismissed && <button className="text-sm underline" onClick={() => setDismissed(false)}>Show forwarding tip</button>}
    {admin && data && <div className="space-y-3">
      {!data.enabled && <p>Email intake is not configured on this deployment. Ask the platform administrator to enable delivery.</p>}
      {!data.alias && data.enabled && <p>Create one forwarding address for your firm, then save it as “LawHand” on staff phones.</p>}
      <label className="block text-sm">Firm time zone<input className={field} value={timezone} onChange={e => setTimezone(e.target.value)} placeholder="America/Chicago" /></label>
      <p className="text-xs text-brand-muted">Used for “tomorrow” and “in two weeks,” based on when LawHand receives the email.</p>
      <div className="flex flex-wrap gap-2">
        {data.alias ? <>
          <button className={button} disabled={busy} onClick={() => configure('settings')}>Save time zone</button>
          <button className={button} disabled={busy || !data.enabled} onClick={() => setConfirmAction('rotate')}>Replace address</button>
          <button className={button} disabled={busy} onClick={() => setConfirmAction('disable')}>Disable address</button>
        </> : <button className={button} disabled={busy || !data.enabled} onClick={() => configure('enable')}>Enable firm address</button>}
      </div>
      {confirmAction && <div role="alert" className="space-y-2 text-sm">
        <p>The current address will stop receiving email. Update saved contacts after replacing it. Emails already awaiting review remain available.</p>
        <button className={button} disabled={busy} onClick={() => configure(confirmAction)}>Confirm {confirmAction === 'rotate' ? 'replacement' : 'disable'}</button>{' '}
        <button className={button} disabled={busy} onClick={() => setConfirmAction(null)}>Cancel</button>
      </div>}
      <details className="text-sm"><summary>Authorized staff senders</summary>
        <p className="my-2">Use the registered staff addresses below. Domain-wide access is not granted. The sending provider must sign the email with DKIM for that address’s domain.</p>
        <ul className="list-disc pl-5">{data.staff.map(u => <li className="break-all" key={u.id}>{u.name} — {u.email}</li>)}</ul>
      </details>
    </div>}
    {created && <p role="status" className="text-sm">To-do created. <Link className="underline" to={`/tasks/${created.task_id}`}>Open to-do</Link></p>}
    {queue && <div className="space-y-3">
      <div className="flex justify-between"><h4 className="font-semibold">Needs review</h4><button className="text-sm underline" onClick={() => setQueue(null)}>Close queue</button></div>
      {!queue.items.length && <p className="text-sm">No emails awaiting review.</p>}
      {queue.items.map(item => <ReviewCard key={item.id} item={item} matters={queue.matters} staff={data?.staff || []} onDone={done} />)}
      {queue.items.length === 50 && <p className="text-sm">Showing the oldest 50 requests. More appear as these are reviewed.</p>}
    </div>}
  </section>
}
