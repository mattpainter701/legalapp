import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Database, FileText, MessageSquare, Plus, Search, X } from 'lucide-react'
import { useAppShell } from '../AppShell'
import FileUpload from '../FileUpload'
import ConversationItem from './ConversationItem'
import DocumentItem from './DocumentItem'

const PIN_STORAGE_KEY = 'clarity.chat.pinned-conversations'

function readPinnedConversationIds() {
  try {
    const stored = localStorage.getItem(PIN_STORAGE_KEY) || localStorage.getItem('pinnedConvIds')
    const ids = JSON.parse(stored || '[]')
    return Array.isArray(ids) ? ids : []
  } catch {
    return []
  }
}

export default function ChatRail({
  className = '',
  onNewConversation,
  onSelectConversation,
  onDeleteConversation,
  onClose,
  isOpen = true,
  sourceHealth = null,
}) {
  const {
    conversations,
    documents,
    activeConvId,
    onConversationDeleted,
    onDocumentUploaded,
    onDocumentDeleted,
  } = useAppShell()
  const handleDeleteConversation = onDeleteConversation || onConversationDeleted
  const [activeSection, setActiveSection] = useState('conversations')
  const [searchQuery, setSearchQuery] = useState('')
  const [pinnedConvIds, setPinnedConvIds] = useState(readPinnedConversationIds)
  const panelRef = useRef(null)
  const closeRef = useRef(null)
  const previousFocusRef = useRef(null)

  useEffect(() => {
    if (!onClose || !isOpen) return undefined
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    queueMicrotask(() => closeRef.current?.focus())

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = Array.from(panelRef.current?.querySelectorAll(
        'button:not([disabled]):not([tabindex="-1"]), input:not([disabled]):not([tabindex="-1"]), [href]:not([tabindex="-1"]), [tabindex]:not([tabindex="-1"])',
      ) || [])
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousBodyOverflow
      queueMicrotask(() => previousFocusRef.current?.focus())
    }
  }, [isOpen, onClose])

  const handleTogglePin = (id) => {
    setPinnedConvIds((previous) => {
      const next = previous.includes(id)
        ? previous.filter((conversationId) => conversationId !== id)
        : [...previous, id]
      try {
        localStorage.setItem(PIN_STORAGE_KEY, JSON.stringify(next))
      } catch {
        // Pinning remains available for the current session.
      }
      return next
    })
  }

  const filteredConversations = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase()
    const filtered = conversations.filter((conversation) =>
      (conversation.title || '').toLowerCase().includes(normalizedQuery),
    )
    const pinned = filtered.filter((conversation) => pinnedConvIds.includes(conversation.id))
    const unpinned = filtered.filter((conversation) => !pinnedConvIds.includes(conversation.id))
    unpinned.sort(
      (a, b) =>
        new Date(b.updated_at || b.created_at).getTime() -
        new Date(a.updated_at || a.created_at).getTime(),
    )
    return [...pinned, ...unpinned]
  }, [conversations, pinnedConvIds, searchQuery])

  const isMobileDrawer = Boolean(onClose)

  return (
    <aside
      ref={panelRef}
      role={isMobileDrawer && isOpen ? 'dialog' : undefined}
      aria-modal={isMobileDrawer && isOpen ? true : undefined}
      aria-label={isMobileDrawer && isOpen ? 'Conversations and sources' : 'Assistant workspace'}
      {...(isMobileDrawer && !isOpen ? { inert: '', 'aria-hidden': true } : {})}
      className={`flex flex-col bg-brand-surface-2 ${className}`}
    >
      <div className="flex min-h-16 shrink-0 items-center justify-between border-b border-brand-line px-4">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-brand-muted">Workspace</p>
          <p className="mt-0.5 font-serif text-lg font-semibold text-brand-ink">Assistant</p>
        </div>
        {onClose && (
          <button
            ref={closeRef}
            type="button"
            className="tap-target rounded-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink lg:hidden"
            onClick={onClose}
            aria-label="Close conversations and sources"
          >
            <X size={19} />
          </button>
        )}
      </div>

      <div className="shrink-0 border-b border-brand-line p-3">
        <button
          type="button"
          onClick={() => {
            onNewConversation?.()
            onClose?.()
          }}
          className="flex min-h-11 w-full items-center justify-between rounded-xl bg-brand-ink px-3.5 text-sm font-semibold text-white hover:bg-brand-ink-2"
        >
          <span className="flex items-center gap-2">
            <Plus className="h-4 w-4" /> New conversation
          </span>
          <kbd className="hidden rounded border border-white/25 px-1.5 py-0.5 font-mono text-[10px] font-normal text-white/75 sm:inline">
            Ctrl N
          </kbd>
        </button>
      </div>

      <div
        role="tablist"
        aria-label="Assistant workspace sections"
        className="grid grid-cols-2 gap-1 border-b border-brand-line px-3 py-2"
      >
        {[
          {
            id: 'conversations',
            label: 'Conversations',
            icon: MessageSquare,
            count: conversations.length,
          },
          {
            id: 'sources',
            label: 'Sources',
            icon: FileText,
            count: documents.length,
          },
        ].map(({ id, label, icon: Icon, count }) => {
          const selected = activeSection === id
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`chat-rail-${id}`}
              onClick={() => setActiveSection(id)}
              className={`flex min-h-10 items-center justify-center gap-2 rounded-lg px-2 text-xs font-semibold ${
                selected
                  ? 'bg-brand-surface text-brand-ink shadow-sm'
                  : 'text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink'
              }`}
            >
              <Icon size={14} />
              <span>{label}</span>
              <span className={`font-mono text-[10px] ${selected ? 'text-brand-accent-2' : ''}`}>
                {count}
              </span>
            </button>
          )
        })}
      </div>

      {activeSection === 'conversations' ? (
        <div
          id="chat-rail-conversations"
          role="tabpanel"
          className="flex min-h-0 flex-1 flex-col"
        >
          {conversations.length > 0 && (
            <div className="relative shrink-0 px-3 py-3">
              <Search className="pointer-events-none absolute left-6 top-1/2 h-4 w-4 -translate-y-1/2 text-brand-muted" />
              <input
                type="search"
                aria-label="Search conversations"
                placeholder="Search conversations"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                className="min-h-10 w-full rounded-xl border border-brand-line bg-brand-surface py-2 pl-9 pr-9 text-sm text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-2 focus:ring-brand-accent"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="absolute right-5 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-brand-muted hover:text-brand-ink"
                  aria-label="Clear conversation search"
                >
                  <X size={14} />
                </button>
              )}
            </div>
          )}

          <div className="flex-1 overflow-y-auto pb-4">
            {conversations.length === 0 ? (
              <div className="px-5 py-10 text-center">
                <MessageSquare size={24} className="mx-auto text-brand-line-2" />
                <p className="mt-3 text-sm font-semibold text-brand-ink">No conversations yet</p>
                <p className="mt-1 text-xs leading-relaxed text-brand-muted">
                  Start a conversation to keep research and drafting work together.
                </p>
              </div>
            ) : filteredConversations.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-brand-muted">
                No conversations match “{searchQuery}”.
              </p>
            ) : (
              <div className="flex flex-col">
                {filteredConversations.map((conversation, index) => (
                  <ConversationItem
                    key={conversation.id}
                    conv={conversation}
                    index={index}
                    isActive={conversation.id === activeConvId}
                    isPinned={pinnedConvIds.includes(conversation.id)}
                    onClick={() => onSelectConversation?.(conversation.id)}
                    onDelete={handleDeleteConversation}
                    onTogglePin={handleTogglePin}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div
          id="chat-rail-sources"
          role="tabpanel"
          className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3"
        >
          <PublicSourceHealth health={sourceHealth} />

          <div className="mt-3 rounded-xl border border-brand-line bg-brand-surface p-3">
            <p className="text-sm font-semibold text-brand-ink">Firm source library</p>
            <p className="mt-1 text-xs leading-relaxed text-brand-muted">
              Upload reference material that can be retrieved across assistant conversations.
            </p>
            <div className="mt-3">
              <FileUpload onUploadComplete={onDocumentUploaded} />
            </div>
          </div>

          {documents.length === 0 ? (
            <div className="px-3 py-10 text-center">
              <FileText size={24} className="mx-auto text-brand-line-2" />
              <p className="mt-3 text-sm font-semibold text-brand-ink">No library sources</p>
              <p className="mt-1 text-xs leading-relaxed text-brand-muted">
                Conversation attachments remain scoped to their thread. Add reusable firm material here.
              </p>
            </div>
          ) : (
            <div className="mt-3 overflow-hidden rounded-xl border border-brand-line bg-brand-surface">
              {documents.map((document) => (
                <DocumentItem key={document.id} doc={document} onDelete={onDocumentDeleted} />
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  )
}

function formatHealthDate(value) {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(parsed)
}

function PublicSourceHealth({ health }) {
  if (!health) {
    return (
      <div className="rounded-xl border border-brand-line bg-brand-surface p-3" aria-label="Loading public authority status">
        <p className="text-sm font-semibold text-brand-ink">Public legal authority</p>
        <p className="mt-1 text-xs text-brand-muted">Checking coverage and freshness…</p>
      </div>
    )
  }

  const sources = (health.sources || []).filter(Boolean)
  const source = sources.find((item) => item.jurisdiction === 'OH') || sources[0]
  const attention = health.status === 'attention'
  if (!health.available || !source) {
    return (
      <div className="rounded-xl border border-brand-line bg-brand-surface p-3">
        <div className="flex items-start gap-2">
          <Database size={16} className="mt-0.5 shrink-0 text-brand-muted" />
          <div>
            <p className="text-sm font-semibold text-brand-ink">Public legal authority</p>
            <p className="mt-1 text-xs leading-relaxed text-brand-muted">
              Local coverage details are unavailable. Research answers should be checked against linked authorities.
            </p>
          </div>
        </div>
      </div>
    )
  }

  const coverageStart = formatHealthDate(source.coverage_start)
  const coverageEnd = formatHealthDate(source.coverage_end)
  const lastSync = formatHealthDate(source.last_successful_sync_at)
  const chunks = Number(source.chunk_count || 0)
  const embedded = Number(source.embedded_chunk_count || 0)
  const itemCount = Number(source.item_count || 0)
  const coverage = [coverageStart, coverageEnd].filter(Boolean).join('–')
  const version = health.corpus_version?.version || 'No promoted release'
  const claimSuppressed = health.claim_state === 'suppressed'

  return (
    <details className="group rounded-xl border border-brand-line bg-brand-surface p-3">
      <summary className="flex cursor-pointer list-none items-start justify-between gap-3">
        <span className="flex min-w-0 items-start gap-2">
          <Database size={16} className="mt-0.5 shrink-0 text-brand-accent-2" />
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-brand-ink">Public legal authority</span>
            <span className="mt-1 block text-xs leading-relaxed text-brand-muted">
              {sources.length} reviewed source{sources.length === 1 ? '' : 's'} · corpus {version}
            </span>
          </span>
        </span>
        <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${
          attention ? 'bg-brand-amber/15 text-brand-amber' : 'bg-brand-accent/10 text-brand-accent-2'
        }`}>
          {attention ? <AlertTriangle size={11} /> : <CheckCircle2 size={11} />}
          {claimSuppressed ? 'Claims limited' : attention ? 'Check status' : 'Synced'}
        </span>
      </summary>
      <div className="mt-3 border-t border-brand-line pt-3 text-xs text-brand-muted">
        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1.5">
          <dt>Local snapshot</dt><dd className="text-right text-brand-ink">{coverageEnd || 'Not reported'}</dd>
          <dt>Last successful sync</dt><dd className="text-right text-brand-ink">{lastSync || 'Not reported'}</dd>
          <dt>Search passages</dt><dd className="text-right text-brand-ink">{embedded.toLocaleString()} / {chunks.toLocaleString()} embedded</dd>
          <dt>Source scope</dt><dd className="text-right text-brand-ink">{source.source_type || 'Authority'} · {source.jurisdiction || 'Named jurisdiction'}</dd>
          <dt>Temporal coverage</dt><dd className="text-right text-brand-ink">{coverage || 'Not established'} · {itemCount.toLocaleString()} records</dd>
          <dt>Rights review</dt><dd className="text-right text-brand-ink">{source.rights_decision || 'Pending review'}</dd>
        </dl>
        <p className="mt-3 leading-relaxed">
          {health.claim_notice || source.claim_safe_wording || 'Coverage is bounded and does not replace checking the linked source or a citator.'}
        </p>
        {sources.length > 1 && (
          <ul className="mt-3 space-y-1 border-t border-brand-line pt-3">
            {sources.slice(0, 6).map((item) => (
              <li key={item.source_key} className="flex items-center justify-between gap-2">
                <span className="truncate text-brand-ink">{item.display_name || item.source_key}</span>
                <span className="shrink-0 text-[10px] uppercase tracking-wide">{item.claim_state || item.status || 'limited'}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  )
}
