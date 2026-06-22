import React from 'react'
import { Bell, X } from 'lucide-react'

export default function NewCallToasts({ toasts, onView, onDismiss }) {
  if (!toasts || toasts.length === 0) return null
  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div key={t.id} className="flex items-center gap-3 rounded-2xl border border-brand-green/30 bg-white px-4 py-3 shadow-lg">
          <Bell size={16} className="text-brand-green" />
          <div className="min-w-0">
            <p className="truncate text-sm font-bold text-brand-ink">New call — {t.title}</p>
            <p className="text-[11px] text-brand-muted">{t.status}</p>
          </div>
          <button type="button" onClick={() => onView(t.callId)}
                  className="rounded-lg bg-brand-ink px-2.5 py-1 text-[11px] font-bold text-white">
            View
          </button>
          <button type="button" onClick={() => onDismiss(t.id)} className="text-brand-muted hover:text-brand-ink">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}
