import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { format } from 'date-fns'
import { Book, Scale, Copy, Check, ExternalLink } from 'lucide-react'
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

export default function ChatMessage({ message }) {
  const isUser = message.role === 'user'
  const timestamp = message.created_at
    ? format(new Date(message.created_at), 'h:mm a')
    : ''
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (isUser) {
    return (
      <div className="flex justify-end mb-8 group">
        <div className="bg-brand-ink text-brand-bg p-5 max-w-2xl border-l-4 border-brand-accent shadow-sm hover:shadow-md transition-shadow">
          <div className="flex items-center gap-2 mb-2 text-xs font-mono text-brand-bg/60 uppercase tracking-wider">
            <span className="font-bold text-brand-accent">Q</span>
            <span>Query</span>
            {timestamp && <span className="ml-auto">{timestamp}</span>}
          </div>
          <p className="text-base leading-relaxed font-sans whitespace-pre-wrap">{message.content}</p>
          <button
            onClick={handleCopy}
            className="mt-2 text-brand-bg/40 hover:text-brand-bg/80 opacity-0 group-hover:opacity-100 transition-opacity text-xs flex items-center gap-1"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? 'Copied' : 'Copy'}
          </button>
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
          <ReactMarkdown components={markdownComponents}>{message.content}</ReactMarkdown>
        </div>

        <SourcesLedger sources={message.sources} />
      </div>
    </div>
  )
}
