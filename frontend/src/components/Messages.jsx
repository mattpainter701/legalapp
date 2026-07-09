import React, { useEffect, useRef } from 'react'
import { Scale } from 'lucide-react'
import ChatMessage from './ChatMessage'
import { MessageSkeleton } from './LoadingSkeleton'
import { ReviewTagLegend } from './legalMarkdown'

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
        Your legal research coworker
      </h3>
      <p className="text-brand-ink-2 text-base max-w-lg leading-relaxed font-sans mb-8">
        Clarity Legal researches, drafts, and analyzes alongside you using firm documents, matter context, uploaded files, cloud sources, and public case law when available. Legal answers include source references and review tags so attorneys can verify before relying.
      </p>

      {/* Trust signals */}
      <div className="grid sm:grid-cols-3 gap-3 w-full mb-8">
        {[
          { icon: '🛡️', title: 'Grounded research', text: 'Uses firm materials, matter context, uploads, cloud files, and public case law when available' },
          { icon: '✓', title: 'Cited where sourced', text: 'Authorities and retrieved materials are shown when used' },
          { icon: '⚖️', title: 'Attorney review ready', text: 'Drafts and analysis are prepared for attorney verification before reliance' },
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
        <ReviewTagLegend />
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
            <div className="max-w-4xl mx-auto">
              <MessageSkeleton />
              <MessageSkeleton />
            </div>
          ) : (
            <div className="max-w-4xl mx-auto">
              <ReviewTagLegend compact />
              {messages.map((msg, idx) => (
                <div
                  key={msg.id}
                  className="animate-fade-in"
                  style={{ animationDelay: `${idx * 50}ms` }}
                >
                  <ChatMessage message={msg} />
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
