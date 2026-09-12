import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, Clock, Send } from 'lucide-react'
import { getAdminUsers, getContacts, getMatterDocuments, getMatterPaperwork, matterPaperworkAction } from '../../api'
import PaperworkDrawer from './PaperworkDrawer'
import {
  deliveryRows, dueTone, formatDue, orderedRequirements,
  packetProgress, requirementLabel, requirementState,
} from './paperwork'

const TONE = {
  done: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  review: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  waiting: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}

const DUE_TONE = {
  overdue: 'text-brand-rose font-semibold',
  soon: 'text-brand-amber font-semibold',
  upcoming: 'text-brand-muted',
}

const errorText = caught => (typeof caught?.response?.data?.detail === 'string'
  ? caught.response.data.detail
  : 'Could not update the paperwork. Check the fields and try again.')

function Shell({ children }) {
  return <section aria-label="Client paperwork" className="rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-sm">{children}</section>
}

export default function CaseSetupCard({ matterId, matter, onPacketChange }) {
  const [documents, setDocuments] = useState([])
  const [users, setUsers] = useState([])
  const [clientEmail, setClientEmail] = useState('')
  const [packet, setPacket] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [retryKey, setRetryKey] = useState('')
  const [receipt, setReceipt] = useState({ requirement: 'fee_agreement', document_id: '', note: '' })
  const [meeting, setMeeting] = useState({ kind: 'conference_call', starts_at: '', details: '' })

  const timeZone = packet?.timezone || matter?.client_timezone || 'America/Chicago'

  const load = useCallback(async () => {
    try {
      // A matter with no packet answers 404, but treat any response that is
      // not a recognizable packet as "nothing sent yet" rather than rendering
      // a half-empty strip.
      const value = await getMatterPaperwork(matterId)
      const next = value && typeof value.status === 'string' ? value : null
      setPacket(next)
      // The page watches for the signed transition to alert and refresh the
      // signature panel; the card owns the poll, so it reports every state.
      onPacketChange?.(next)
      setError('')
    } catch (caught) {
      if (caught.response?.status === 404) {
        setPacket(null)
        onPacketChange?.(null)
      } else setError(errorText(caught))
    } finally {
      setLoading(false)
    }
  }, [matterId, onPacketChange])

  useEffect(() => {
    setPacket(null)
    setLoading(true)
    load()
    // A packet changes when the client acts, not when the firm does, so the
    // strip refreshes on its own rather than waiting for a reload.
    const timer = setInterval(load, 30000)
    return () => clearInterval(timer)
  }, [load])

  // Matter documents back both the drawer's pickers and the verification of
  // paperwork that arrived outside the portal, so they load with the card
  // itself rather than only when the drawer opens. The poll above does not
  // refetch them.
  useEffect(() => {
    let active = true
    getMatterDocuments(matterId, { limit: 200 })
      .then(value => { if (active) setDocuments(Array.isArray(value) ? value : value?.items || []) })
      .catch(() => {})
    return () => { active = false }
  }, [matterId])

  // Staff and the client's email are only needed to compose a packet.
  async function openDrawer() {
    setDrawerOpen(true)
    try {
      const value = await getAdminUsers()
      setUsers(Array.isArray(value) ? value : value?.users || [])
    } catch { /* The drawer assigns the packet to the signed-in user. */ }
    if (!clientEmail && matter?.client_contact_id) {
      try {
        const contacts = await getContacts({ limit: 200, active_only: true })
        const list = Array.isArray(contacts) ? contacts : contacts?.items || []
        setClientEmail(list.find(contact => contact.id === matter.client_contact_id)?.email || '')
      } catch { /* The firm can type the client's email in the drawer. */ }
    }
  }

  async function act(action, body) {
    setBusy(true)
    setError('')
    try {
      setPacket(await matterPaperworkAction(matterId, action, body))
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Shell><p role="status" className="text-[13px] text-brand-muted">Loading client paperwork…</p></Shell>

  if (!packet || packet.status === 'cancelled') {
    return (
      <>
        <Shell>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="min-w-0">
              <h2 className="font-serif text-lg font-bold text-brand-ink">
                {packet ? 'Client paperwork cancelled' : 'Start this case'}
              </h2>
              <p className="mt-0.5 text-[13px] text-brand-muted">
                {packet
                  ? 'Follow-ups were cancelled for this matter. Send a new packet to restart the engagement.'
                  : 'Send the fee agreement, intake form, questionnaire, and any other forms in one message, then track each signature here.'}
              </p>
            </div>
            <button
              type="button"
              onClick={openDrawer}
              className="flex min-h-11 items-center gap-2 rounded-xl bg-brand-ink px-5 text-[13px] font-semibold text-white shadow-sm transition-all hover:-translate-y-[1px] hover:bg-brand-ink-2"
            >
              <Send size={15} /> Send client paperwork
            </button>
          </div>
          {error && <p role="alert" className="mt-3 text-[13px] text-brand-rose">{error}</p>}
        </Shell>
        {drawerOpen && (
          <PaperworkDrawer
            matterId={matterId}
            documents={documents}
            users={users}
            clientEmail={clientEmail}
            timeZone={timeZone}
            matterType={matter?.matter_type}
            practiceArea={matter?.practice_area}
            onClose={() => setDrawerOpen(false)}
            onSent={setPacket}
          />
        )}
      </>
    )
  }

  const rows = orderedRequirements(packet)
  const progress = packetProgress(packet)
  const attention = deliveryRows(packet).filter(row => row.needsAttention)
  const receivable = packet.status === 'awaiting_documents'
    ? rows.filter(row => !row.requirement.completed)
    : []
  const submitted = receivable.filter(row => row.requirement.submitted_document_id).length

  return (
    <Shell>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-lg font-bold text-brand-ink">Client paperwork</h2>
          <p className="mt-0.5 text-[13px] text-brand-muted">
            {progress.done} of {progress.total} complete · sent {packet.sent_at ? new Date(packet.sent_at).toLocaleDateString() : 'not yet'}
          </p>
        </div>
        <span className="rounded-lg border border-brand-line bg-brand-bg-soft px-2.5 py-1 text-[12px] font-semibold text-brand-ink-2">
          {packet.status.replaceAll('_', ' ')}
        </span>
      </div>

      <ul className="mt-4 divide-y divide-brand-line/70 border-y border-brand-line/70">
        {rows.map(({ key, requirement }) => {
          const state = requirementState(requirement, key)
          const tone = dueTone(requirement.due_at, requirement.completed)
          return (
            <li key={key} className="flex flex-wrap items-center gap-3 py-3">
              <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-brand-ink">
                {requirementLabel(key, requirement)}
              </span>
              {requirement.due_at && (
                <span className={`flex items-center gap-1 text-[12px] ${DUE_TONE[tone] || 'text-brand-muted'}`}>
                  <Clock size={12} />
                  {tone === 'overdue' ? 'Overdue' : 'Due'} {formatDue(requirement.due_at, timeZone)}
                </span>
              )}
              <span className={`flex items-center gap-1 rounded-lg border px-2.5 py-1 text-[12px] font-semibold ${TONE[state.tone]}`}>
                {state.tone === 'done' && <Check size={12} />}
                {state.label}
              </span>
            </li>
          )
        })}
      </ul>

      {(packet.signing_followup_due_at || packet.scheduling_due_at) && (
        <div className="mt-4 space-y-1 rounded-xl border border-brand-green/20 bg-brand-green/5 px-4 py-3 text-[13px] text-brand-ink">
          {packet.signing_followup_due_at && (
            <p className="font-semibold">
              {packet.requirements?.fee_agreement ? 'Fee agreement signed' : 'Paperwork sent'} — follow up by {new Date(packet.signing_followup_due_at).toLocaleString()}
            </p>
          )}
          {packet.scheduling_due_at && (
            <p>Contact the client to schedule by {new Date(packet.scheduling_due_at).toLocaleString()}</p>
          )}
        </div>
      )}

      {attention.length > 0 && (
        <div className="mt-4 rounded-xl border border-brand-rose/30 bg-brand-rose/5 px-4 py-3">
          <p className="flex items-center gap-2 text-[13px] font-semibold text-brand-rose">
            <AlertTriangle size={14} /> {attention.length} message{attention.length === 1 ? '' : 's'} did not reach the client
          </p>
          <ul className="mt-2 space-y-1 text-[13px] text-brand-ink-2">
            {attention.map(row => (
              <li key={row.key} className="flex flex-wrap items-center gap-2">
                <span>{row.kind} · {row.channel}: {row.state} {row.detail}</span>
                <button type="button" onClick={() => setRetryKey(row.key)} className="font-semibold text-brand-accent underline">Review delivery</button>
              </li>
            ))}
          </ul>
          {retryKey && (
            <div className="mt-3 rounded-lg border border-brand-line bg-brand-surface p-3 text-[13px]">
              <p className="text-brand-ink-2">Check {retryKey} in the provider&apos;s delivery records before retrying. An unknown result may already have reached the client.</p>
              <div className="mt-2 flex flex-wrap gap-3">
                <button
                  type="button"
                  disabled={busy}
                  onClick={async () => { await act('retry', { delivery_key: retryKey, confirm_not_sent: true }); setRetryKey('') }}
                  className="font-semibold text-brand-accent underline"
                >
                  I verified it was not sent — retry
                </button>
                <button type="button" onClick={() => setRetryKey('')} className="text-brand-muted">Close</button>
              </div>
            </div>
          )}
        </div>
      )}

      {receivable.length > 0 && (
        <details className="mt-4 rounded-xl border border-brand-line px-4 py-3 text-[13px]">
          <summary className="cursor-pointer font-semibold text-brand-ink">
            Review received documents{submitted > 0 ? ` (${submitted} awaiting review)` : ''}
          </summary>
          <div className="mt-3 space-y-3">
            <label className="block">
              <span className="text-[12px] font-semibold uppercase tracking-wider text-brand-muted">Requirement</span>
              <select className="mt-1 block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2" value={receipt.requirement} onChange={event => setReceipt({ ...receipt, requirement: event.target.value })}>
                {receivable.map(({ key, requirement }) => <option key={key} value={key}>{requirementLabel(key, requirement)}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="text-[12px] font-semibold uppercase tracking-wider text-brand-muted">Received document</span>
              <select className="mt-1 block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2" value={receipt.document_id} onChange={event => setReceipt({ ...receipt, document_id: event.target.value })}>
                <option value="">Choose an uploaded matter document</option>
                {documents.map(document => <option key={document.id} value={document.id}>{document.filename}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="text-[12px] font-semibold uppercase tracking-wider text-brand-muted">Verification note</span>
              <input className="mt-1 block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2" value={receipt.note} onChange={event => setReceipt({ ...receipt, note: event.target.value })} />
            </label>
            <button
              type="button"
              disabled={busy || !receipt.requirement || !receipt.document_id || !receipt.note}
              onClick={() => act('receipt', receipt)}
              className="min-h-11 rounded-lg bg-brand-ink px-4 text-[13px] font-semibold text-white disabled:opacity-50"
            >
              Confirm document is complete
            </button>
          </div>
        </details>
      )}

      {packet.status === 'documents_complete' && !packet.meeting && (
        <fieldset className="mt-4 space-y-3 rounded-xl border border-brand-line px-4 py-3 text-[13px]">
          <legend className="px-1 font-semibold text-brand-ink">Record the initial meeting</legend>
          <label className="block">
            <span className="text-[12px] font-semibold uppercase tracking-wider text-brand-muted">Meeting type</span>
            <select className="mt-1 block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2" value={meeting.kind} onChange={event => setMeeting({ ...meeting, kind: event.target.value })}>
              <option value="conference_call">Conference call</option>
              <option value="in_person">In-person meeting</option>
            </select>
          </label>
          <label className="block">
            <span className="text-[12px] font-semibold uppercase tracking-wider text-brand-muted">Meeting date and time</span>
            <input type="datetime-local" className="mt-1 block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2" value={meeting.starts_at} onChange={event => setMeeting({ ...meeting, starts_at: event.target.value })} />
          </label>
          <label className="block">
            <span className="text-[12px] font-semibold uppercase tracking-wider text-brand-muted">Call details or office location</span>
            <textarea className="mt-1 block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2" value={meeting.details} onChange={event => setMeeting({ ...meeting, details: event.target.value })} />
          </label>
          <button
            type="button"
            disabled={busy || !meeting.starts_at || !meeting.details}
            onClick={() => act('meeting', { ...meeting, starts_at: new Date(meeting.starts_at).toISOString() })}
            className="min-h-11 rounded-lg bg-brand-ink px-4 text-[13px] font-semibold text-white disabled:opacity-50"
          >
            Save meeting &amp; notify client
          </button>
        </fieldset>
      )}

      {packet.meeting && (
        <p className="mt-4 text-[13px] text-brand-ink-2">
          {packet.meeting.kind === 'in_person' ? 'In-person meeting' : 'Conference call'}: {new Date(packet.meeting.starts_at).toLocaleString()} — {packet.meeting.details}
        </p>
      )}

      {Object.keys(packet.answers || {}).length > 0 && (
        <details className="mt-4 text-[13px]">
          <summary className="cursor-pointer font-semibold text-brand-ink">View completed questionnaire</summary>
          {packet.questions.map(question => (
            <div key={question.key} className="py-2">
              <strong className="text-brand-ink">{question.label}</strong>
              <p className="whitespace-pre-wrap text-brand-ink-2">{packet.answers[question.key]}</p>
            </div>
          ))}
        </details>
      )}

      <details className="mt-4 text-[13px]">
        <summary className="cursor-pointer text-brand-muted">Paperwork tools</summary>
        <div className="mt-2 flex flex-wrap gap-4">
          <button type="button" disabled={busy} onClick={() => act('renew-invitation', {})} className="font-semibold text-brand-accent underline">
            Send renewed portal invitation
          </button>
          <button type="button" disabled={busy} onClick={() => act('cancel', {})} className="text-brand-rose underline">
            Cancel intake follow-ups
          </button>
          <button type="button" onClick={load} className="text-brand-muted underline">Refresh</button>
        </div>
        <p className="mt-2 text-[12px] text-brand-muted">A renewed invitation replaces the previous invitation and portal sessions.</p>
      </details>

      {error && <p role="alert" className="mt-3 text-[13px] text-brand-rose">{error}</p>}
    </Shell>
  )
}
