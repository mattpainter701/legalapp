import React from 'react'
import { FileText, CheckCircle2, Loader2, Trash2 } from 'lucide-react'

export default function DocumentItem({ doc, onDelete }) {
  const isIndexed = doc.status === 'indexed'
  const isProcessing = doc.status === 'processing' || doc.status === 'uploading'
  const filename = doc.filename || 'Untitled document'

  return (
    <div className="group flex min-h-14 items-center gap-3 border-b border-brand-line px-3 py-2 text-sm last:border-0 hover:bg-brand-bg-soft">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-bg-soft text-brand-accent-2">
        <FileText className="h-4 w-4" />
      </span>
      <div className="flex-1 min-w-0">
        <div className="truncate text-xs font-medium text-brand-ink" title={filename}>
          {filename}
        </div>
        <div className="mt-1 flex items-center gap-2">
          {isIndexed ? (
            <span className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest text-brand-accent">
              <CheckCircle2 className="w-3 h-3" /> Indexed
            </span>
          ) : isProcessing ? (
            <span className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest text-brand-amber">
              <Loader2 className="w-3 h-3 animate-spin" /> Processing
            </span>
          ) : (
            <span className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest text-brand-rose">
              {doc.status || 'Unavailable'}
            </span>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onDelete(doc.id)}
        className="tap-target shrink-0 rounded-lg text-brand-muted hover:bg-brand-rose/10 hover:text-brand-rose sm:opacity-0 sm:group-hover:opacity-100 sm:focus:opacity-100"
        aria-label={`Delete ${filename}`}
      >
        <Trash2 size={13} />
      </button>
    </div>
  )
}
