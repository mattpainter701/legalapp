import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ShieldCheck, Download, Search, MoreVertical, PanelLeft } from 'lucide-react'

export default function ChatHeader({
  activeRef,
  activeConvTitle,
  usePremium,
  setUsePremium,
  includePublic,
  setIncludePublic,
  user,
  onExportConversation,
  onSearchMessages,
  onRenameConversation,
  onRenameError,
  onOpenSidebar,
}) {
  const navigate = useNavigate()
  const [showMenu, setShowMenu] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [editedTitle, setEditedTitle] = useState(activeConvTitle)
  const [savingTitle, setSavingTitle] = useState(false)

  useEffect(() => {
    setEditedTitle(activeConvTitle)
    setIsEditing(false)
  }, [activeConvTitle])

  const handleTitleEdit = async () => {
    if (savingTitle) return
    if (!isEditing) {
      if (activeConvTitle && onRenameConversation) setIsEditing(true)
      return
    }

    const nextTitle = editedTitle.trim()
    if (!nextTitle) {
      setEditedTitle(activeConvTitle)
      setIsEditing(false)
      onRenameError?.('Conversation title cannot be blank.')
      return
    }
    if (nextTitle === activeConvTitle) {
      setEditedTitle(activeConvTitle)
      setIsEditing(false)
      return
    }

    setSavingTitle(true)
    try {
      await onRenameConversation(nextTitle)
      setIsEditing(false)
    } catch (e) {
      onRenameError?.(e?.response?.data?.detail || e?.message || 'Conversation title could not be saved.')
      setEditedTitle(activeConvTitle)
      setIsEditing(false)
    } finally {
      setSavingTitle(false)
    }
  }

  const canEditTitle = Boolean(activeConvTitle && onRenameConversation)

  return (
    <div className="h-16 bg-brand-surface border-b border-brand-line px-4 md:px-6 flex items-center justify-between flex-shrink-0 z-20">
      {/* Left: Hamburger (mobile) + Conversation info */}
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <button
          className="lg:hidden tap-target text-brand-muted hover:text-brand-ink transition-colors -ml-1 flex-shrink-0"
          onClick={onOpenSidebar}
          aria-label="Open conversations panel"
          title="Conversations & documents"
        >
          <PanelLeft size={20} />
        </button>
      <div className="flex flex-col min-w-0 flex-1">
        <div className="text-xs font-mono text-brand-muted uppercase tracking-widest mb-0.5 flex items-center gap-2">
          <span>Case Ledger</span>
          <span className="text-brand-line-2">/</span>
          <span>Ref: {activeRef}</span>
        </div>
        {isEditing ? (
          <input
            type="text"
            value={editedTitle}
            onChange={(e) => setEditedTitle(e.target.value)}
            onBlur={handleTitleEdit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleTitleEdit()
              if (e.key === 'Escape') {
                setEditedTitle(activeConvTitle)
                setIsEditing(false)
              }
            }}
            disabled={savingTitle}
            className="font-serif text-xl text-brand-ink font-semibold bg-brand-bg border border-brand-accent px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-brand-accent disabled:opacity-60"
            autoFocus
          />
        ) : (
          <h1
            className={`font-serif text-xl text-brand-ink font-semibold truncate px-1 py-0.5 rounded transition-all ${
              canEditTitle ? 'cursor-pointer hover:text-brand-accent hover:bg-brand-line/20' : ''
            }`}
            onClick={handleTitleEdit}
            title={canEditTitle ? 'Click to edit title' : undefined}
          >
            {activeConvTitle || 'Select a conversation'}
          </h1>
        )}
      </div>
      </div>{/* end left flex wrapper */}

      {/* Right: Controls */}
      <div className="flex items-center gap-2 md:gap-6 flex-shrink-0">
        {/* Legal-safe badge */}
        <div
          className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 bg-brand-accent/10 text-brand-accent border border-brand-accent/20 text-xs font-semibold"
          title="Responses are grounded in your sources, cited by confidence, and gated for attorney review"
        >
          <ShieldCheck size={14} strokeWidth={2} />
          <span>LEGAL-SAFE</span>
        </div>

        {/* Model selector */}
        <div className="hidden sm:flex items-center bg-brand-surface-2 border border-brand-line p-0.5 rounded">
          <button
            onClick={() => setUsePremium(false)}
            className={`px-3 py-1.5 text-xs font-medium transition-all rounded-sm ${
              !usePremium
                ? 'bg-brand-surface text-brand-ink shadow-sm border border-brand-line'
                : 'text-brand-muted hover:text-brand-ink hover:bg-brand-line/30'
            }`}
          >
            Standard
          </button>
          <button
            onClick={() => setUsePremium(true)}
            className={`px-3 py-1.5 text-xs font-medium transition-all rounded-sm ${
              usePremium
                ? 'bg-brand-surface text-brand-ink shadow-sm border border-brand-line'
                : 'text-brand-muted hover:text-brand-ink hover:bg-brand-line/30'
            }`}
          >
            Premium
          </button>
        </div>

        {/* Public case law toggle */}
        <button
          type="button"
          role="switch"
          aria-checked={includePublic}
          onClick={() => setIncludePublic((v) => !v)}
          className="hidden md:flex items-center gap-2 cursor-pointer group focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-ink"
        >
          <span
            className={`relative inline-block w-8 h-4 rounded-full transition-colors ${
              includePublic ? 'bg-brand-accent' : 'bg-brand-line-2'
            }`}
          >
            <span
              className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-transform ${
                includePublic ? 'left-[18px]' : 'left-0.5'
              }`}
            />
          </span>
          <span className="text-xs font-medium text-brand-ink">Public case law</span>
        </button>

        {/* Menu */}
        <div className="relative group">
          <button
            onClick={() => setShowMenu(!showMenu)}
            className="p-2 text-brand-muted hover:text-brand-ink hover:bg-brand-line/40 rounded transition-all"
            title="More options"
          >
            <MoreVertical size={16} />
          </button>
          {showMenu && (
            <div className="absolute right-0 mt-2 w-48 bg-brand-surface border border-brand-line shadow-lg z-30 rounded-lg overflow-hidden animate-scale-in">
              {onSearchMessages && (
                <button
                  onClick={() => {
                    onSearchMessages()
                    setShowMenu(false)
                  }}
                  className="w-full text-left px-4 py-2.5 hover:bg-brand-line/40 text-sm text-brand-ink flex items-center gap-2 transition-colors"
                >
                  <Search size={14} /> Search messages
                </button>
              )}
              <button
                onClick={() => {
                  onExportConversation?.()
                  setShowMenu(false)
                }}
                className={`w-full text-left px-4 py-2.5 hover:bg-brand-line/40 text-sm text-brand-ink flex items-center gap-2 transition-colors ${onSearchMessages ? 'border-t border-brand-line' : ''}`}
              >
                <Download size={14} /> Export conversation
              </button>
{/* Admin button moved to AppShell header */}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
