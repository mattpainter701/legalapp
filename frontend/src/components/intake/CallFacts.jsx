import React from 'react'
import { ExternalLink } from 'lucide-react'

function durationLabel(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value <= 0) return null
  const m = Math.floor(value / 60)
  const s = value % 60
  return m ? `${m}m ${s}s` : `${s}s`
}

export default function CallFacts({ caller }) {
  if (!caller) return null
  const fields = [
    ['Called', caller.occurred_at ? new Date(caller.occurred_at).toLocaleString() : 'Unknown'],
    ['Phone', caller.phone || 'Not captured'],
    ['Status', caller.result || '—'],
    ['Answered by', caller.answered_by || '—'],
    ['Duration', durationLabel(caller.duration_seconds) || '—'],
    ['Source', caller.source === 'zoom_phone' ? 'Zoom Phone' : 'Manual'],
  ]
  return (
    <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-brand-muted">Selected call</p>
          <h3 className="mt-1 font-serif text-lg font-bold text-brand-ink">{caller.caller_name}</h3>
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
    </section>
  )
}
