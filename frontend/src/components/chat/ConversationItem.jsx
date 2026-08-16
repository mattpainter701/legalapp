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
  const title = conv.title || 'Untitled conversation'

  return (
    <div
      className={`group flex w-full items-stretch border-l-2 text-left text-sm transition-colors ${
        isActive
          ? 'border-brand-accent bg-brand-bg text-brand-ink font-medium'
          : 'border-transparent text-brand-muted hover:bg-brand-line/40 hover:text-brand-ink'
      }`}
    >
      <button
        type="button"
        aria-label={title}
        aria-current={isActive ? 'page' : undefined}
        onClick={onClick}
        className="flex min-h-[44px] min-w-0 flex-1 items-start gap-3 px-4 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-ink"
      >
        <span className="flex w-[18px] shrink-0 items-center justify-center pt-[3px]">
          {isPinned
            ? <Pin size={11} className="text-brand-accent" fill="currentColor" />
            : <span className="font-mono text-[10px] text-brand-muted">{String(index + 1).padStart(2, '0')}</span>
          }
        </span>
        <span className="min-w-0 flex-1 truncate leading-tight" title={title}>{title}</span>
      </button>
      <div className="flex shrink-0 items-center gap-1 pr-2">
        <button
          type="button"
          onClick={() => onTogglePin?.(conv.id)}
          className={`inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent ${
            isPinned
              ? 'text-brand-accent hover:bg-brand-accent/10'
              : 'text-brand-muted hover:bg-brand-accent/10 hover:text-brand-accent'
          }`}
          aria-label={`${isPinned ? 'Unpin' : 'Pin'} ${title}`}
          title={isPinned ? 'Unpin conversation' : 'Pin conversation'}
        >
          <Pin size={13} fill={isPinned ? 'currentColor' : 'none'} />
        </button>
        <button
          type="button"
          onClick={() => onDelete(conv.id)}
          className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-sm text-brand-muted transition-colors hover:bg-brand-rose/10 hover:text-brand-rose focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-rose"
          aria-label={`Delete ${title}`}
          title="Delete conversation"
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  )
}
