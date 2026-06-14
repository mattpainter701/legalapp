import React, { useState, useMemo } from 'react'
import { Plus, Search, X, ChevronRight } from 'lucide-react'
import { useAppShell } from '../AppShell'
import FileUpload from '../FileUpload'
import IntegrationPanel from '../IntegrationPanel'
import ConversationItem from './ConversationItem'
import DocumentItem from './DocumentItem'

export default function ChatRail({
  className = '',
  onNewConversation,
  onSelectConversation,
  onDeleteConversation,
  onClose,
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

  const [searchQuery, setSearchQuery] = useState('')
  const [showIntegrations, setShowIntegrations] = useState(false)
  const [pinnedConvIds, setPinnedConvIds] = useState(() => {
    try { return JSON.parse(localStorage.getItem('pinnedConvIds') || '[]') }
    catch { return [] }
  })

  const handleTogglePin = (id) => {
    setPinnedConvIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
      localStorage.setItem('pinnedConvIds', JSON.stringify(next))
      return next
    })
  }

  const filteredConversations = useMemo(() => {
    const filtered = conversations.filter((conv) =>
      (conv.title || '').toLowerCase().includes(searchQuery.toLowerCase())
    )

    const pinned = filtered.filter((c) => pinnedConvIds.includes(c.id))
    const unpinned = filtered.filter((c) => !pinnedConvIds.includes(c.id))

    unpinned.sort(
      (a, b) =>
        new Date(b.updated_at || b.created_at).getTime() -
        new Date(a.updated_at || a.created_at).getTime()
    )

    return [...pinned, ...unpinned]
  }, [conversations, searchQuery, pinnedConvIds])

  return (
    <div className={`flex flex-col bg-brand-surface-2 ${className}`}>
      {/* Header */}
      <div className="h-14 flex items-center justify-between px-4 border-b border-brand-line shrink-0">
        <span className="text-xs font-semibold uppercase tracking-wider text-brand-muted">Assistant</span>
        {onClose && (
          <button
            className="lg:hidden p-1.5 text-brand-muted hover:text-brand-ink transition-colors tap-target"
            onClick={onClose}
            aria-label="Close panel"
          >
            <X size={18} />
          </button>
        )}
      </div>

      {/* New conversation */}
      <div className="p-3 border-b border-brand-line shrink-0">
        <button
          onClick={() => { onNewConversation?.(); onClose?.() }}
          className="flex items-center justify-between w-full px-3 py-2 bg-transparent text-brand-ink text-sm hover:bg-brand-line/50 transition-colors border border-brand-line rounded"
        >
          <span className="flex items-center gap-2">
            <Plus className="w-4 h-4" /> New Conversation
          </span>
          <span className="text-brand-muted text-xs font-mono">⌘N</span>
        </button>
      </div>

      {/* Scrollable areas */}
      <div className="flex-1 overflow-y-auto">
        {/* Conversations */}
        <div className="py-4">
          <div className="px-4 mb-3 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-brand-muted">
            <span>Conversations</span>
            <span className="font-mono text-[10px]">{filteredConversations.length}</span>
          </div>

          {/* Search */}
          {conversations.length > 0 && (
            <div className="px-4 mb-3 relative">
              <Search className="absolute left-6 top-2.5 w-3.5 h-3.5 text-brand-muted pointer-events-none" />
              <input
                type="text"
                placeholder="Search..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-8 pr-3 py-1.5 bg-brand-bg border border-brand-line rounded text-xs text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-1 focus:ring-brand-accent"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-6 top-2.5 text-brand-muted hover:text-brand-ink"
                >
                  <X size={12} />
                </button>
              )}
            </div>
          )}

          {conversations.length === 0 ? (
            <p className="px-4 text-[13px] text-brand-muted italic font-sans">No history yet</p>
          ) : filteredConversations.length === 0 ? (
            <p className="px-4 text-[13px] text-brand-muted italic font-sans">No matching conversations</p>
          ) : (
            <div className="flex flex-col">
              {filteredConversations.map((conv, index) => (
                <ConversationItem
                  key={conv.id}
                  conv={conv}
                  index={index}
                  isActive={conv.id === activeConvId}
                  isPinned={pinnedConvIds.includes(conv.id)}
                  onClick={() => onSelectConversation?.(conv.id)}
                  onDelete={handleDeleteConversation}
                  onTogglePin={handleTogglePin}
                />
              ))}
            </div>
          )}
        </div>

        <div className="w-full h-px bg-brand-line my-2" />

        {/* Library Documents */}
        <div className="py-4">
          <div className="px-4 mb-2 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-brand-muted">
            <span>Library Documents</span>
            <span className="font-mono text-[10px]">{documents.length}</span>
          </div>
          {documents.length === 0 ? (
            <p className="px-4 text-[13px] text-brand-muted italic font-sans mb-3">No documents uploaded</p>
          ) : (
            <div className="flex flex-col gap-1 px-2 mb-3">
              {documents.map((doc) => (
                <DocumentItem key={doc.id} doc={doc} onDelete={onDocumentDeleted} />
              ))}
            </div>
          )}
          <div className="px-4">
            <FileUpload onUploadComplete={onDocumentUploaded} />
          </div>
        </div>

        <div className="w-full h-px bg-brand-line my-2" />

        {/* Cloud Integrations — collapsible */}
        <div className="py-2">
          <button
            onClick={() => setShowIntegrations((v) => !v)}
            className="w-full px-4 py-2 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-brand-muted hover:text-brand-ink transition-colors"
          >
            <span>Cloud Integrations</span>
            <ChevronRight
              size={14}
              className={`transition-transform duration-200 ${showIntegrations ? 'rotate-90' : ''}`}
            />
          </button>
          {showIntegrations && (
            <div className="px-4 pb-4">
              <IntegrationPanel
                integrationStatus={{
                  google_drive: { connected: false, fileCount: 0 },
                  onedrive: { connected: false, fileCount: 0 },
                  sharepoint: { connected: false, fileCount: 0 },
                }}
                onConnect={(serviceId) => {
                  console.log('Connect to:', serviceId)
                }}
                onDisconnect={(serviceId) => {
                  console.log('Disconnect from:', serviceId)
                }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
