import React, { useRef, useState } from 'react'
import { FileText, Paperclip, Send, Sparkles, X } from 'lucide-react'

const QUICK_EXAMPLES = [
  'Summarize the key issues and open questions',
  'Draft a client-ready follow-up',
  'Build a chronology from the available sources',
  'Compare the governing standards',
]

export default function ChatInput({
  inputValue,
  onInputChange,
  onSend,
  onUploadClick,
  onDropFiles,
  isSending,
  disabled,
  pendingAttachments = [],
  onRemoveAttachment,
  placeholder = 'Ask about a matter, draft, document, or legal issue…',
  suggestions = QUICK_EXAMPLES,
}) {
  const textareaRef = useRef(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const charCount = inputValue.length

  const handleDragEnter = (event) => {
    event.preventDefault()
    event.stopPropagation()
    setIsDragOver(true)
  }

  const handleDragLeave = (event) => {
    event.preventDefault()
    event.stopPropagation()
    if (!event.currentTarget.contains(event.relatedTarget)) setIsDragOver(false)
  }

  const handleDragOver = (event) => {
    event.preventDefault()
    event.stopPropagation()
  }

  const handleDrop = (event) => {
    event.preventDefault()
    event.stopPropagation()
    setIsDragOver(false)
    const files = event.dataTransfer?.files
    if (files?.length && onDropFiles) onDropFiles(Array.from(files))
  }

  const handleTextareaChange = (event) => {
    onInputChange(event.target.value)
    event.target.style.height = 'auto'
    event.target.style.height = `${Math.min(event.target.scrollHeight, 200)}px`
  }

  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      onSend()
    }
  }

  const chooseSuggestion = (suggestion) => {
    onInputChange(suggestion)
    textareaRef.current?.focus()
  }

  return (
    <div
      className={`relative z-20 flex-shrink-0 border-t border-brand-line bg-brand-surface/95 px-2 pt-2 backdrop-blur transition-colors sm:px-4 sm:pt-3 md:px-6 ${
        isDragOver ? 'bg-brand-accent/10' : ''
      }`}
      style={{ paddingBottom: 'max(0.5rem, env(safe-area-inset-bottom, 0.5rem))' }}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {isDragOver && (
        <div className="pointer-events-none absolute inset-2 z-30 flex items-center justify-center rounded-2xl border-2 border-dashed border-brand-accent bg-brand-surface/95">
          <p className="flex items-center gap-2 text-sm font-semibold text-brand-accent-2">
            <Paperclip size={17} /> Add files to this conversation
          </p>
        </div>
      )}

      <div className="mx-auto flex max-w-4xl flex-col gap-2.5">
        {!inputValue && pendingAttachments.length === 0 && suggestions.length > 0 && (
          <div className="-mx-1 hidden gap-2 overflow-x-auto px-1 pb-0.5 sm:flex" aria-label="Suggested prompts">
            {suggestions.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => chooseSuggestion(suggestion)}
                className="inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border border-brand-line bg-brand-surface px-3 text-xs font-medium text-brand-ink hover:border-brand-line-2 hover:bg-brand-bg-soft"
              >
                <Sparkles size={13} className="text-brand-accent-2" />
                {suggestion}
              </button>
            ))}
          </div>
        )}

        {pendingAttachments.length > 0 && (
          <div className="flex flex-wrap gap-2" aria-label="Pending attachments">
            {pendingAttachments.map((attachment) => (
              <span
                key={attachment.id}
                className="inline-flex min-h-9 max-w-full items-center gap-2 rounded-lg border border-brand-line bg-brand-bg-soft px-2.5 text-xs text-brand-ink"
              >
                <FileText size={13} className="shrink-0 text-brand-accent-2" />
                <span className="max-w-56 truncate">{attachment.filename}</span>
                {onRemoveAttachment && (
                  <button
                    type="button"
                    onClick={() => onRemoveAttachment(attachment.id)}
                    className="rounded-md p-1 text-brand-muted hover:bg-brand-surface hover:text-brand-rose"
                    aria-label={`Remove ${attachment.filename}`}
                  >
                    <X size={13} />
                  </button>
                )}
              </span>
            ))}
          </div>
        )}

        <div className="rounded-xl border border-brand-line-2 bg-brand-surface p-1.5 shadow-sm focus-within:border-brand-accent focus-within:ring-2 focus-within:ring-brand-accent/15 sm:rounded-2xl sm:p-2">
          <textarea
            ref={textareaRef}
            value={inputValue}
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            aria-label="Message the assistant"
            aria-describedby="assistant-review-note"
            className="min-h-[42px] max-h-[120px] w-full resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed text-brand-ink placeholder-brand-muted focus:outline-none sm:min-h-[52px] sm:max-h-[200px] sm:py-2"
            rows={1}
            style={{ height: 'auto' }}
            disabled={disabled || isSending}
          />

          <div className="flex items-center justify-between gap-3 border-t border-brand-line/70 px-1 pt-2">
            <div className="flex min-w-0 items-center gap-1.5 sm:gap-2">
              <button
                type="button"
                onClick={onUploadClick}
                disabled={isSending}
                className="inline-flex min-h-9 items-center gap-2 rounded-lg px-2 text-xs font-semibold text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-10 sm:rounded-xl sm:px-2.5"
                aria-label="Attach a document"
              >
                <Paperclip size={16} />
                <span className="hidden sm:inline">Attach</span>
              </button>
              {charCount > 0 && (
                <span className={`text-[10px] font-mono ${charCount > 1000 ? 'text-brand-rose' : 'text-brand-muted'}`}>
                  {charCount.toLocaleString()}
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              <span className="hidden text-[10px] text-brand-muted md:inline">Enter to send · Shift+Enter for a new line</span>
              <button
                type="button"
                onClick={onSend}
                disabled={!inputValue.trim() || isSending}
                className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-brand-ink px-3 text-sm font-semibold text-white hover:bg-brand-ink-2 disabled:cursor-not-allowed disabled:bg-brand-line-2 disabled:text-brand-muted sm:min-h-10 sm:rounded-xl sm:px-3.5"
                aria-label={isSending ? 'Assistant is responding' : 'Send message'}
              >
                {isSending ? (
                  <>
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                    <span className="hidden sm:inline">Working</span>
                  </>
                ) : (
                  <>
                    <Send size={16} />
                    <span className="hidden sm:inline">Send</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>

        <p id="assistant-review-note" className="hidden text-center text-[10px] leading-relaxed text-brand-muted sm:block">
          Verify cited authority, dates, and legal conclusions before relying on assistant work.
        </p>
      </div>
    </div>
  )
}
