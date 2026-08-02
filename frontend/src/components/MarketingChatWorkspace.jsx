import React from 'react'
import {
  Book,
  Briefcase,
  ChevronDown,
  FileText,
  MessageSquare,
  MoreVertical,
  Paperclip,
  Plus,
  Scale,
  Send,
  Settings2,
  ShieldCheck,
} from 'lucide-react'

const PREVIEW_CONVERSATIONS = [
  ['Settlement position comparison', 'Active'],
  ['Demand response outline', 'Yesterday'],
  ['Damages chronology', 'Jul 31'],
]

const PREVIEW_SOURCES = [
  { number: '01', name: 'Demand letter', type: 'Matter context', reference: 'p. 4', origin: 'Matter file' },
  { number: '02', name: 'Mediator brief', type: 'Matter context', reference: 'p. 7', origin: 'Matter file' },
  { number: '03', name: 'Draft term sheet', type: 'Upload', reference: 'Sections 2-5', origin: 'Conversation' },
]

function AssistantRail() {
  return (
    <aside className="hidden min-w-0 flex-col border-r border-brand-line bg-brand-surface-2 sm:flex">
      <div className="border-b border-brand-line px-3 py-3.5">
        <p className="text-[7px] font-bold uppercase tracking-[0.16em] text-brand-muted">Workspace</p>
        <p className="mt-0.5 font-serif text-[13px] font-semibold text-brand-ink">Assistant</p>
      </div>
      <div className="border-b border-brand-line p-2.5">
        <div className="flex min-h-8 items-center justify-between rounded-xl bg-brand-ink px-2.5 text-[9px] font-semibold text-white">
          <span className="flex items-center gap-1.5"><Plus size={10} /> New conversation</span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-1 border-b border-brand-line p-2">
        <span className="flex items-center justify-center gap-1 rounded-lg bg-brand-surface px-1 py-2 text-[7px] font-semibold text-brand-ink shadow-sm">
          <MessageSquare size={9} /> Conversations <span className="font-mono text-brand-accent-2">3</span>
        </span>
        <span className="flex items-center justify-center gap-1 rounded-lg px-1 py-2 text-[7px] font-semibold text-brand-muted">
          <FileText size={9} /> Sources <span className="font-mono">12</span>
        </span>
      </div>
      <div className="space-y-1.5 p-2">
        {PREVIEW_CONVERSATIONS.map(([title, meta], index) => (
          <div
            key={title}
            className={`border-l-2 px-2 py-2 ${index === 0 ? 'border-brand-accent bg-brand-surface shadow-sm' : 'border-transparent'}`}
          >
            <p className="line-clamp-2 text-[8px] font-semibold leading-snug text-brand-ink">{title}</p>
            <p className="mt-1 font-mono text-[6.5px] uppercase tracking-wide text-brand-muted">{meta}</p>
          </div>
        ))}
      </div>
    </aside>
  )
}

function ConversationHeader({ compact }) {
  return (
    <>
      <div className="flex min-h-12 items-center justify-between gap-2 border-b border-brand-line bg-brand-surface px-3 py-2">
        <div className="min-w-0">
          <p className="text-[7px] font-bold uppercase tracking-[0.16em] text-brand-muted">AI assistant / Conversation 01</p>
          <p className="mt-0.5 truncate font-serif text-[12px] font-semibold text-brand-ink sm:text-[13px]">Settlement position comparison</p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {!compact && (
            <span className="hidden items-center gap-1 rounded-full border border-brand-accent/20 bg-brand-accent/10 px-2 py-1 text-[7px] font-semibold text-brand-accent-2 md:flex">
              <ShieldCheck size={9} /> Review required
            </span>
          )}
          <span className="inline-flex items-center gap-1 rounded-xl border border-brand-line bg-brand-surface px-2 py-1.5 text-[7px] font-semibold text-brand-ink">
            <Settings2 size={10} /> Standard <ChevronDown size={8} />
          </span>
          <MoreVertical size={12} className="text-brand-muted" />
        </div>
      </div>

      <div className="px-2.5 pt-2.5">
        <div className="flex min-h-10 items-center gap-2 rounded-2xl border border-brand-line bg-brand-surface/95 px-2 py-1.5 shadow-sm">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl bg-brand-accent/10 text-brand-accent-2">
            <Briefcase size={12} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[6.5px] font-bold uppercase tracking-[0.14em] text-brand-muted">Working context</p>
            <p className="mt-0.5 truncate text-[9px] font-semibold text-brand-ink">
              Rivera v. Northwind <span className="ml-1 font-normal text-brand-muted">2026-CV-01482</span>
            </p>
          </div>
          <span className="rounded-xl border border-brand-line bg-brand-surface px-2 py-1 text-[7px] font-semibold text-brand-ink">Change</span>
        </div>
      </div>
    </>
  )
}

function ReferenceSummary() {
  return (
    <div className="mt-3 border border-brand-line bg-brand-bg px-2.5 py-2 text-[7px] text-brand-ink-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono font-bold uppercase tracking-widest text-brand-muted">References</span>
        <span className="font-mono font-bold text-brand-ink">3 cited</span>
        <span className="inline-flex items-center gap-1 border border-brand-line bg-brand-surface px-1.5 py-1">
          <Scale size={8} /> Matter <strong className="font-mono">2</strong>
        </span>
        <span className="inline-flex items-center gap-1 border border-brand-line bg-brand-surface px-1.5 py-1">
          <FileText size={8} /> Uploads <strong className="font-mono">1</strong>
        </span>
      </div>
    </div>
  )
}

