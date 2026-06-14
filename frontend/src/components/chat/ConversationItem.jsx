import React, { useState } from 'react'
import { Trash2, Pin } from 'lucide-react'

export default function ConversationItem({
  conv,
  index,
  isActive,
  isPinned,
  onClick,
  onDelete,
  onTogglePin,
}) {
  const [hover, setHover] = useState(false)

  return (
    <div
      role="button"
      tabIndex={0}
      className={`w-full text-left px-4 py-2 text-sm flex items-start gap-3 border-l-2 transition-colors cursor-pointer group focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-ink ${
        isActive
          ? 'border-brand-accent bg-brand-bg text-brand-ink font-medium'
          : 'border-transparent text-brand-muted hover:bg-brand-line/40 hover:text-brand-ink'
      }`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
    >
      <span className="shrink-0 pt-[3px] flex items-center justify-center w-[18px]">
        {isPinned
          ? <Pin size={11} className="text-brand-accent" fill="currentColor" />
          : <span className="font-mono text-[10px] text-brand-muted">{String(index + 1).padStart(2, '0')}</span>
        }
      </span>
      <span className="flex-1 truncate leading-tight" title={conv.title || 'Untitled conversation'}>
        {conv.title || 'Untitled conversation'}
      </span>
      {(hover || isActive) && (
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={(e) => {
              e.stopPropagation()
              onTogglePin?.(conv.id)
            }}
            className={`p-1 transition-colors ${
              isPinned
                ? 'text-brand-accent hover:bg-brand-accent/10'
                : 'text-brand-muted hover:bg-brand-accent/10 hover:text-brand-accent'
            }`}
            title={isPinned ? 'Unpin conversation' : 'Pin conversation'}
          >
            <Pin size={13} fill={isPinned ? 'currentColor' : 'none'} />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              onDelete(conv.id)
            }}
            className="p-1 text-brand-muted hover:bg-brand-rose/10 hover:text-brand-rose transition-colors"
            title="Delete conversation"
          >
            <Trash2 size={13} />
          </button>
        </div>
      )}
    </div>
  )
}
