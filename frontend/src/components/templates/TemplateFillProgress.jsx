export default function TemplateFillProgress({ progress, requiredMissing = 0, filter, onFilter, onNext }) {
  const remaining = filter === 'remaining' ? progress.remaining.length : filter === 'review' ? progress.review.length : progress.remaining.length + progress.review.length
  return <section aria-label="Document completion" className="rounded-lg border border-brand-line bg-brand-bg p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold">{progress.percent}% complete <span className="font-normal text-brand-muted">· {progress.completed} of {progress.total} fields filled</span></p>
      <button type="button" onClick={onNext} disabled={!remaining} className="rounded border border-brand-line px-3 py-1.5 text-xs font-semibold disabled:opacity-40">Next field needing attention</button>
    </div>
    <progress aria-label="Fields completed" value={progress.completed} max={progress.total || 1} className="mt-2 h-2 w-full accent-brand-accent" />
    <p className="mt-1 text-xs text-brand-muted">{requiredMissing} required missing · {progress.remaining.length - requiredMissing} optional unfilled · {progress.review.length} suggestions to review</p>
    <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Filter fields">
      {[['all', `All fields (${progress.total})`], ['remaining', `Missing (${progress.remaining.length})`], ['review', `Review suggestions (${progress.review.length})`]].map(([value, label]) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => onFilter(value)} className="rounded border border-brand-line px-2 py-1 text-xs aria-pressed:border-brand-accent aria-pressed:bg-brand-accent/10">{label}</button>)}
    </div>
    <details className="mt-2 text-xs text-brand-muted"><summary className="cursor-pointer">How completion is measured</summary><p className="mt-1">Completion counts filled values. Review suggested values and the finished document before using it. Signatures are collected separately.</p></details>
  </section>
}
