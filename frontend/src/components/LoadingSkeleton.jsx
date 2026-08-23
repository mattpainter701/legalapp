
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

// Row-shaped placeholder for the list surfaces that go slow first (matters,
// tasks, contacts, matter documents). A spinner says "something is happening";
// a skeleton says what is coming and roughly how much of it, which is the
// information that matters when a query takes seconds rather than milliseconds.
export function TableSkeleton({ rows = 6, columns = 5, ariaLabel = 'Loading results' }) {
  return (
    <div role="status" aria-live="polite" aria-label={ariaLabel} className="w-full">
      <div className="space-y-2">
        {Array.from({ length: rows }, (_, rowIndex) => (
          <div
            key={rowIndex}
            className="flex items-center gap-4 rounded-lg border border-brand-line bg-brand-surface px-4 py-3"
          >
            {Array.from({ length: columns }, (_, columnIndex) => (
              <div
                key={columnIndex}
                className={`h-4 skeleton rounded ${columnIndex === 0 ? 'flex-[2]' : 'flex-1'}`}
              />
            ))}
          </div>
        ))}
      </div>
      <span className="sr-only">Loading results, please wait.</span>
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
    case 'table':
      return <TableSkeleton />
    default:
      return <MessageSkeleton />
  }
}
