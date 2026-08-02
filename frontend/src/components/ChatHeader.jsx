import React, { useEffect, useRef, useState } from 'react'
import {
  Check,
  ChevronDown,
  Download,
  MoreVertical,
  PanelLeft,
  Search,
  Settings2,
  ShieldCheck,
} from 'lucide-react'

function useDismissablePopover(open, onClose, containerRef) {
  useEffect(() => {
    if (!open) return undefined

    const handlePointerDown = (event) => {
      if (!containerRef.current?.contains(event.target)) onClose()
    }
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [containerRef, onClose, open])
}

export default function ChatHeader({
  activeRef,
  activeConvTitle,
  usePremium,
  setUsePremium,
  includePublic,
  setIncludePublic,
  onExportConversation,
  onSearchMessages,
  onRenameConversation,
  onRenameError,
  onOpenSidebar,
}) {
  const [showMenu, setShowMenu] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [editedTitle, setEditedTitle] = useState(activeConvTitle)
  const [savingTitle, setSavingTitle] = useState(false)
  const menuRef = useRef(null)
  const settingsRef = useRef(null)

  useDismissablePopover(showMenu, () => setShowMenu(false), menuRef)
  useDismissablePopover(showSettings, () => setShowSettings(false), settingsRef)

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
    } catch (error) {
      onRenameError?.(
        error?.response?.data?.detail ||
        error?.message ||
        'Conversation title could not be saved.',
      )
      setEditedTitle(activeConvTitle)
      setIsEditing(false)
    } finally {
      setSavingTitle(false)
    }
  }

  const canEditTitle = Boolean(activeConvTitle && onRenameConversation)

  return (
    <header className="z-20 flex min-h-12 flex-shrink-0 items-center justify-between gap-2 border-b border-brand-line bg-brand-surface px-2 py-1.5 sm:min-h-16 sm:gap-3 sm:px-4 sm:py-2 md:px-6">
      <div className="flex min-w-0 flex-1 items-center gap-2 sm:gap-3">
        <button
          type="button"
          className="tap-target -ml-1 flex-shrink-0 rounded-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink lg:hidden"
          onClick={onOpenSidebar}
          aria-label="Open conversations and sources"
          title="Conversations and sources"
        >
          <PanelLeft size={20} />
        </button>

        <div className="min-w-0 flex-1">
          <div className="mb-0.5 hidden items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-brand-muted sm:flex">
            <span>AI assistant</span>
            {activeRef !== '—' && (
              <>
                <span aria-hidden="true" className="text-brand-line-2">/</span>
                <span>Conversation {activeRef}</span>
              </>
            )}
          </div>
          {isEditing ? (
            <input
              type="text"
              value={editedTitle}
              onChange={(event) => setEditedTitle(event.target.value)}
              onBlur={handleTitleEdit}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleTitleEdit()
                if (event.key === 'Escape') {
                  setEditedTitle(activeConvTitle)
                  setIsEditing(false)
                }
              }}
              disabled={savingTitle}
              aria-label="Conversation title"
              className="w-full max-w-xl rounded-lg border border-brand-accent bg-brand-bg px-2 py-1 font-serif text-lg font-semibold text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent disabled:opacity-60"
              autoFocus
            />
          ) : (
            <h1 className="min-w-0 truncate font-serif text-base font-semibold text-brand-ink sm:text-xl">
              {canEditTitle ? (
                <button
                  type="button"
                  onClick={handleTitleEdit}
                  className="max-w-full truncate rounded-md text-left hover:text-brand-accent-2"
                  title="Rename conversation"
                >
                  {activeConvTitle}
                </button>
              ) : (
                activeConvTitle || 'Start a conversation'
              )}
            </h1>
          )}
        </div>
      </div>

      <div className="flex flex-shrink-0 items-center gap-1.5 sm:gap-2">
        <div
          className="hidden items-center gap-1.5 rounded-full border border-brand-accent/20 bg-brand-accent/10 px-2.5 py-1 text-[11px] font-semibold text-brand-accent-2 sm:flex"
          title="Assistant work should be verified before it is relied upon"
        >
          <ShieldCheck size={14} aria-hidden="true" />
          <span>Review required</span>
        </div>

        <div className="relative" ref={settingsRef}>
          <button
            type="button"
            onClick={() => {
              setShowSettings((open) => !open)
              setShowMenu(false)
            }}
            aria-label="Response settings"
            aria-haspopup="dialog"
            aria-expanded={showSettings}
            className={`inline-flex min-h-9 items-center gap-2 rounded-lg border px-2 text-xs font-semibold sm:min-h-10 sm:rounded-xl sm:px-2.5 ${
              showSettings
                ? 'border-brand-ink bg-brand-ink text-white'
                : 'border-brand-line bg-brand-surface text-brand-ink hover:bg-brand-bg-soft'
            }`}
          >
            <Settings2 size={16} aria-hidden="true" />
            <span className="hidden md:inline">{usePremium ? 'Premium' : 'Standard'}</span>
            <ChevronDown size={13} className="hidden md:block" aria-hidden="true" />
          </button>

          {showSettings && (
            <div
              role="dialog"
              aria-label="Response settings"
              className="absolute right-0 top-[calc(100%+0.5rem)] z-30 w-[min(22rem,calc(100vw-1.5rem))] rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-xl"
            >
              <div>
                <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-brand-muted">
                  Response model
                </p>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {[
                    {
                      value: false,
                      label: 'Standard',
                      description: 'Everyday research and drafting',
                    },
                    {
                      value: true,
                      label: 'Premium',
                      description: 'More complex analysis',
                    },
                  ].map((option) => {
                    const selected = usePremium === option.value
                    return (
                      <button
                        key={option.label}
                        type="button"
                        aria-pressed={selected}
                        onClick={() => setUsePremium(option.value)}
                        className={`rounded-xl border p-3 text-left ${
                          selected
                            ? 'border-brand-accent bg-brand-accent/10'
                            : 'border-brand-line hover:bg-brand-bg-soft'
                        }`}
                      >
                        <span className="flex items-center justify-between gap-2 text-sm font-semibold text-brand-ink">
                          {option.label}
                          {selected && <Check size={15} className="text-brand-accent-2" />}
                        </span>
                        <span className="mt-1 block text-[11px] leading-snug text-brand-muted">
                          {option.description}
                        </span>
                      </button>
                    )
                  })}
                </div>
              </div>

              <div className="mt-4 border-t border-brand-line pt-4">
                <button
                  type="button"
                  role="switch"
                  aria-checked={includePublic}
                  onClick={() => setIncludePublic((value) => !value)}
                  className="flex w-full items-center justify-between gap-4 rounded-xl text-left"
                >
                  <span>
                    <span className="block text-sm font-semibold text-brand-ink">Public case law</span>
                    <span className="mt-0.5 block text-[11px] leading-snug text-brand-muted">
                      Include available public authorities alongside firm and matter sources.
                    </span>
                  </span>
                  <span
                    aria-hidden="true"
                    className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
                      includePublic ? 'bg-brand-accent' : 'bg-brand-line-2'
                    }`}
                  >
                    <span
                      className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
                        includePublic ? 'translate-x-6' : 'translate-x-1'
                      }`}
                    />
                  </span>
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="relative" ref={menuRef}>
          <button
            type="button"
            onClick={() => {
              setShowMenu((open) => !open)
              setShowSettings(false)
            }}
            className="tap-target rounded-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink"
            aria-label="Conversation options"
            aria-haspopup="menu"
            aria-expanded={showMenu}
          >
            <MoreVertical size={18} />
          </button>
          {showMenu && (
            <div
              role="menu"
              className="absolute right-0 mt-2 w-52 overflow-hidden rounded-xl border border-brand-line bg-brand-surface py-1 shadow-lg"
            >
              {onSearchMessages && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    onSearchMessages()
                    setShowMenu(false)
                  }}
                  className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm text-brand-ink hover:bg-brand-bg-soft"
                >
                  <Search size={15} /> Search messages
                </button>
              )}
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onExportConversation?.()
                  setShowMenu(false)
                }}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm text-brand-ink hover:bg-brand-bg-soft"
              >
                <Download size={15} /> Export conversation
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
