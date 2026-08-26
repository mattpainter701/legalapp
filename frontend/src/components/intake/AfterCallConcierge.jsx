import { useEffect, useState } from 'react'
import { AlertCircle, ArrowRight, CalendarClock, CheckCircle2, ChevronDown, Loader2, MessageSquareText, Sparkles } from 'lucide-react'
import { approveLeadEngagementPacket, createLeadEngagementPacket, getLeadEngagementPacket, prepareLeadFollowThrough, previewLeadEngagementPacket, searchUsers, updateLeadEngagementPacket, updateLeadFollowThrough } from '../../api'
import FeeAgreementPacket from './FeeAgreementPacket'

const DECISIONS = [{ key: 'pursue', label: 'Pursue' }, { key: 'needs_information', label: 'Needs information' }, { key: 'decline', label: 'Decline' }, { key: 'reassign', label: 'Reassign' }]
const messageFor = (error) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return error?.message || 'The assistant is unavailable right now.'
}

export default function AfterCallConcierge({ lead, communicationId, enabled = false, onLeadUpdated }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [data, setData] = useState(null)
  const [packet, setPacket] = useState(null)
  const [error, setError] = useState(null)
  const [showPacket, setShowPacket] = useState(false)
  const [showReassign, setShowReassign] = useState(false)
  const [attorneyQuery, setAttorneyQuery] = useState('')
  const [attorneys, setAttorneys] = useState([])
  const [nextAction, setNextAction] = useState('')
  const [nextActionDate, setNextActionDate] = useState('')

  useEffect(() => {
    if (!data) return
    setNextAction(data.next_action || '')
    setNextActionDate(data.next_action_date || '')
  }, [data])

  useEffect(() => {
    if (!open || data || !lead?.id) return
    let cancelled = false
    setLoading(true); setError(null)
    const load = async () => {
      try {
        const followThrough = await prepareLeadFollowThrough(lead.id, {
          ...(communicationId ? { communication_id: communicationId } : {}),
        })
        const engagementPacket = await getLeadEngagementPacket(lead.id).catch(() => null)
        if (!cancelled) { setData(followThrough || {}); setPacket(engagementPacket?.packet || engagementPacket) }
      } catch (err) { if (!cancelled) setError(messageFor(err)) }
      finally { if (!cancelled) setLoading(false) }
    }
    load()
    return () => { cancelled = true }
  }, [open, data, lead?.id, communicationId])

  useEffect(() => {
    if (!showReassign || attorneyQuery.trim().length < 2) {
      setAttorneys([])
      return
    }
    let cancelled = false
    searchUsers(attorneyQuery.trim())
      .then(result => { if (!cancelled) setAttorneys(result || []) })
      .catch(() => { if (!cancelled) setAttorneys([]) })
    return () => { cancelled = true }
  }, [attorneyQuery, showReassign])

  if (!enabled) return null
  const update = async (changes) => {
    setSaving(true); setError(null)
    try {
      const result = await updateLeadFollowThrough(lead.id, { ...changes, expected_version: data?.version })
      setData(current => ({ ...current, ...result })); onLeadUpdated?.(result)
      return true
    }
    catch (err) { setError(messageFor(err)); return false } finally { setSaving(false) }
  }
  const savePacket = async form => {
    setSaving(true); setError(null)
    try {
      // PacketUpdate omits the create-only idempotency key while allowing a
      // reviewer to replace the approved template before packet approval.
      const updateForm = {
        fee_structure: form.fee_structure,
        fee_amount: form.fee_amount,
        template_id: form.template_id,
        scope_bullets: form.scope_bullets,
        exclusions: form.exclusions,
        client: form.client,
        attorney: form.attorney,
        signers: form.signers,
      }
      const saved = packet?.id
        ? await updateLeadEngagementPacket(lead.id, { ...updateForm, expected_version: packet.version })
        : await createLeadEngagementPacket(lead.id, form)
      const preview = await previewLeadEngagementPacket(lead.id)
      setPacket(preview?.packet || preview || saved)
      setShowPacket(true)
    }
    catch (err) { setError(messageFor(err)) } finally { setSaving(false) }
  }
  const approvePacket = async () => {
    setSaving(true); setError(null)
    try {
      const result = await approveLeadEngagementPacket(lead.id, { expected_version: packet?.version })
      setPacket(result?.packet || result)
    } catch (err) { setError(messageFor(err)) } finally { setSaving(false) }
  }
  const unavailable = data?.inference_available === false || data?.status === 'unavailable'
  const suggestion = data?.suggestion || data?.assistant || {}
  const decision = data?.decision || lead?.status
  const canPreparePacket = decision === 'pursue' || Boolean(packet)
  return (
    <div className="mt-3 w-full border-t border-brand-line pt-3">
      <button type="button" onClick={() => setOpen(current => !current)} aria-expanded={open} className="flex min-h-[40px] w-full items-center justify-between rounded-lg bg-brand-bg-soft px-3 py-2 text-left hover:bg-brand-accent/5">
        <span className="flex items-center gap-2 text-xs font-bold text-brand-ink"><Sparkles size={15} className="text-brand-accent" aria-hidden="true" />After-call concierge</span><ChevronDown size={15} className={`text-brand-muted transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>
      {open && <div className="mt-3 grid gap-3 lg:grid-cols-2">
        {loading ? <div className="rounded-lg border border-dashed border-brand-line p-4 text-sm text-brand-muted"><Loader2 size={16} className="mr-2 inline animate-spin" aria-hidden="true" />Preparing the handoff…</div> : error && !data ? <div role="alert" className="rounded-lg border border-brand-amber/30 bg-brand-amber/10 p-4 text-sm text-brand-ink"><AlertCircle size={16} className="mr-2 inline text-brand-amber" aria-hidden="true" />{error}<p className="mt-2 text-xs text-brand-muted">Your saved note and assignment are unchanged. Try again later.</p></div> : <>
          <section aria-labelledby={`concierge-${lead.id}`} className="rounded-xl border border-brand-line bg-white p-4">
            <div className="flex items-start gap-2"><MessageSquareText size={17} className="mt-0.5 text-brand-accent" aria-hidden="true" /><div><h3 id={`concierge-${lead.id}`} className="text-sm font-bold text-brand-ink">Attorney-ready handoff</h3><p className="text-xs text-brand-muted">Review the preparation, then choose what happens next.</p></div></div>
            {unavailable ? <div className="mt-4 rounded-lg border border-brand-amber/30 bg-brand-amber/10 p-3 text-xs text-brand-ink"><AlertCircle size={14} className="mr-1 inline text-brand-amber" aria-hidden="true" />Assistant inference is unavailable. You can still set the next action and prepare a packet manually.</div> : <div className="mt-4 space-y-3 text-sm">
              {suggestion.brief && <div><p className="text-[10px] font-bold uppercase tracking-wider text-brand-muted">Brief</p><p className="mt-1 leading-5 text-brand-ink">{suggestion.brief}</p></div>}
              {suggestion.missing_information?.length > 0 && <div><p className="text-[10px] font-bold uppercase tracking-wider text-brand-muted">Missing information</p><ul className="mt-1 list-disc pl-5 text-brand-ink">{suggestion.missing_information.map(item => <li key={item}>{item}</li>)}</ul></div>}
              {suggestion.outreach_draft && <div className="rounded-lg bg-brand-bg-soft p-3"><p className="text-[10px] font-bold uppercase tracking-wider text-brand-muted">Outreach draft</p><p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-brand-ink">{suggestion.outreach_draft}</p></div>}
            </div>}
            <div className="mt-4 border-t border-brand-line pt-3"><p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-brand-muted">Decision</p><div className="flex flex-wrap gap-2">{DECISIONS.map(item => <button key={item.key} type="button" aria-pressed={decision === item.key} onClick={() => { if (item.key === 'reassign') setShowReassign(true); else { setShowReassign(false); update({ decision: item.key }) } }} disabled={saving} className={`min-h-[38px] rounded-lg border px-3 text-xs font-bold ${decision === item.key ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line text-brand-ink hover:border-brand-accent'}`}>{item.label}</button>)}</div></div>
            {showReassign && <div className="mt-3 rounded-lg border border-brand-line bg-brand-bg-soft p-3"><label className="text-[10px] font-bold uppercase tracking-wider text-brand-muted">New responsible attorney<input aria-label="Find attorney for reassignment" value={attorneyQuery} onChange={event => setAttorneyQuery(event.target.value)} placeholder="Type at least 2 characters" className="mt-1 w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm font-normal normal-case tracking-normal" /></label>{attorneys.length > 0 && <div className="mt-2 grid gap-1">{attorneys.map(attorney => <button key={attorney.id} type="button" disabled={saving} onClick={async () => { if (await update({ decision: 'reassign', assigned_attorney_user_id: attorney.id })) { setShowReassign(false); setAttorneyQuery('') } }} className="rounded-lg border border-brand-line bg-white px-3 py-2 text-left text-xs font-semibold text-brand-ink hover:border-brand-accent">{attorney.full_name || attorney.email}</button>)}</div>}</div>}
            <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_150px]"><label className="text-[10px] font-bold uppercase tracking-wider text-brand-muted">Next action<input aria-label="Next action" value={nextAction} onChange={e => setNextAction(e.target.value)} onBlur={e => update({ next_action: e.target.value })} placeholder="e.g. Send consultation options" className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm font-normal normal-case tracking-normal" /></label><label className="text-[10px] font-bold uppercase tracking-wider text-brand-muted"><span className="flex items-center gap-1"><CalendarClock size={12} aria-hidden="true" /> Follow up</span><input type="date" aria-label="Follow up date" value={nextActionDate} onChange={e => setNextActionDate(e.target.value)} onBlur={e => update({ next_action_date: e.target.value })} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm font-normal tracking-normal" /></label></div>
            {error && data && <p role="alert" className="mt-2 text-xs text-brand-rose">{error}</p>}
          </section>
          <div className="space-y-3"><button type="button" onClick={() => canPreparePacket && setShowPacket(current => !current)} disabled={!canPreparePacket} className="flex min-h-[42px] w-full items-center justify-between rounded-xl border border-brand-line bg-white px-4 text-left text-xs font-bold text-brand-ink hover:border-brand-accent disabled:cursor-not-allowed disabled:opacity-50"><span>{canPreparePacket ? 'Prepare a fee agreement packet' : 'Choose Pursue before preparing an agreement'}</span><ArrowRight size={15} aria-hidden="true" /></button>{showPacket && canPreparePacket && <FeeAgreementPacket lead={lead} initialValue={packet} onSave={savePacket} onApprove={approvePacket} saving={saving} />}{packet?.status === 'approved' && <p className="flex items-center gap-1 text-xs text-brand-green"><CheckCircle2 size={14} aria-hidden="true" />Packet approved as a reviewed artifact. Nothing was sent or submitted for signature.</p>}</div>
        </>}
      </div>}
    </div>
  )
}
