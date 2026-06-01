import React, { useEffect, useRef } from 'react'
import { Scale } from 'lucide-react'
import ChatMessage from './ChatMessage'

function TypingIndicator() {
  return (
    <div className="flex justify-start mb-8 animate-fade-in">
      <div className="bg-brand-surface border border-brand-line p-8 max-w-3xl w-full shadow-sm relative">
        <div className="absolute top-0 left-0 w-full h-1 bg-brand-gold"></div>
        <div className="flex items-center gap-2 text-xs font-mono text-brand-muted uppercase tracking-wider">
          <Scale className="w-4 h-4 text-brand-gold" strokeWidth={2} />
          <span className="font-bold text-brand-ink">Clarity Legal Analysis</span>
          <span className="ml-auto flex gap-1.5 items-center">
            <span className="w-1.5 h-1.5 bg-brand-muted animate-bounce" style={{ animationDelay: '0ms' }} />
            <span className="w-1.5 h-1.5 bg-brand-muted animate-bounce" style={{ animationDelay: '150ms' }} />
            <span className="w-1.5 h-1.5 bg-brand-muted animate-bounce" style={{ animationDelay: '300ms' }} />
          </span>
        </div>
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center min-h-full text-center max-w-2xl mx-auto py-10">
      <div className="w-16 h-16 bg-brand-ink flex items-center justify-center mb-6 relative shadow-sm">
        <Scale className="w-8 h-8 text-brand-bg" strokeWidth={1.5} />
        <div className="absolute top-0 left-0 w-full h-1 bg-brand-gold"></div>
      </div>
      <div className="text-xs font-mono text-brand-muted uppercase tracking-widest mb-3">
        Clarity Legal · Case Ledger
      </div>
      <h3 className="font-serif text-3xl font-semibold text-brand-ink mb-4 tracking-tight">
        Your legal-safe AI coworker
      </h3>
      <p className="text-brand-ink-2 text-base max-w-lg leading-relaxed font-sans mb-8">
        Clarity Legal researches, drafts, and analyzes alongside you — grounded in your firm's documents and public case law, with every answer cited and ready for attorney review.
      </p>

      {/* Trust signals */}
      <div className="grid sm:grid-cols-3 gap-3 w-full mb-8">
        {[
          { icon: '🛡️', title: 'Grounded answers', text: 'Drawn from your documents + public case law' },
          { icon: '✓', title: 'Cited & verifiable', text: 'Every claim tagged by confidence level' },
          { icon: '⚖️', title: 'Attorney-reviewed', text: 'Work product gated for sign-off' },
        ].map(({ icon, title, text }) => (
          <div
            key={title}
            className="flex flex-col items-center text-center gap-2 p-4 bg-brand-surface border border-brand-line relative hover:border-brand-accent transition-colors"
          >
            <div className="absolute top-0 left-0 w-full h-px bg-brand-gold/60"></div>
            <div className="text-2xl">{icon}</div>
            <p className="text-[13px] font-sans font-bold text-brand-ink">{title}</p>
            <p className="text-[12px] font-sans text-brand-ink-2 leading-snug">{text}</p>
          </div>
        ))}
      </div>

      {/* Citation legend */}
      <div className="mt-8 w-full">
        <p className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-3 font-mono">
          How answers are tagged
        </p>
        <div className="flex flex-wrap items-center justify-center gap-2">
          {[
            { label: 'settled', text: 'Well-established law', classes: 'bg-brand-green/10 text-brand-green border-brand-green/20' },
            { label: 'verify', text: 'Confirm before relying', classes: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20' },
            { label: 'model knowledge', text: 'General reasoning, not a source', classes: 'bg-brand-gold/10 text-brand-gold border-brand-gold/20' },
          ].map(({ label, text, classes }) => (
            <div key={label} className="flex items-center gap-2 px-3 py-1.5 bg-brand-surface border border-brand-line">
              <span className={`text-[9px] font-bold uppercase tracking-widest font-mono px-1.5 py-0.5 border ${classes}`}>
                {label}
              </span>
              <span className="text-[12px] font-sans text-brand-ink-2">{text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function Messages({ messages, isLoading, isSending, onMessageScroll }) {
  const messagesEndRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, isSending])

  return (
    <div className="flex-1 overflow-y-auto px-8 py-6" onScroll={onMessageScroll}>
      {!messages || messages.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          {isLoading ? (
            <div className="flex justify-center py-12">
              <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
            </div>
          ) : (
            <div className="max-w-4xl mx-auto">
              {messages.map((msg, idx) => (
                <div
                  key={msg.id}
                  className="animate-fade-in"
                  style={{ animationDelay: `${idx * 50}ms` }}
                >
                  <ChatMessage message={msg} />
                </div>
              ))}
              {isSending && <TypingIndicator />}
              <div ref={messagesEndRef} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
