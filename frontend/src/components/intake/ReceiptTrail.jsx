import React, { useMemo, useState } from 'react'
import { RefreshCcw } from 'lucide-react'

function ReceiptIcon({ status }) {
  if (status === 'ok') return <span className="inline-block h-1.5 w-1.5 rounded-full bg-brand-green" />
  if (status === 'failed') return <span className="inline-block h-1.5 w-1.5 rounded-full bg-brand-rose" />
  return <span className="inline-block h-1.5 w-1.5 rounded-full bg-brand-muted" />
}

function ReceiptLine({ receipt, onRetry }) {
  const canRetry = receipt.status === 'failed' && Boolean(receipt.retry?.method) && Boolean(receipt.retry?.url)
  return (
    <div className="flex items-start gap-2 rounded-lg border border-brand-line bg-brand-bg-soft px-3 py-2 text-xs">
      <ReceiptIcon status={receipt.status} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className="truncate font-bold text-brand-ink">{receipt.label || 'Action'}</p>
          <span className="text-[10px] text-brand-muted">{receipt.at ? new Date(receipt.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''}</span>
        </div>
        {receipt.error && <p className="mt-1 text-brand-rose">{receipt.error}</p>}
      </div>
      {canRetry && (
        <button
          type="button"
          onClick={() => onRetry(receipt.id)}
          className="rounded-md border border-brand-line px-2 py-1 text-[10px] font-bold text-brand-rose"
        >
          <span className="inline-flex items-center gap-1">
            <RefreshCcw size={11} /> Retry
          </span>
        </button>
      )}
    </div>
  )
}

export default function ReceiptTrail({ receipts = [], onRetry, limit = 3 }) {
  const [expanded, setExpanded] = useState(false)
  const visibleCount = expanded ? receipts.length : Math.min(limit, receipts.length)
  const visible = useMemo(() => [...receipts].slice(0, visibleCount), [receipts, visibleCount])
  return (
    <div className="space-y-2">
      {visible.map((receipt) => (
        <ReceiptLine key={receipt.id} receipt={receipt} onRetry={onRetry} />
      ))}
      {receipts.length > limit && (
        <button
          type="button"
          onClick={() => setExpanded((next) => !next)}
          className="text-[11px] font-bold uppercase text-brand-ink/80"
        >
          {expanded ? 'Show fewer' : `Show ${receipts.length - limit} more`}
        </button>
      )}
      {receipts.length === 0 && (
        <p className="text-xs text-brand-muted">No action receipts yet.</p>
      )}
    </div>
  )
}
