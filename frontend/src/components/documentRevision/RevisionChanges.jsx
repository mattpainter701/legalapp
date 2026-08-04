import React from 'react'
import { AlertTriangle, CheckCircle2, FileDiff, Info } from 'lucide-react'

const valueFor = (operation, keys) => {
  for (const key of keys) {
    if (operation?.[key] != null) return String(operation[key])
  }
  return ''
}

export const normalizeRevisionOperation = (operation, index) => ({
  id: operation?.id || operation?.operation_id || `operation-${index}`,
  label: valueFor(operation, ['label', 'summary', 'description', 'rationale', 'section']) || `Change ${index + 1}`,
  section: valueFor(operation, ['section', 'location', 'anchor_label', 'block_id']),
  before: valueFor(operation, ['before', 'before_text', 'old_text', 'source_text', 'target_text']),
  after: valueFor(operation, ['after', 'after_text', 'new_text', 'replacement_text']),
  kind: valueFor(operation, ['kind', 'operation_type', 'type']).replaceAll('_', ' '),
})

export default function RevisionChanges({ revision }) {
  const operations = (revision?.operations || revision?.change_operations || []).map(normalizeRevisionOperation)
  const warnings = [
    ...(Array.isArray(revision?.warnings) ? revision.warnings : []),
    ...(revision?.storage_warning ? [revision.storage_warning] : []),
  ]

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-5" aria-labelledby="change-summary-title">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-green/10 text-brand-green">
            <CheckCircle2 size={18} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">Assistant summary</p>
            <h2 id="change-summary-title" className="mt-1 font-serif text-xl font-bold text-brand-ink">
              {revision?.summary || 'Revision ready for review'}
            </h2>
            <p className="mt-2 text-xs leading-relaxed text-brand-muted">
              Compare every exact before-and-after block, then inspect the DOCX artifact before approval.
            </p>
          </div>
        </div>
      </section>

      {warnings.length > 0 && (
        <section className="rounded-2xl border border-amber-200 bg-amber-50 p-4" aria-labelledby="revision-warnings-title">
          <div className="flex items-center gap-2 text-amber-800">
            <AlertTriangle size={17} aria-hidden="true" />
            <h2 id="revision-warnings-title" className="text-sm font-bold">Review warnings</h2>
          </div>
          <ul className="mt-2 space-y-1.5 text-sm leading-relaxed text-amber-900">
            {warnings.map((warning, index) => (
              <li key={`${String(warning)}-${index}`}>{typeof warning === 'string' ? warning : warning?.message || JSON.stringify(warning)}</li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="exact-changes-title">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <FileDiff size={18} className="text-brand-accent-2" aria-hidden="true" />
            <h2 id="exact-changes-title" className="font-serif text-lg font-bold text-brand-ink">Exact changes</h2>
          </div>
          <span className="rounded-full border border-brand-line bg-brand-bg-soft px-2.5 py-1 text-[11px] font-bold text-brand-muted">
            {operations.length} change{operations.length === 1 ? '' : 's'}
          </span>
        </div>

        {operations.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-brand-line-2 bg-brand-surface p-6 text-center">
            <Info size={22} className="mx-auto text-brand-muted" aria-hidden="true" />
            <p className="mt-2 text-sm font-semibold text-brand-ink">No exact operations were returned.</p>
            <p className="mt-1 text-xs text-brand-muted">Do not approve until the server provides reviewable before-and-after evidence.</p>
          </div>
        ) : (
          <ol className="space-y-3">
            {operations.map((operation, index) => (
              <li key={operation.id} className="overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm">
                <header className="flex flex-wrap items-center justify-between gap-2 border-b border-brand-line bg-brand-bg-soft/60 px-4 py-3">
                  <div>
                    <p className="text-sm font-bold text-brand-ink">{index + 1}. {operation.label}</p>
                    {operation.section && operation.section !== operation.label && (
                      <p className="mt-0.5 text-[11px] text-brand-muted">{operation.section}</p>
                    )}
                  </div>
                  {operation.kind && <span className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">{operation.kind}</span>}
                </header>
                <div className="grid gap-px bg-brand-line sm:grid-cols-2">
                  <div className="bg-red-50 p-4">
                    <p className="text-[10px] font-bold uppercase tracking-[0.13em] text-red-700">Before</p>
                    <del className="mt-2 block whitespace-pre-wrap text-sm leading-relaxed text-red-950 decoration-red-500/70">
                      {operation.before || 'No previous text'}
                    </del>
                  </div>
                  <div className="bg-green-50 p-4">
                    <p className="text-[10px] font-bold uppercase tracking-[0.13em] text-green-700">After</p>
                    <ins className="mt-2 block whitespace-pre-wrap text-sm leading-relaxed text-green-950 no-underline">
                      {operation.after || 'Removed'}
                    </ins>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}
