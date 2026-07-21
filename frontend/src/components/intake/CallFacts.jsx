import React from 'react'
import { ExternalLink } from 'lucide-react'

function durationLabel(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value <= 0) return null
  const m = Math.floor(value / 60)
  const s = value % 60
  return m ? `${m}m ${s}s` : `${s}s`
}

function followUpBadge(caller) {
  if (!caller.task_id) return null
  if (caller.task_status === 'completed') {
    return { label: 'Follow-up done', cls: 'bg-emerald-100 text-emerald-700' }
  }
  if (caller.task_customer_contacted_at) {
    return { label: 'Customer contacted', cls: 'bg-blue-100 text-blue-700' }
  }
  if (caller.task_viewed_at) {
    return { label: 'Seen by assignee', cls: 'bg-emerald-50 text-emerald-700' }
  }
  return { label: 'Not seen yet', cls: 'bg-amber-100 text-amber-700' }
}

export default function CallFacts({ caller }) {
  if (!caller) return null
  const badge = followUpBadge(caller)
  const fields = [
    ['Called', caller.occurred_at ? new Date(caller.occurred_at).toLocaleString() : 'Unknown'],
    ['Phone', caller.phone || 'Not captured'],
    ['Status', caller.result || '—'],
    ['Answered by', caller.answered_by || '—'],
    ['Duration', durationLabel(caller.duration_seconds) || '—'],
    ['Source', caller.source === 'zoom_phone' ? 'Zoom Phone' : 'Manual'],
  ]
  if (caller.lead_id) {
    fields.push(['Lead status', caller.lead_status ? caller.lead_status.replace('_', ' ') : '—'])
  }
  if (caller.assigned_to_name) {
    fields.push(['Assigned to', caller.assigned_to_name])
  }
  if (caller.task_id) {
    fields.push(['Task status', (caller.task_status || 'pending').replace('_', ' ')])
    fields.push([
      'Task seen',
      caller.task_viewed_at ? new Date(caller.task_viewed_at).toLocaleString() : 'Not yet',
    ])
    fields.push([
      'Customer contacted',
      caller.task_customer_contacted_at
        ? `${new Date(caller.task_customer_contacted_at).toLocaleString()}${caller.task_customer_contact_method ? ` (${caller.task_customer_contact_method})` : ''}`
        : 'Not yet',
    ])
  }
  return (
    <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-brand-muted">Selected call</p>
          <h3 className="mt-1 font-serif text-lg font-bold text-brand-ink">{caller.caller_name}</h3>
          {badge && (
            <span className={`mt-1 inline-flex rounded-full px-2 py-0.5 text-[11px] font-bold ${badge.cls}`}>
              {badge.label}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          {caller.recording_url && (
            <a href={caller.recording_url} target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-1 rounded-full bg-brand-bg-soft px-3 py-1 text-[11px] font-bold text-brand-accent">
              ▶ Recording <ExternalLink size={11} />
            </a>
          )}
          {caller.transcript_url && (
            <a href={caller.transcript_url} target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-1 rounded-full bg-brand-bg-soft px-3 py-1 text-[11px] font-bold text-brand-accent">
              Transcript <ExternalLink size={11} />
            </a>
          )}
        </div>
      </div>
      <dl className="mt-4 grid gap-3 text-xs md:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt className="font-black uppercase tracking-widest text-brand-muted">{label}</dt>
            <dd className="mt-1 text-brand-ink">{value}</dd>
          </div>
        ))}
      </dl>
      {caller.source === 'zoom_phone' && (
        <div className="mt-5 space-y-3 border-t border-brand-line pt-4">
          <div>
            <dt className="font-black uppercase tracking-widest text-brand-muted">Zoom call summary</dt>
            {caller.call_summary ? (
              <dd className="mt-1 whitespace-pre-wrap text-sm leading-6 text-brand-ink">{caller.call_summary}</dd>
            ) : (
              <dd className="mt-1 text-xs text-brand-muted">
                {caller.has_call_summary
                  ? 'Generated — restricted by your role.'
                  : 'Not generated or not provided by Zoom for this call.'}
              </dd>
            )}
          </div>
          {caller.transcript_text && (
            <details>
              <summary className="cursor-pointer text-xs font-black uppercase tracking-widest text-brand-accent">View transcript</summary>
              <p className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-xl bg-brand-bg-soft p-3 text-xs leading-5 text-brand-ink">{caller.transcript_text}</p>
            </details>
          )}
          {!caller.transcript_text && (
            <div>
              <dt className="font-black uppercase tracking-widest text-brand-muted">Zoom transcript</dt>
              <dd className="mt-1 text-xs text-brand-muted">
                {caller.has_transcript
                  ? (caller.can_view_confidential_call_content ? 'Available from Zoom via the transcript link.' : 'Available — restricted by your role.')
                  : 'Not generated or not provided by Zoom for this call.'}
              </dd>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
