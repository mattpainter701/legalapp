import React, { useRef, useState } from 'react'
import { FileText, Send, Upload, Sparkles } from 'lucide-react'

export default function ChatInput({
  inputValue,
  onInputChange,
  onSend,
  onUploadClick,
  isSending,
  disabled,
  placeholder = "Ask a legal question or drop a document here...",
}) {
  const textareaRef = useRef(null)
  const [showExamples, setShowExamples] = useState(false)
  const [charCount, setCharCount] = useState(0)

  const handleTextareaChange = (e) => {
    const value = e.target.value
    onInputChange(value)
    setCharCount(value.length)

    // Auto-resize
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px'
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSend()
    }
  }

  const quickExamples = [
    'Summarize the key holdings in Twombly and Iqbal',
    'Draft a demand letter for breach of contract',
    'What are the elements of promissory estoppel?',
    'Compare negligence standards across jurisdictions',
  ]

  return (
    <div className="bg-brand-surface border-t border-brand-line px-8 py-4 flex-shrink-0 z-20">
      <div className="max-w-4xl mx-auto flex flex-col gap-3">
        {/* Quick examples dropdown */}
        <div className="flex justify-center relative">
          <button
            onClick={() => setShowExamples(!showExamples)}
            className="text-xs font-mono text-brand-muted uppercase tracking-wider hover:text-brand-ink transition-colors flex items-center gap-1 mb-2"
          >
            <Sparkles size={12} /> Suggested prompts
          </button>
          {showExamples && (
            <div className="absolute top-8 left-1/2 -translate-x-1/2 w-96 bg-brand-surface border border-brand-line shadow-lg z-10 rounded-lg overflow-hidden animate-scale-in">
              {quickExamples.map((example, i) => (
                <button
                  key={i}
                  onClick={() => {
                    onInputChange(example)
                    setShowExamples(false)
                    setCharCount(example.length)
                    textareaRef.current?.focus()
                  }}
                  className="w-full text-left px-4 py-3 hover:bg-brand-line/40 text-sm text-brand-ink transition-colors border-b border-brand-line last:border-b-0"
                >
                  {example}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Input area */}
        <div className="relative flex items-end shadow-sm">
          <div className="absolute left-4 top-4 text-brand-muted pointer-events-none">
            <FileText className="w-5 h-5" strokeWidth={1.5} />
          </div>

          <textarea
            ref={textareaRef}
            value={inputValue}
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="w-full resize-none bg-brand-bg border border-brand-ink text-brand-ink px-12 py-4 pr-24 min-h-[56px] max-h-[200px] text-[15px] font-sans focus:outline-none focus:ring-1 focus:ring-brand-ink placeholder-brand-muted leading-relaxed"
            rows={1}
            style={{ height: 'auto' }}
            disabled={disabled || isSending}
          />

          {/* Action buttons */}
          <div className="absolute right-3 top-3 flex items-center gap-2">
            {charCount > 0 && (
              <span
                className={`text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded ${
                  charCount > 1000
                    ? 'text-brand-rose bg-brand-rose/10'
                    : 'text-brand-muted bg-brand-line/20'
                }`}
              >
                {charCount}
              </span>
            )}
            <button
              onClick={onUploadClick}
              disabled={isSending}
              className="p-2 text-brand-muted hover:text-brand-ink hover:bg-brand-line/40 disabled:opacity-50 disabled:cursor-not-allowed transition-colors rounded"
              title="Upload document"
            >
              <Upload className="w-4 h-4" />
            </button>
            <button
              onClick={onSend}
              disabled={!inputValue.trim() || isSending}
              className="p-2 bg-brand-ink text-brand-surface hover:bg-brand-accent hover:shadow-md disabled:bg-brand-line disabled:text-brand-muted disabled:cursor-not-allowed transition-all rounded"
              title="Send message (Shift+Enter for new line)"
            >
              {isSending ? (
                <div className="w-4 h-4 border-2 border-brand-surface border-t-transparent rounded-full animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </button>
          </div>
        </div>

        {/* Info text */}
        <p className="text-center text-[10px] text-brand-muted font-mono uppercase tracking-widest">
          Clarity Legal may produce inaccurate information. Always verify citations independently.
        </p>
      </div>
    </div>
  )
}
