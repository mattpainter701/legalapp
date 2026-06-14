import React from 'react'
import { FileText, CheckCircle2, Loader2, Trash2 } from 'lucide-react'

export default function DocumentItem({ doc, onDelete }) {
  const isIndexed = doc.status === 'indexed'
  const isProcessing = doc.status === 'processing' || doc.status === 'uploading'

  return (
    <div className="flex items-center gap-3 px-2 py-2 text-sm group hover:bg-brand-line/40 transition-colors">
      <FileText className="w-4 h-4 text-brand-muted shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="truncate text-brand-ink font-mono text-xs" title={doc.filename}>
          {doc.filename}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          {isIndexed ? (
            <span className="flex items-center gap-1 text-[8px] uppercase tracking-widest text-brand-accent font-bold">
              <CheckCircle2 className="w-3 h-3" /> Indexed
            </span>
          ) : isProcessing ? (
            <span className="flex items-center gap-1 text-[8px] uppercase tracking-widest text-brand-amber font-bold">
              <Loader2 className="w-3 h-3 animate-spin" /> Processing
            </span>
          ) : (
            <span className="flex items-center gap-1 text-[8px] uppercase tracking-widest text-brand-rose font-bold">
              {doc.status}
            </span>
          )}
        </div>
      </div>
      <button
        onClick={() => onDelete(doc.id)}
        className="shrink-0 p-1 text-brand-muted hover:bg-brand-rose/10 hover:text-brand-rose transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
        title="Delete document"
      >
        <Trash2 size={13} />
      </button>
    </div>
  )
}
