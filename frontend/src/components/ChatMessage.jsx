import React, { useRef, useState } from 'react'
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
  CheckCircle2,
  LoaderCircle,
  ShieldCheck,
  Clock3,
  ChevronDown,
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
  if (url?.startsWith('/')) return `https://www.courtlistener.com${url}`
  const citation = cleanSourceText(src?.citation)
  if (citation.startsWith('http://') || citation.startsWith('https://')) return citation
  return ''
}

export function sourceAnchor(messageId, index) {
  const safeMessageId = String(messageId || 'message').replace(/[^A-Za-z0-9_-]/g, '-')
  return `source-${safeMessageId}-${index + 1}`
}

export function linkSourceReferences(text, sources, messageId) {
  if (!text) return ''
  const sourceList = Array.isArray(sources) ? sources : []
  const sourceIndexes = new Map()
  sourceList.forEach((source, index) => {
    const id = String(source?.source_id || source?.id || '').trim().toLowerCase()
    if (id && !sourceIndexes.has(id)) sourceIndexes.set(id, { source, index })
  })

  return String(text).replace(/\[source:\s*([^\]]+)\]/gi, (match, rawId) => {
    const entry = sourceIndexes.get(String(rawId || '').trim().toLowerCase())
    if (!entry) return '**[source]**'
    const label = `[${entry.index + 1}]`
    return `[${label}](#${sourceAnchor(messageId, entry.index)})`
  })
}

