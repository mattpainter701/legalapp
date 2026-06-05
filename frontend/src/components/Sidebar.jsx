import React, { useState, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import FileUpload from './FileUpload'
import IntegrationPanel from './IntegrationPanel'
import { Plus, Blocks, FileText, Trash2, Scale, CheckCircle2, Loader2, Search, Pin, X, BarChart2, CalendarDays, MessageSquare, FileSignature, Briefcase, Clock, Receipt, User, ChevronRight } from 'lucide-react'

const NAV_ITEMS = [
  { path: '/matters',        label: 'My Matters',       icon: Briefcase,    primary: true },
  { path: '/calendar',       label: 'Calendar',         icon: CalendarDays  },
  { path: '/communications', label: 'Communications',   icon: MessageSquare },
  { path: '/time-tracking',  label: 'Time Tracking',    icon: Clock         },
  { path: '/invoices',       label: 'Invoices',         icon: Receipt       },
  { path: '/reports',        label: 'Reports',          icon: BarChart2     },
  { path: '/templates',      label: 'Templates',        icon: FileSignature },
  { path: '/plugins',        label: 'Add-on Modules',   icon: Blocks        },
]

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
  isOpen = true,
  onClose,
}) {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [searchQuery, setSearchQuery] = useState('')
  const [showIntegrations, setShowIntegrations] = useState(false)
  const [pinnedConvIds, setPinnedConvIds] = useState(() => {
    try { return JSON.parse(localStorage.getItem('pinnedConvIds') || '[]') }
    catch { return [] }
  })

  const isActive = (path) => pathname === path || pathname.startsWith(path + '/')

  const handleDeleteConv = (id) => onConversationDeleted(id)
  const handleDeleteDoc  = (id) => onDocumentDeleted(id)

  const handleTogglePin = (id) => {
    setPinnedConvIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
      localStorage.setItem('pinnedConvIds', JSON.stringify(next))
      return next
    })
  }

  // Filter and sort conversations
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

  const handleNavAndClose = (path) => {
    navigate(path)
    onClose?.()
  }

  return (
    <>
      {/* Mobile backdrop */}
      <div
        className={`fixed inset-0 bg-black/40 z-30 md:hidden transition-opacity duration-300 ${isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Sidebar panel */}
      <div className={`
        fixed md:relative inset-y-0 left-0 z-40
        w-[300px] flex-shrink-0 border-r border-brand-line flex flex-col h-full bg-brand-surface-2
        transition-transform duration-300 ease-in-out
        ${isOpen ? 'sidebar-visible' : 'sidebar-hidden md:translate-x-0'}
      `}>
      {/* Header */}
      <div className="h-16 flex items-center px-4 border-b border-brand-line shrink-0">
        <Scale className="w-5 h-5 mr-2 text-brand-accent" strokeWidth={1.5} />
        <span className="font-serif font-semibold text-lg tracking-tight text-brand-ink flex-1">Clarity Legal</span>
        <button
          className="md:hidden p-1.5 text-brand-muted hover:text-brand-ink transition-colors"
          onClick={onClose}
          aria-label="Close sidebar"
        >
          <X size={18} />
        </button>
      </div>

      {/* Navigation */}
      <div className="p-3 flex flex-col gap-0.5 border-b border-brand-line shrink-0">
        {/* New Conversation — action button, not a nav destination */}
        <button
          onClick={() => { onNewConversation?.(); onClose?.() }}
          className="flex items-center justify-between w-full px-3 py-2 mb-1 bg-transparent text-brand-ink text-sm hover:bg-brand-line/50 transition-colors border border-brand-line rounded"
        >
          <span className="flex items-center gap-2">
            <Plus className="w-4 h-4" /> New Conversation
          </span>
          <span className="text-brand-muted text-xs font-mono">⌘N</span>
        </button>

        {NAV_ITEMS.map(({ path, label, icon: Icon, primary }) => {
          const active = isActive(path)
          if (primary) {
            return (
              <button
                key={path}
                onClick={() => handleNavAndClose(path)}
                className={`sidebar-item w-full rounded text-white ${active ? 'bg-brand-ink-2' : 'bg-brand-ink hover:bg-brand-ink-2'}`}
              >
                <Icon className="w-4 h-4" /> {label}
              </button>
            )
          }
          return (
            <button
              key={path}
              onClick={() => handleNavAndClose(path)}
              className={`sidebar-item w-full rounded ${active ? 'sidebar-item-active' : 'sidebar-item-inactive'}`}
            >
              <Icon className="w-4 h-4" /> {label}
            </button>
          )
        })}
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
          onClick={() => handleNavAndClose('/profile')}
          className="text-brand-muted hover:text-brand-ink transition-colors shrink-0"
          title="Profile"
        >
          <User className="w-4 h-4" />
        </button>
        <button
          onClick={onLogout}
          className="text-brand-muted hover:text-brand-rose transition-colors shrink-0 ml-2"
          title="Sign out"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
        </button>
      </div>
      </div>{/* end sidebar panel */}
    </>
  )
}
