import React, { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import FileUpload from './FileUpload'
import IntegrationPanel from './IntegrationPanel'
import { deleteDocument, deleteConversation } from '../api'
import { Plus, Blocks, FileText, Trash2, Settings, Scale, CheckCircle2, Loader2, Search, Pin, X, BarChart2, CalendarDays } from 'lucide-react'

function ConversationItem({
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
      <span className="font-mono text-[10px] pt-[3px] text-brand-muted shrink-0">
        {isPinned ? '📌' : String(index + 1).padStart(2, '0')}
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

function DocumentItem({ doc, onDelete }) {
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

export default function Sidebar({
  conversations,
  activeConvId,
  onSelectConversation,
  onNewConversation,
  onConversationDeleted,
  documents,
  onDocumentUploaded,
  onDocumentDeleted,
  user,
  onLogout,
}) {
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [pinnedConvIds, setPinnedConvIds] = useState([])

  const handleDeleteConv = async (id) => {
    try {
      await deleteConversation(id)
      onConversationDeleted(id)
    } catch (err) {
      console.error('Failed to delete conversation', err)
    }
  }

  const handleDeleteDoc = async (id) => {
    try {
      await deleteDocument(id)
      onDocumentDeleted(id)
    } catch (err) {
      console.error('Failed to delete document', err)
    }
  }

  const handleTogglePin = (id) => {
    setPinnedConvIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }

  // Filter and sort conversations
  const filteredConversations = useMemo(() => {
    const filtered = conversations.filter((conv) =>
      (conv.title || '').toLowerCase().includes(searchQuery.toLowerCase())
    )

    // Separate pinned and unpinned
    const pinned = filtered.filter((c) => pinnedConvIds.includes(c.id))
    const unpinned = filtered.filter((c) => !pinnedConvIds.includes(c.id))

    // Sort unpinned by date (newest first)
    unpinned.sort(
      (a, b) =>
        new Date(b.updated_at || b.created_at).getTime() -
        new Date(a.updated_at || a.created_at).getTime()
    )

    return [...pinned, ...unpinned]
  }, [conversations, searchQuery, pinnedConvIds])

  return (
    <div className="w-[300px] flex-shrink-0 border-r border-brand-line flex flex-col h-full bg-brand-surface-2 relative z-20">
      {/* Header */}
      <div className="h-16 flex items-center px-4 border-b border-brand-line shrink-0">
        <Scale className="w-5 h-5 mr-2 text-brand-accent" strokeWidth={1.5} />
        <span className="font-serif font-semibold text-lg tracking-tight text-brand-ink">Clarity Legal</span>
      </div>

      {/* Actions */}
      <div className="p-4 flex flex-col gap-2 border-b border-brand-line shrink-0">
        <button
          onClick={onNewConversation}
          className="flex items-center justify-between w-full px-3 py-2 bg-brand-ink text-white text-sm font-medium hover:bg-brand-ink-2 transition-colors border border-brand-ink"
        >
          <span className="flex items-center gap-2">
            <Plus className="w-4 h-4" /> New Conversation
          </span>
          <span className="text-white/50 text-xs font-mono">⌘N</span>
        </button>
        <button
          onClick={() => navigate('/plugins')}
          className="flex items-center justify-between w-full px-3 py-2 bg-transparent text-brand-ink text-sm hover:bg-brand-line/50 transition-colors border border-brand-line"
        >
          <span className="flex items-center gap-2">
            <Blocks className="w-4 h-4" /> Add-on Modules
          </span>
        </button>
        <button
          onClick={() => navigate('/reports')}
          className="flex items-center justify-between w-full px-3 py-2 bg-transparent text-brand-ink text-sm hover:bg-brand-line/50 transition-colors border border-brand-line"
        >
          <span className="flex items-center gap-2">
            <BarChart2 className="w-4 h-4" /> Reports
          </span>
        </button>
        <button
          onClick={() => navigate('/calendar')}
          className="flex items-center justify-between w-full px-3 py-2 bg-transparent text-brand-ink text-sm hover:bg-brand-line/50 transition-colors border border-brand-line"
        >
          <span className="flex items-center gap-2">
            <CalendarDays className="w-4 h-4" /> Calendar
          </span>
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
                  className="absolute right-2 top-2.5 text-brand-muted hover:text-brand-ink"
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
                  onClick={() => onSelectConversation(conv.id)}
                  onDelete={handleDeleteConv}
                  onTogglePin={handleTogglePin}
                />
              ))}
            </div>
          )}
        </div>

        <div className="w-full h-px bg-brand-line my-2"></div>

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
                <DocumentItem key={doc.id} doc={doc} onDelete={handleDeleteDoc} />
              ))}
            </div>
          )}
          <div className="px-4">
            <FileUpload onUploadComplete={onDocumentUploaded} />
          </div>
        </div>

        <div className="w-full h-px bg-brand-line my-2"></div>

        {/* Cloud Integrations */}
        <div className="py-4 px-4">
          <IntegrationPanel
            integrationStatus={{
              google_drive: { connected: false, fileCount: 0 },
              onedrive: { connected: false, fileCount: 0 },
              sharepoint: { connected: false, fileCount: 0 },
            }}
            onConnect={(serviceId) => {
              // TODO: Implement OAuth flow for each service
              console.log('Connect to:', serviceId)
            }}
            onDisconnect={(serviceId) => {
              // TODO: Implement disconnect
              console.log('Disconnect from:', serviceId)
            }}
          />
        </div>
      </div>

      {/* Footer / User info */}
      <div className="p-4 border-t border-brand-line flex items-center gap-3 bg-brand-surface-2 shrink-0">
        <div className="w-8 h-8 bg-brand-ink text-brand-bg flex items-center justify-center font-serif text-sm font-semibold shrink-0">
          {user?.full_name?.[0]?.toUpperCase() || user?.email?.[0]?.toUpperCase() || 'U'}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-brand-ink truncate">
            {user?.full_name || user?.email}
          </p>
          <p className="text-xs text-brand-muted font-mono uppercase tracking-wider truncate">
            {user?.billing_tier || 'Free Tier'}
          </p>
        </div>
        <button
          onClick={onLogout}
          className="text-brand-muted hover:text-brand-ink transition-colors shrink-0"
          title="Sign out"
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