function SourcesLedger() {
  return (
    <div className="mt-4 border-t-[3px] border-brand-ink pt-3">
      <p className="mb-2 flex items-center gap-1.5 font-mono text-[7px] font-bold uppercase tracking-widest text-brand-ink">
        <Book size={10} /> Sources &amp; References
      </p>
      <div className="overflow-hidden border border-brand-line bg-brand-bg">
        <div className="grid grid-cols-[22px_minmax(0,1.6fr)_minmax(0,.7fr)_minmax(0,.9fr)] gap-1 border-b border-brand-line bg-brand-surface-2 px-2 py-1.5 font-mono text-[6px] uppercase tracking-wider text-brand-muted">
          <span>#</span><span>Source</span><span>Reference</span><span>Origin</span>
        </div>
        <div className="divide-y divide-brand-line">
          {PREVIEW_SOURCES.map((source) => (
            <div key={source.number} className="grid grid-cols-[22px_minmax(0,1.6fr)_minmax(0,.7fr)_minmax(0,.9fr)] items-center gap-1 px-2 py-2 text-[7px]">
              <span className="font-mono text-brand-muted">{source.number}</span>
              <span className="min-w-0">
                <span className="block truncate font-serif font-bold text-brand-ink">{source.name}</span>
                <span
                  className={`mt-0.5 inline-flex px-1 py-0.5 font-mono text-[5.5px] font-bold uppercase tracking-wide ${
                    source.type === 'Upload'
                      ? 'border border-brand-amber/20 bg-brand-amber/10 text-brand-amber'
                      : 'border border-brand-gold/20 bg-brand-gold/10 text-brand-gold'
                  }`}
                >
                  {source.type}
                </span>
              </span>
              <span className="truncate font-mono text-brand-ink-2">{source.reference}</span>
              <span className="truncate text-brand-muted">{source.origin}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Transcript({ compact }) {
  return (
    <div className="flex-1 px-2.5 py-3">
      <div className="mx-auto max-w-xl">
        <div className="mb-3 flex justify-end">
          <div className="max-w-[88%] border-l-4 border-brand-accent bg-brand-ink p-3 text-brand-bg shadow-sm">
            <p className="mb-1.5 font-mono text-[6.5px] font-bold uppercase tracking-widest text-brand-accent">You</p>
            <p className="text-[9px] leading-relaxed sm:text-[9.5px]">
              Compare the settlement positions and identify the issues that still need attorney review.
            </p>
          </div>
        </div>

        <div className="relative border border-brand-line bg-brand-surface p-3.5 shadow-sm">
          <div className="absolute left-0 top-0 h-1 w-full bg-brand-gold" />
          <div className="mb-2.5 flex items-center gap-1.5 border-b border-brand-line pb-2 font-mono text-[6.5px] uppercase tracking-wider text-brand-muted">
            <Scale size={10} className="text-brand-gold" />
            <span className="font-bold text-brand-ink">LawHand Analysis</span>
          </div>
          <p className="text-[9px] leading-relaxed text-brand-ink sm:text-[9.5px]">
            The current positions align on a 30-day payment window and mutual confidentiality <span className="font-mono font-bold text-brand-accent-2">[1][2]</span>. Attorney review is still needed for the release carve-outs, tax allocation, and whether non-disparagement is mutual <span className="font-mono font-bold text-brand-accent-2">[1][2][3]</span>.
          </p>
          <ReferenceSummary />
          {!compact && <SourcesLedger />}
        </div>
      </div>
    </div>
  )
}

function Composer({ compact }) {
  return (
    <div className="border-t border-brand-line bg-brand-surface/95 px-2.5 pb-2 pt-2.5">
      <div className="mx-auto max-w-xl rounded-2xl border border-brand-line-2 bg-brand-surface p-1.5 shadow-sm">
        <p className="min-h-8 px-2 py-1.5 text-[8px] text-brand-muted">Ask about a matter, draft, document, or legal issue...</p>
        <div className="flex items-center justify-between border-t border-brand-line/70 px-1 pt-1.5">
          <span className="inline-flex items-center gap-1 px-1.5 text-[7px] font-semibold text-brand-muted"><Paperclip size={10} /> Attach</span>
          <span className="inline-flex items-center gap-2">
            {!compact && <span className="hidden text-[6px] text-brand-muted md:inline">Enter to send</span>}
            <span className="inline-flex items-center gap-1 rounded-xl bg-brand-ink px-2 py-1.5 text-[7px] font-semibold text-white"><Send size={9} /> Send</span>
          </span>
        </div>
      </div>
      {!compact && (
        <p className="mt-1.5 text-center text-[6px] leading-relaxed text-brand-muted">
          Verify cited authority, dates, and legal conclusions before relying on assistant work.
        </p>
      )}
    </div>
  )
}

export default function MarketingChatWorkspace({ compact = false }) {
  return (
    <div>
      <div
        role="region"
        aria-label={compact ? 'Compact LawHand Assistant workspace preview' : 'LawHand Assistant workspace preview'}
        className={`overflow-hidden border border-brand-line bg-brand-bg shadow-2xl ${compact ? 'rounded-2xl' : 'rounded-3xl'}`}
      >
        <div className={compact ? 'grid grid-cols-1' : 'grid grid-cols-1 sm:grid-cols-[154px_minmax(0,1fr)]'}>
          {!compact && <AssistantRail />}
          <div className="flex min-w-0 flex-col bg-brand-bg">
            <ConversationHeader compact={compact} />
            <Transcript compact={compact} />
            <Composer compact={compact} />
          </div>
        </div>
      </div>
      {!compact && (
        <p className="mt-3 text-center text-[10px] leading-relaxed text-brand-muted">
          Illustrative matter and response shown in the current LawHand Assistant workspace pattern.
        </p>
      )}
    </div>
  )
}
