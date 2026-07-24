import React, { useEffect, useRef } from 'react'
import { FileSearch, ListTree, PenLine, Scale, Sparkles } from 'lucide-react'
import ChatMessage from './ChatMessage'
import { MessageSkeleton } from './LoadingSkeleton'
import { ReviewTagLegend } from './legalMarkdown'

const STARTER_ACTIONS = [
  {
    icon: FileSearch,
    title: 'Review a source',
    prompt: 'Summarize the key issues, authorities, and open questions in the available sources.',
  },
  {
    icon: PenLine,
    title: 'Draft work product',
    prompt: 'Draft a client-ready follow-up that explains the next steps and questions we still need answered.',
  },
  {
    icon: ListTree,
    title: 'Build a chronology',
    prompt: 'Create a dated chronology from the available matter context and documents.',
  },
]

function EmptyState({ onPromptSelect }) {
  return (
    <div className="mx-auto flex min-h-full max-w-3xl flex-col justify-center py-8 text-center sm:py-12">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-ink text-brand-bg shadow-sm">
        <Scale className="h-7 w-7" strokeWidth={1.5} />
      </div>
      <p className="mt-5 text-[11px] font-bold uppercase tracking-[0.16em] text-brand-accent-2">
        Research, drafting, and analysis
      </p>
      <h2 className="mt-2 font-serif text-2xl font-semibold tracking-tight text-brand-ink sm:text-3xl">
        What do you want to move forward?
      </h2>
      <p className="mx-auto mt-3 max-w-xl text-sm leading-relaxed text-brand-ink-2 sm:text-base">
        Link a matter or attach a document for focused context, then choose a starting point or ask in your own words.
      </p>

      <div className="mt-7 grid gap-3 text-left sm:grid-cols-3">
        {STARTER_ACTIONS.map(({ icon: Icon, title, prompt }) => (
          <button
            key={title}
            type="button"
            onClick={() => onPromptSelect?.(prompt)}
            className="group rounded-2xl border border-brand-line bg-brand-surface p-4 hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-sm"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-bg-soft text-brand-accent-2 group-hover:bg-brand-accent/10">
              <Icon size={18} />
            </span>
            <span className="mt-4 block text-sm font-semibold text-brand-ink">{title}</span>
            <span className="mt-1.5 block text-xs leading-relaxed text-brand-muted">{prompt}</span>
          </button>
        ))}
      </div>

      <div className="mt-7 rounded-2xl border border-brand-line bg-brand-surface/70 p-4 text-left">
        <div className="flex items-start gap-3">
          <Sparkles size={17} className="mt-0.5 shrink-0 text-brand-accent-2" />
          <div>
            <p className="text-sm font-semibold text-brand-ink">Answers show their working context</p>
            <p className="mt-1 text-xs leading-relaxed text-brand-muted">
              Retrieved sources, review tags, and matter context stay visible so the result can be checked before use.
            </p>
          </div>
        </div>
        <div className="mt-3 border-t border-brand-line pt-3">
          <ReviewTagLegend compact />
        </div>
      </div>
    </div>
  )
}

export default function Messages({
  messages,
  isLoading,
  isSending,
  onMessageScroll,
  onPromptSelect,
}) {
  const messagesEndRef = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isSending])

  return (
    <div
      className="flex-1 overflow-y-auto px-3 py-4 sm:px-5 md:px-8 md:py-6"
      onScroll={onMessageScroll}
      aria-live={isSending ? 'polite' : 'off'}
    >
      {!messages || messages.length === 0 ? (
        <EmptyState onPromptSelect={onPromptSelect} />
      ) : isLoading ? (
        <div className="mx-auto max-w-4xl">
          <MessageSkeleton />
          <MessageSkeleton />
        </div>
      ) : (
        <div className="mx-auto max-w-4xl">
          <ReviewTagLegend compact />
          {messages.map((message, index) => (
            <div
              key={message.id}
              className="animate-fade-in"
              style={{ animationDelay: `${Math.min(index * 35, 210)}ms` }}
            >
              <ChatMessage message={message} />
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
      )}
    </div>
  )
}