function splitMarkdownSections(markdown) {
  const intro = []
  const sections = []
  let activeSection = null

  for (const line of String(markdown || '').split('\n')) {
    const match = line.match(/^#{2,6}\s+(.+)$/)
    if (match) {
      activeSection = { title: match[1], body: [] }
      sections.push(activeSection)
    } else if (activeSection) {
      activeSection.body.push(line)
    } else {
      intro.push(line)
    }
  }

  return { intro: intro.join('\n').trim(), sections }
}

function readableSectionTitle(value) {
  return String(value || '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_`]/g, '')
    .trim()
}

function CollapsibleMarkdown({ content }) {
  const { intro, sections } = splitMarkdownSections(content)
  const [collapsed, setCollapsed] = useState({})
  const hasOpenSections = sections.some((_, index) => !collapsed[index])

  const setAllSections = (nextCollapsed) => {
    setCollapsed(Object.fromEntries(sections.map((_, index) => [index, nextCollapsed])))
  }

  return (
    <>
      {intro && <ReactMarkdown components={markdownComponents}>{intro}</ReactMarkdown>}
      {sections.length > 0 && (
        <div className="mb-3 flex justify-end">
          <button
            type="button"
            data-copy-exclude="true"
            onClick={() => setAllSections(hasOpenSections)}
            className="inline-flex items-center gap-1 border border-brand-line bg-brand-bg px-2 py-1 text-[10px] font-mono font-semibold uppercase tracking-wider text-brand-ink-2 hover:bg-brand-bg-soft"
          >
            {hasOpenSections ? 'Collapse sections' : 'Expand sections'}
          </button>
        </div>
      )}
      {sections.map((section, index) => {
        const isOpen = !collapsed[index]
        const title = readableSectionTitle(section.title)
        return (
          <section key={`${title}-${index}`} className="border-t border-brand-line first:border-t-0">
            <button
              type="button"
              data-copy-heading="true"
              aria-expanded={isOpen}
              onClick={() => setCollapsed((current) => ({ ...current, [index]: isOpen }))}
              className="flex w-full items-center gap-2 py-4 text-left font-sans text-sm font-bold uppercase tracking-widest text-brand-muted hover:text-brand-ink"
            >
              <ChevronDown className={`h-4 w-4 shrink-0 transition-transform ${isOpen ? '' : '-rotate-90'}`} aria-hidden="true" />
              <span>{title}</span>
            </button>
            <div className="pb-2" hidden={!isOpen}>
              <ReactMarkdown components={markdownComponents}>{section.body.join('\n').trim()}</ReactMarkdown>
            </div>
          </section>
        )
      })}
    </>
  )
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

function SourcesLedger({ sources, messageId }) {
  if (!sources || sources.length === 0) return null

  const cols = 'sm:grid-cols-[30px_minmax(0,2fr)_minmax(0,1.3fr)_minmax(0,1fr)]'
  const publicAuthorityCount = sources.filter((src) => src?.source_type === 'public_authority').length
  const heading = publicAuthorityCount > 0 ? 'Authorities Referenced' : 'Sources & References'

  return (
    <div className="mt-5 border-t-[3px] border-brand-ink pt-4 sm:mt-10 sm:pt-6">
      <h4 className="mb-3 flex items-center gap-2 font-mono text-xs font-bold uppercase tracking-widest text-brand-ink sm:mb-4">
        <Book className="w-4 h-4" /> {heading}
      </h4>

      <div className="w-full overflow-hidden border border-brand-line bg-brand-bg text-left text-sm sm:overflow-x-auto">
        {/* Header */}
        <div className={`hidden min-w-[620px] ${cols} gap-2 border-b border-brand-line bg-brand-surface-2 p-2 font-mono text-xs uppercase tracking-wider text-brand-muted sm:grid`}>
          <div className="text-center">#</div>
          <div>Source</div>
          <div>Reference</div>
          <div>Origin</div>
        </div>

        {/* Rows */}
        <div className="divide-y divide-brand-line">
          {sources.map((src, idx) => {
            const citation = cleanSourceText(src.citation)
            const caseName = cleanSourceText(src.case_name) || 'Unknown source'
            const court = cleanSourceText(src.court)
            const excerpt = cleanSourceText(src.excerpt)
            const href = sourceHref(src)
            const badge = sourceBadge(src)
            const locator = cleanSourceText(src.locator)
            const anchor = sourceAnchor(messageId, idx)

            return (
              <div
                key={idx}
                id={anchor}
                tabIndex={-1}
                className={`scroll-mt-24 flex flex-col outline-none transition target:bg-brand-gold/10 target:ring-2 target:ring-inset target:ring-brand-gold ${idx % 2 === 1 ? 'bg-brand-surface' : ''}`}
              >
                <div className={`grid min-w-0 grid-cols-1 ${cols} items-center gap-2 p-3 sm:min-w-[620px]`}>
                  <div className="hidden text-center font-mono text-xs text-brand-muted sm:block">
                    {String(idx + 1).padStart(2, '0')}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate font-serif font-bold text-brand-ink" title={caseName}>
                      {caseName}
                    </div>
                    <span className={`mt-1 inline-flex w-fit items-center px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest font-mono border ${badge.classes}`}>
                      {badge.label}
                    </span>
                    {locator && (
                      <a
                        href={`#${anchor}`}
                        className="ml-2 mt-1 inline-flex w-fit font-mono text-[10px] text-brand-accent-2 underline decoration-brand-line-2 underline-offset-2"
                        aria-label={`Link to ${locator}`}
                      >
                        {locator}
                      </a>
                    )}
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
                  <div className="truncate text-xs text-brand-ink-2" title={court}>
                    {court || '—'}
                  </div>
                </div>
                {excerpt && (
                  <div className="px-3 pb-3 sm:pl-[38px] sm:pr-4">
                    <details className="sm:hidden">
                      <summary className="cursor-pointer text-xs font-semibold text-brand-accent-2">
                        View source excerpt
                      </summary>
                      <p className="mt-2 max-w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere] border-l-[3px] border-brand-line-2 bg-brand-surface p-2 pl-3 font-serif text-xs italic text-brand-ink-2">
                        "{excerpt}"
                      </p>
                    </details>
                    <p className="hidden max-w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere] border-l-[3px] border-brand-line-2 bg-brand-surface p-2 pl-3 font-serif text-sm italic text-brand-ink-2 sm:ml-2 sm:block">
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

function countSourcesByType(sources) {
  const counts = {
    matter: 0,
    uploads: 0,
    firm: 0,
    courtlistener: 0,
    total: 0,
  }

  for (const src of Array.isArray(sources) ? sources : []) {
    const type = src?.source_type || ''
    if (type === 'public_authority') {
      counts.courtlistener += 1
    } else if (type === 'matter_context') {
      counts.matter += 1
    } else {
      counts.firm += 1
    }
  }
  counts.total = counts.matter + counts.uploads + counts.firm + counts.courtlistener
  return counts
}

function ReferenceTrail({ referenceContext, sources, variant = 'assistant' }) {
  const sourceCounts = countSourcesByType(sources)
  const contextCounts = referenceContext?.counts || {}
  const counts = {
    matter: formatCount(contextCounts.matter ?? sourceCounts.matter),
    uploads: formatCount(contextCounts.uploads ?? sourceCounts.uploads),
    firm: formatCount(contextCounts.firm ?? sourceCounts.firm),
    courtlistener: formatCount(contextCounts.courtlistener ?? sourceCounts.courtlistener),
  }
  counts.total = formatCount(
    contextCounts.total ??
    (counts.matter + counts.uploads + counts.firm + counts.courtlistener)
  )
  const sourceCount = formatCount(referenceContext?.source_count ?? (Array.isArray(sources) ? sources.length : 0))
  const status = referenceContext?.status || (sourceCount ? 'Sources attached to answer' : '')
  const hasAny = counts.total > 0 || sourceCount > 0 || status
  if (!hasAny) return null

  const chips = [
    { icon: Scale, label: 'Matter', value: counts.matter },
    { icon: FileText, label: 'Uploads', value: counts.uploads },
    { icon: FolderSearch, label: 'Firm/cloud', value: counts.firm },
    { icon: BookOpen, label: 'Authority', value: counts.courtlistener },
  ].filter((chip) => chip.value > 0)

  const isUser = variant === 'user'
  const boxClasses = isUser
    ? 'border-brand-bg/15 bg-brand-bg/10 text-brand-bg/75'
    : 'border-brand-line bg-brand-bg text-brand-ink-2'
  const labelClasses = isUser ? 'text-brand-bg/55' : 'text-brand-muted'
  const valueClasses = isUser ? 'text-brand-bg' : 'text-brand-ink'
  const chipClasses = isUser ? 'border-brand-bg/15 bg-brand-ink/30' : 'border-brand-line bg-brand-surface'

  return (
    <div className={`mt-3 min-w-0 border px-3 py-2 text-xs ${boxClasses}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`font-mono font-bold uppercase tracking-widest ${labelClasses}`}>
          References
        </span>
        {sourceCount > 0 && (
          <span className={`font-mono font-bold ${valueClasses}`}>
            {sourceCount} cited
          </span>
        )}
        {chips.map(({ icon: Icon, label, value }) => (
          <span key={label} className={`inline-flex items-center gap-1 border px-2 py-1 ${chipClasses}`}>
            <Icon className="h-3 w-3" strokeWidth={2} />
            <span>{label}</span>
            <span className={`font-mono font-bold ${valueClasses}`}>{value}</span>
          </span>
        ))}
        {status && (
          <span className="hidden min-w-0 max-w-full break-words [overflow-wrap:anywhere] sm:inline" title={status}>
            {status}
          </span>
        )}
      </div>
    </div>
  )
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
    { icon: BookOpen, label: 'Public authority', value: courtlistenerCount },
  ]
  const activities = Array.isArray(progress?.activities) ? progress.activities : []
  const fallbackActivities = [
    { id: 'firm_search', state: 'started', label: 'Searching firm knowledge' },
    ...(courtlistenerCount || progress?.event === 'retrieving'
      ? [{ id: 'public_authority', state: 'started', label: 'Checking public authority' }]
      : []),
  ]
  const timeline = activities.length ? activities : fallbackActivities
  const currentActivity = [...timeline].reverse().find((item) => ['started', 'progress'].includes(item.state))
    || timeline[timeline.length - 1]
  const sourcePreviews = []
  const seenSourceIds = new Set()
  for (const activity of timeline) {
    for (const source of activity.sources || []) {
      const key = source.source_id || `${source.case_name}-${source.citation}`
      if (!seenSourceIds.has(key)) {
        seenSourceIds.add(key)
        sourcePreviews.push(source)
      }
    }
  }
  const activityIcons = {
    understanding: Search,
    working_context: Scale,
    firm_search: FolderSearch,
    public_authority: BookOpen,
    drafting: PenLine,
    citation_check: ShieldCheck,
  }
  const formatElapsed = (milliseconds) => {
    const value = Number(milliseconds)
    if (!Number.isFinite(value)) return ''
    return value < 1000 ? `${Math.max(0, Math.round(value))}ms` : `${(value / 1000).toFixed(1)}s`
  }

  if (compact) {
    return (
      <div className="mb-4 border border-brand-line bg-brand-bg px-3 py-2 text-xs text-brand-ink-2">
        <div className="flex flex-wrap items-center gap-2">
          <CheckCircle2 className="h-3.5 w-3.5 text-brand-green" aria-hidden="true" />
          <span className="font-semibold text-brand-ink">{currentActivity?.label || status}</span>
          <span className="ml-auto font-mono text-brand-muted">{counts.total || localCount + courtlistenerCount} sources</span>
          {focusTerms.length > 0 && (
            <span className="hidden min-w-0 truncate text-brand-muted sm:inline">
              Focus: {focusTerms.join(', ')}
            </span>
          )}
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="border border-brand-line bg-brand-bg px-3 py-2 text-xs text-brand-ink-2">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 animate-pulse rounded-full bg-brand-accent" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate">{currentActivity?.label || status}</span>
          <span className="shrink-0 font-mono text-brand-muted">
            {counts.total || localCount + courtlistenerCount} sources
          </span>
        </div>
        {currentActivity?.detail && (
          <p className="mt-1 truncate pl-4 text-[10px] text-brand-muted">{currentActivity.detail}</p>
        )}
      </div>
      <div className="hidden border border-brand-line bg-brand-bg p-4">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center border border-brand-line bg-brand-surface-2">
          <Scale className="h-4 w-4 text-brand-gold" strokeWidth={2} />
        </div>
        <div className="min-w-0 text-left">
          <p className="font-sans text-sm font-semibold text-brand-ink">LawHand is working</p>
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
      <div className="mt-3 overflow-hidden border border-brand-line bg-brand-surface">
        {timeline.map((activity, index) => {
          const Icon = activityIcons[activity.id] || Search
          const isActive = ['started', 'progress'].includes(activity.state)
          const isComplete = activity.state === 'completed'
          return (
            <div key={activity.id} className={`flex items-start gap-3 px-3 py-2.5 ${index ? 'border-t border-brand-line' : ''}`}>
              <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center border ${isActive ? 'border-brand-accent/30 bg-brand-accent/10 text-brand-accent-2' : 'border-brand-line bg-brand-bg text-brand-muted'}`}>
                {isComplete ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-brand-green" aria-label="Completed" />
                ) : isActive ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-label="In progress" />
                ) : (
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-semibold text-brand-ink">{activity.label}</span>
                {activity.detail && <span className="mt-0.5 block truncate text-[10px] text-brand-muted">{activity.detail}</span>}
              </span>
              {activity.elapsed_ms != null && (
                <span className="inline-flex shrink-0 items-center gap-1 font-mono text-[10px] text-brand-muted">
                  <Clock3 className="h-3 w-3" /> {formatElapsed(activity.elapsed_ms)}
                </span>
              )}
            </div>
          )
        })}
      </div>
      {sourcePreviews.length > 0 && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2" aria-label="Sources found">
          {sourcePreviews.slice(0, 4).map((source) => (
            <div key={source.source_id || source.case_name} className="animate-fade-in border border-brand-line bg-brand-surface px-3 py-2">
              <div className="flex items-center gap-2">
                <BookOpen className="h-3.5 w-3.5 shrink-0 text-brand-gold" />
                <span className="min-w-0 flex-1 truncate text-xs font-semibold text-brand-ink">{source.case_name}</span>
                <span className="shrink-0 font-mono text-[9px] uppercase text-brand-muted">{source.source_label}</span>
              </div>
              {(source.citation || source.locator) && (
                <p className="mt-1 truncate pl-5 font-mono text-[10px] text-brand-muted">
                  {[source.citation, source.locator].filter(Boolean).join(' · ')}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
      </div>
    </>
  )
}

export default function ChatMessage({ message }) {
  const isUser = message.role === 'user'
  const content = message.content || ''
  const renderedContent = linkSourceReferences(content, message.sources, message.id)
  const hasAssistantContent = content.trim().length > 0
  const timestamp = message.created_at
    ? format(new Date(message.created_at), 'h:mm a')
    : ''
  const [copied, setCopied] = useState(false)
  const responseCopyRef = useRef(null)

  const handleCopy = async () => {
    const copyTarget = isUser ? null : responseCopyRef.current
    const copyContents = copyTarget?.cloneNode(true)
    copyContents?.querySelectorAll('[data-copy-exclude]').forEach((element) => {
      element.remove()
    })
    copyContents?.querySelectorAll('[data-copy-heading]').forEach((element) => {
      const heading = document.createElement('h2')
      heading.textContent = element.textContent
      element.replaceWith(heading)
    })
    copyContents?.querySelectorAll('[hidden]').forEach((element) => {
      element.hidden = false
    })
    const plainText = copyContents?.textContent?.trim() || content

    const ClipboardItemConstructor = window.ClipboardItem
    if (copyTarget && navigator.clipboard?.write && ClipboardItemConstructor) {
      const html = copyContents?.innerHTML || copyTarget.innerHTML
      await navigator.clipboard.write([
        new ClipboardItemConstructor({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([plainText], { type: 'text/plain' }),
        }),
      ])
    } else {
      await navigator.clipboard.writeText(plainText)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (isUser) {
    return (
      <div className="group mb-4 flex justify-end sm:mb-7">
        <div className="max-w-2xl border-l-4 border-brand-accent bg-brand-ink p-3 text-brand-bg shadow-sm transition-shadow hover:shadow-md sm:p-4">
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
              className="ml-auto flex items-center gap-1 text-brand-bg/55 opacity-100 transition-opacity hover:text-brand-bg/85 sm:opacity-0 sm:group-hover:opacity-100"
              title="Copy query"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              <span className="sr-only">{copied ? 'Copied' : 'Copy query'}</span>
            </button>
          </div>
          <p className="text-base leading-relaxed font-sans whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{content}</p>
          <div className="hidden sm:block">
            <ReferenceTrail
              referenceContext={message.referenceContext}
              sources={message.sources}
              variant="user"
            />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="group mb-4 flex justify-start sm:mb-8">
      <div data-testid="assistant-response" className="relative w-full max-w-none border border-brand-line bg-brand-surface p-4 shadow-sm transition-shadow hover:shadow-md sm:p-8">
        {/* Gold top bar */}
        <div className="absolute top-0 left-0 w-full h-1 bg-brand-gold"></div>

        {/* Header */}
        <div className="mb-3 flex items-center gap-2 border-b border-brand-line pb-2 font-mono text-[10px] uppercase tracking-wider text-brand-muted sm:mb-6 sm:pb-4 sm:text-xs">
          <Scale className="w-4 h-4 text-brand-gold" strokeWidth={2} />
          <span className="font-bold text-brand-ink">LawHand Analysis</span>
          {timestamp && <span className="ml-auto">{timestamp}</span>}
          <button
            onClick={handleCopy}
            className="ml-2 text-brand-muted opacity-100 transition-opacity hover:text-brand-ink sm:opacity-0 sm:group-hover:opacity-100"
            title="Copy formatted response"
            aria-label="Copy formatted response"
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
        </div>

        {/* Body */}
        <div className="min-w-0 break-words text-[14px] text-brand-ink [overflow-wrap:anywhere] sm:text-[15px]">
          <div ref={responseCopyRef}>
            {hasAssistantContent ? (
              <>
              {message.progress && !message.progress.complete && (
                <AssistantWorkingState progress={message.progress} compact />
              )}
                <CollapsibleMarkdown content={renderedContent} />
              </>
            ) : (
              <AssistantWorkingState progress={message.progress} />
            )}
            {hasAssistantContent && <SourcesLedger sources={message.sources} messageId={message.id} />}
          </div>
          {hasAssistantContent && (
            <ReferenceTrail
              referenceContext={message.referenceContext}
              sources={message.sources}
            />
          )}
        </div>
      </div>
    </div>
  )
}
