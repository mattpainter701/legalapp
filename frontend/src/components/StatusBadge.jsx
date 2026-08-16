
/**
 * Shared status badge for entities (estate, matter, mediation, contact).
 * Styles are keyed by lowercase status string.
 */
const STATUS_STYLES = {
  active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  in_probate: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  draft: 'bg-blue-50 text-blue-700 border-blue-200',
  closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  open: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  pending: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  resolved: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  archived: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}

export default function StatusBadge({ status }) {
  const cfg = STATUS_STYLES[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-[12px] font-sans font-semibold capitalize border ${cfg}`}>
      {(status || '—').replace(/_/g, ' ')}
    </span>
  )
}
