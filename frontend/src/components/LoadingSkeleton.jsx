import React from 'react'

export function MessageSkeleton() {
  return (
    <div className="flex justify-start mb-8 animate-fade-in">
      <div className="max-w-3xl w-full space-y-3">
        <div className="bg-brand-surface border border-brand-line p-8 relative">
          <div className="absolute top-0 left-0 w-full h-1 bg-brand-gold"></div>

          {/* Header skeleton */}
          <div className="flex items-center gap-2 mb-6 pb-4 border-b border-brand-line">
            <div className="w-4 h-4 skeleton rounded" />
            <div className="h-4 w-32 skeleton rounded" />
            <div className="h-4 w-16 skeleton rounded ml-auto" />
          </div>

          {/* Content skeleton */}
          <div className="space-y-3">
            <div className="h-4 w-full skeleton rounded" />
            <div className="h-4 w-5/6 skeleton rounded" />
            <div className="h-4 w-4/5 skeleton rounded" />
            <div className="h-4 w-3/4 skeleton rounded" />
          </div>

          {/* Sources skeleton */}
          <div className="mt-8 pt-6 border-t border-brand-line">
            <div className="h-4 w-32 skeleton rounded mb-4" />
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-8 skeleton rounded" />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export function InputSkeleton() {
  return (
    <div className="bg-brand-surface border-t border-brand-line px-8 py-4">
      <div className="max-w-4xl mx-auto">
        {/* Suggested prompts skeleton */}
        <div className="flex justify-center mb-3">
          <div className="h-4 w-24 skeleton rounded" />
        </div>

        {/* Input skeleton */}
        <div className="relative">
          <div className="w-full h-14 skeleton rounded border border-brand-line" />
        </div>

        {/* Footer skeleton */}
        <div className="mt-3">
          <div className="h-3 w-48 skeleton rounded mx-auto" />
        </div>
      </div>
    </div>
  )
}

export function ConversationListSkeleton() {
  return (
    <div className="space-y-2">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="px-4 py-2 h-10 skeleton rounded" />
      ))}
    </div>
  )
}

export default function LoadingSkeleton({ type = 'message' }) {
  switch (type) {
    case 'message':
      return <MessageSkeleton />
    case 'input':
      return <InputSkeleton />
    case 'list':
      return <ConversationListSkeleton />
    default:
      return <MessageSkeleton />
  }
}
