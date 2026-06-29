import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { format } from 'date-fns'
import {
  Book,
  Scale,
  Copy,
  Check,
  ExternalLink,
  Search,
  BookOpen,
  PenLine,
  FileText,
  FolderSearch,
} from 'lucide-react'
import { markdownComponents } from './legalMarkdown'

function cleanSourceText(value) {
  if (!value) return ''
  const textarea = typeof document !== 'undefined' ? document.createElement('textarea') : null
  const stripped = String(value).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
  if (!textarea) return stripped
  textarea.innerHTML = stripped
  return textarea.value
}

function sourceHref(src) {
  const url = cleanSourceText(src?.url)
  if (url?.startsWith('http://') || url?.startsWith('https://')) return url
  const citation = cleanSourceText(src?.citation)
  if (citation.startsWith('http://') || citation.startsWith('https://')) return citation
  return ''
}

function sourceBadge(src) {
  const type = src?.source_type || ''
  const label = src?.source_label || 'Context'
  if (type === 'public_authority') {
    return { label, classes: 'bg-brand-green/10 text-brand-green border-brand-green/20' }
  }
  if (type === 'cloud') {
    return { label, classes: 'bg-brand-accent/10 text-brand-accent-2 border-brand-accent/20' }
  }
  if (type === 'matter_context') {
    return { label, classes: 'bg-brand-gold/10 text-brand-gold border-brand-gold/20' }
  }
  if (type === 'tenant_document') {
    return { label, classes: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20' }
  }
  return { label, classes: 'bg-brand-line/40 text-brand-muted border-brand-line' }
}

function SourcesLedger({ sources }) {
  if (!sources || sources.length === 0) return null

  const cols = 'grid-cols-[30px_minmax(150px,2fr)_minmax(120px,140px)_minmax(120px,1.5fr)]'

  return (
    <div className="mt-10 pt-6 border-t-[3px] border-brand-ink">
      <h4 className="font-mono text-xs font-bold uppercase tracking-widest text-brand-ink mb-4 flex items-center gap-2">
        <Book className="w-4 h-4" /> Authorities Referenced
      </h4>

      <div className="w-full text-left text-sm border border-brand-line bg-brand-bg">
        {/* Header */}
        <div className={`grid ${cols} gap-2 p-2 border-b border-brand-line bg-brand-surface-2 text-xs font-mono text-brand-muted uppercase tracking-wider`}>
          <div className="text-center">#</div>
          <div>Authority</div>
          <div>Citation</div>
          <div>Court</div>
        </div>

        {/* Rows */}
        <div className="divide-y divide-brand-line">
          {sources.map((src, idx) => {
            const citation = cleanSourceText(src.citation)
            const caseName = cleanSourceText(src.case_name) || 'Unknown authority'
            const court = cleanSourceText(src.court)
            const excerpt = cleanSourceText(src.excerpt)
            const href = sourceHref(src)
            const badge = sourceBadge(src)

            return (
              <div
                key={idx}
                className={`flex flex-col ${idx % 2 === 1 ? 'bg-brand-surface' : ''}`}
              >
                <div className={`grid ${cols} gap-2 p-3 items-center`}>
                  <div className="text-center font-mono text-brand-muted text-xs">
                    {String(idx + 1).padStart(2, '0')}
                  </div>
                  <div className="min-w-0">
                    <div className="font-serif font-bold text-brand-ink truncate" title={caseName}>
                      {caseName}
                    </div>
                    <span className={`mt-1 inline-flex w-fit items-center px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest font-mono border ${badge.classes}`}>
                      {badge.label}
                    </span>
                  </div>
                  <div className="min-w-0">
                    {citation ? (
                      href ? (
                        <a
                          href={href}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono text-xs bg-brand-line/30 px-1.5 py-0.5 inline-flex items-center gap-1 max-w-full text-brand-accent-2 hover:text-brand-ink underline decoration-brand-line-2 underline-offset-2"
                          title={citation}
                        >
                          <span className="truncate">{citation}</span>
                          <ExternalLink size={11} className="shrink-0" />
                        </a>
                      ) : (
                        <span className="font-mono text-xs bg-brand-line/30 px-1.5 py-0.5 inline-block truncate max-w-full" title={citation}>
                          {citation}
                        </span>
                      )
                    ) : href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono text-xs text-brand-accent-2 hover:text-brand-ink underline decoration-brand-line-2 underline-offset-2 inline-flex items-center gap-1"
                      >
                        View <ExternalLink size={11} />
                      </a>
                    ) : (
                      <span className="text-brand-muted text-xs">—</span>
                    )}
                  </div>
                  <div className="text-xs text-brand-ink-2 truncate" title={court}>
                    {court || '—'}
                  </div>
                </div>
                {excerpt && (
                  <div className="pl-[38px] pr-4 pb-3">
                    <p className="font-serif italic text-sm text-brand-ink-2 border-l-[3px] border-brand-line-2 pl-3 ml-2 bg-brand-surface p-2">
                      "{excerpt}"
                    </p>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function formatCount(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0
}

function AssistantWorkingState({ progress, compact = false }) {
  const counts = progress?.counts || {}
  const matterCount = formatCount(counts.matter)
  const uploadCount = formatCount(counts.uploads)
  const firmCount = formatCount(counts.firm)
  const courtlistenerCount = formatCount(counts.courtlistener)
  const localCount = matterCount + uploadCount + firmCount
  const focusTerms = Array.isArray(progress?.keyphrases)
    ? progress.keyphrases.filter(Boolean).slice(0, 4)
    : []
  const status = progress?.status || 'Retrieving context and preparing a cited response'
  const sourceTiles = [
    { icon: Scale, label: 'Matter', value: matterCount },
    { icon: FileText, label: 'Uploads', value: uploadCount },
    { icon: FolderSearch, label: 'Firm/cloud', value: firmCount },
    { icon: BookOpen, label: 'CourtListener', value: courtlistenerCount },
  ]
  const steps = [
    {
      icon: Search,
      label: localCount > 0 ? `${localCount} local source${localCount === 1 ? '' : 's'} found` : 'Searching local sources',
    },
    {
      icon: BookOpen,
      label: courtlistenerCount > 0 ? `${courtlistenerCount} CourtListener source${courtlistenerCount === 1 ? '' : 's'} found` : 'Checking CourtListener authority',
    },
    {
      icon: PenLine,
      label: focusTerms.length ? `Streaming answer focus: ${focusTerms.join(', ')}` : status,
    },
  ]

  if (compact) {
    return (
      <div className="mb-4 border border-brand-line bg-brand-bg px-3 py-2 text-xs text-brand-ink-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono font-bold uppercase tracking-widest text-brand-muted">
            {status}
          </span>
          <span className="text-brand-line-2">|</span>
          <span>{localCount} local</span>
          <span>{courtlistenerCount} CourtListener</span>
          {focusTerms.length > 0 && (
            <span className="min-w-0 truncate text-brand-muted">
              Focus: {focusTerms.join(', ')}
            </span>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="border border-brand-line bg-brand-bg p-4">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center border border-brand-line bg-brand-surface-2">
          <Scale className="h-4 w-4 text-brand-gold" strokeWidth={2} />
        </div>
        <div className="min-w-0 text-left">
          <p className="font-sans text-sm font-semibold text-brand-ink">Clarity Legal is working</p>
          <p className="text-xs text-brand-muted">{status}</p>
        </div>
        <div className="ml-auto flex items-center gap-1.5" aria-label="Working">
          <span className="h-1.5 w-1.5 animate-bounce bg-brand-muted" style={{ animationDelay: '0ms' }} />
          <span className="h-1.5 w-1.5 animate-bounce bg-brand-muted" style={{ animationDelay: '150ms' }} />
          <span className="h-1.5 w-1.5 animate-bounce bg-brand-muted" style={{ animationDelay: '300ms' }} />
        </div>
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-4">
        {sourceTiles.map(({ icon: Icon, label, value }) => (
          <div key={label} className="flex items-center justify-between gap-2 border border-brand-line bg-brand-surface px-3 py-2 text-xs">
            <span className="flex min-w-0 items-center gap-2 text-brand-ink-2">
              <Icon className="h-3.5 w-3.5 shrink-0 text-brand-muted" strokeWidth={2} />
              <span className="truncate">{label}</span>
            </span>
            <span className="font-mono font-bold text-brand-ink">{value}</span>
          </div>
        ))}
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {steps.map(({ icon: Icon, label }) => (
          <div key={label} className="flex items-center gap-2 border border-brand-line bg-brand-surface px-3 py-2 text-xs text-brand-ink-2">
            <Icon className="h-3.5 w-3.5 text-brand-muted" strokeWidth={2} />
            <span className="min-w-0 truncate" title={label}>{label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function ChatMessage({ message }) {
  const isUser = message.role === 'user'
  const content = message.content || ''
  const hasAssistantContent = content.trim().length > 0
  const timestamp = message.created_at
    ? format(new Date(message.created_at), 'h:mm a')
    : ''
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (isUser) {
    return (
      <div className="flex justify-end mb-7 group">
        <div className="bg-brand-ink text-brand-bg p-4 max-w-2xl border-l-4 border-brand-accent shadow-sm hover:shadow-md transition-shadow">
          <div className="mb-2 flex items-center gap-2 text-[11px] font-mono uppercase tracking-widest text-brand-bg/55">
            <span className="font-bold text-brand-accent">You</span>
            {timestamp && (
              <>
                <span className="text-brand-bg/30">·</span>
                <span>{timestamp}</span>
              </>
            )}
            <button
              onClick={handleCopy}
              className="ml-auto flex items-center gap-1 text-brand-bg/40 opacity-0 transition-opacity hover:text-brand-bg/85 group-hover:opacity-100"
              title="Copy query"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              <span className="sr-only">{copied ? 'Copied' : 'Copy query'}</span>
            </button>
          </div>
          <p className="text-base leading-relaxed font-sans whitespace-pre-wrap">{content}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start mb-8 group">
      <div className="bg-brand-surface border border-brand-line p-8 max-w-3xl w-full shadow-sm hover:shadow-md transition-shadow relative">
        {/* Gold top bar */}
        <div className="absolute top-0 left-0 w-full h-1 bg-brand-gold"></div>

        {/* Header */}
        <div className="flex items-center gap-2 mb-6 text-xs font-mono text-brand-muted uppercase tracking-wider border-b border-brand-line pb-4">
          <Scale className="w-4 h-4 text-brand-gold" strokeWidth={2} />
          <span className="font-bold text-brand-ink">Clarity Legal Analysis</span>
          {timestamp && <span className="ml-auto">{timestamp}</span>}
          <button
            onClick={handleCopy}
            className="ml-2 text-brand-muted hover:text-brand-ink opacity-0 group-hover:opacity-100 transition-opacity"
            title="Copy response"
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
        </div>

        {/* Body */}
        <div className="text-brand-ink text-[15px]">
          {hasAssistantContent ? (
            <>
              {message.progress && !message.progress.complete && (
                <AssistantWorkingState progress={message.progress} compact />
              )}
              <ReactMarkdown components={markdownComponents}>{content}</ReactMarkdown>
            </>
          ) : (
            <AssistantWorkingState progress={message.progress} />
          )}
        </div>

        {hasAssistantContent && <SourcesLedger sources={message.sources} />}
      </div>
    </div>
  )
}
