import { Check, Download, ExternalLink, FileCheck2, ShieldCheck, X } from 'lucide-react'

const previewBlocks = (preview) => {
  const raw = preview?.blocks ?? preview?.sections ?? preview
  if (typeof raw === 'string') return [{ id: 'preview-text', text: raw }]
  if (!Array.isArray(raw)) return []
  return raw.map((block, index) => (
    typeof block === 'string'
      ? { id: `preview-${index}`, text: block }
      : {
          id: block?.id || block?.block_id || `preview-${index}`,
          title: block?.title || block?.heading || block?.section || [block?.scope, block?.path].filter(Boolean).join(' · ') || '',
          text: block?.text || block?.content || block?.body || '',
        }
  ))
}

export default function RenderedRevisionPreview({
  revision,
  artifactUrl,
  reviewed,
  onReviewedChange,
  onApprove,
  onReject,
  approving = false,
  rejecting = false,
}) {
  const output = revision?.output_document || {}
  const outputFilename = output.filename || revision?.output_filename || 'Revised document.docx'
  const outputSha = output.sha256 || revision?.output_sha256 || ''
  const blocks = previewBlocks(revision?.output_text_preview || revision?.output_text_blocks)
  const ready = revision?.status === 'ready_for_review'
  const approved = revision?.status === 'approved'
  const rejected = revision?.status === 'rejected'
  const superseded = revision?.status === 'superseded'
  const busy = approving || rejecting

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-5" aria-labelledby="artifact-review-title">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-accent/10 text-brand-accent-2">
              <FileCheck2 size={19} aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">Exact artifact</p>
              <h2 id="artifact-review-title" className="mt-1 truncate font-serif text-xl font-bold text-brand-ink">{outputFilename}</h2>
              {outputSha && <p className="mt-1 font-mono text-[10px] text-brand-muted" title={outputSha}>SHA-256 {outputSha.slice(0, 16)}…</p>}
            </div>
          </div>
          <a
            href={artifactUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-brand-ink px-4 text-sm font-bold text-white hover:bg-brand-ink-2"
          >
            <ExternalLink size={16} aria-hidden="true" /> Open exact DOCX
          </a>
        </div>
      </section>

      <section className="rounded-2xl border border-amber-200 bg-amber-50 p-4" role="note">
        <div className="flex items-start gap-2.5">
          <Download size={17} className="mt-0.5 shrink-0 text-amber-700" aria-hidden="true" />
          <div>
            <p className="text-sm font-bold text-amber-950">Content preview — not page-faithful</p>
            <p className="mt-1 text-xs leading-relaxed text-amber-900">
              The text below helps with phone review, but it does not prove Word layout, pagination, numbering, headers, footers, tables, or signatures. Open the exact DOCX before approval.
            </p>
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-brand-line bg-brand-surface shadow-sm" aria-labelledby="content-preview-title">
        <header className="border-b border-brand-line px-4 py-3">
          <h2 id="content-preview-title" className="font-serif text-lg font-bold text-brand-ink">Content preview</h2>
        </header>
        {blocks.length > 0 ? (
          <div className="max-h-[58vh] space-y-5 overflow-y-auto p-4 sm:p-6">
            {blocks.map((block) => (
              <div key={block.id}>
                {block.title && <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-brand-muted">{block.title}</h3>}
                <p className="whitespace-pre-wrap font-serif text-[15px] leading-7 text-brand-ink">{block.text}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="p-6 text-sm text-brand-muted">No text preview is available. Review the exact DOCX artifact before deciding.</p>
        )}
      </section>

      {(ready || approved || rejected || superseded) && (
        <section className={`rounded-2xl border p-4 shadow-sm sm:p-5 ${approved ? 'border-brand-green/30 bg-brand-green/5' : rejected ? 'border-brand-rose/30 bg-brand-rose/5' : superseded ? 'border-amber-200 bg-amber-50' : 'border-brand-line bg-brand-surface'}`} aria-labelledby="revision-decision-title">
          <div className="flex items-start gap-3">
            <ShieldCheck size={19} className={approved ? 'text-brand-green' : rejected ? 'text-brand-rose' : superseded ? 'text-amber-700' : 'text-brand-accent-2'} aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <h2 id="revision-decision-title" className="text-sm font-bold text-brand-ink">
                {approved ? 'Revision approved' : rejected ? 'Revision rejected' : superseded ? 'Revision superseded' : 'Revision decision'}
              </h2>
              {approved ? (
                <p className="mt-1 text-xs leading-relaxed text-brand-green">The reviewed output hash is approved. Signature preparation remains a separate action.</p>
              ) : rejected ? (
                <p className="mt-1 text-xs leading-relaxed text-brand-rose">This revision will not be used. The source document remains unchanged.</p>
              ) : superseded ? (
                <p className="mt-1 text-xs leading-relaxed text-amber-900">A newer revision now continues this document lineage. This candidate is read-only and can no longer be approved.</p>
              ) : (
                <>
                  <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-xl border border-brand-line bg-brand-bg-soft/50 p-3 text-sm text-brand-ink">
                    <input
                      type="checkbox"
                      checked={reviewed}
                      onChange={(event) => onReviewedChange(event.target.checked)}
                      className="mt-0.5 h-4 w-4 rounded border-brand-line text-brand-ink focus:ring-brand-accent"
                    />
                    <span>I reviewed the exact DOCX artifact and the listed before-and-after changes.</span>
                  </label>
                  <p className="mt-2 text-[11px] leading-relaxed text-brand-muted">Approval is bound to the current output SHA-256. Any follow-up creates a new revision requiring another review.</p>
                </>
              )}
            </div>
          </div>

          {ready && (
            <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={onReject}
                disabled={busy}
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-brand-rose/30 px-4 text-sm font-bold text-brand-rose hover:bg-brand-rose/5 disabled:opacity-50"
              >
                <X size={16} aria-hidden="true" /> {rejecting ? 'Rejecting…' : 'Reject revision'}
              </button>
              <button
                type="button"
                onClick={onApprove}
                disabled={!reviewed || !outputSha || busy}
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-brand-ink px-5 text-sm font-bold text-white hover:bg-brand-ink-2 disabled:cursor-not-allowed disabled:bg-brand-line-2 disabled:text-brand-muted"
              >
                {approving ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" aria-hidden="true" /> : <Check size={16} aria-hidden="true" />}
                {approving ? 'Approving…' : 'Approve reviewed revision'}
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
