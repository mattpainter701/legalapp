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
    <div className="mx-auto flex min-h-full max-w-3xl flex-col justify-center py-4 text-center sm:py-12">
      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-brand-ink text-brand-bg shadow-sm sm:h-14 sm:w-14 sm:rounded-2xl">
        <Scale className="h-7 w-7" strokeWidth={1.5} />
      </div>
      <p className="mt-3 text-[10px] font-bold uppercase tracking-[0.16em] text-brand-accent-2 sm:mt-5 sm:text-[11px]">
        Research, drafting, and analysis
      </p>
      <h2 className="mt-1.5 font-serif text-xl font-semibold tracking-tight text-brand-ink sm:mt-2 sm:text-3xl">
        What do you want to move forward?
      </h2>
      <p className="mx-auto mt-2 max-w-xl text-xs leading-relaxed text-brand-ink-2 sm:mt-3 sm:text-base">
        Link a matter or attach a document for focused context, then choose a starting point or ask in your own words.
      </p>

      <div className="mt-4 grid gap-2 text-left sm:mt-7 sm:grid-cols-3 sm:gap-3">
        {STARTER_ACTIONS.map(({ icon: Icon, title, prompt }) => (
          <button
            key={title}
            type="button"
            onClick={() => onPromptSelect?.(prompt)}
            className="group flex min-h-11 items-center gap-3 rounded-xl border border-brand-line bg-brand-surface p-2.5 hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-sm sm:block sm:rounded-2xl sm:p-4"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-bg-soft text-brand-accent-2 group-hover:bg-brand-accent/10">
              <Icon size={18} />
            </span>
            <span className="block text-sm font-semibold text-brand-ink sm:mt-4">{title}</span>
            <span className="mt-1.5 hidden text-xs leading-relaxed text-brand-muted sm:block">{prompt}</span>
          </button>
        ))}
      </div>

      <div className="mt-7 hidden rounded-2xl border border-brand-line bg-brand-surface/70 p-4 text-left sm:block">
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
      className="min-h-0 flex-1 overflow-y-auto px-2 py-2 sm:px-5 sm:py-4 md:px-8 md:py-6"
      onScroll={onMessageScroll}
      aria-live={isSending ? 'polite' : 'off'}
    >
      {!messages || messages.length === 0 ? (
        <EmptyState onPromptSelect={onPromptSelect} />
      ) : isLoading ? (
        <div className="mx-auto w-full max-w-none">
          <MessageSkeleton />
          <MessageSkeleton />
        </div>
      ) : (
        <div className="mx-auto w-full max-w-none">
          <div className="hidden sm:block">
            <ReviewTagLegend compact />
          </div>
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
