import React from 'react'

export const REVIEW_TAGS = [
  {
    label: 'settled',
    text: 'Well-established',
    classes: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    swatch: 'bg-brand-green',
  },
  {
    label: 'verify',
    text: 'Confirm before relying',
    classes: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    swatch: 'bg-brand-amber',
  },
  {
    label: 'model',
    text: 'General reasoning',
    classes: 'bg-brand-gold/10 text-brand-gold border-brand-gold/20',
    swatch: 'bg-brand-gold',
  },
]

// Citation tag definitions: pattern → { label, classes }
const CITATION_PATTERNS = [
  {
    regex: /\[settled\]/gi,
    label: 'settled',
    classes: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  },
  {
    regex: /\[verify-pinpoint\]/gi,
    label: 'verify-pinpoint',
    classes: 'bg-brand-accent/10 text-brand-accent-2 border-brand-accent/20',
  },
  {
    regex: /\[verify\]/gi,
    label: 'verify',
    classes: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  },
  {
    regex: /\[model knowledge\]/gi,
    label: 'model knowledge',
    classes: 'bg-brand-gold/10 text-brand-gold border-brand-gold/20',
  },
  {
    regex: /\[model reasoning\]/gi,
    label: 'model reasoning',
    classes: 'bg-brand-accent/10 text-brand-accent-2 border-brand-accent/20',
  },
  {
    regex: /\[well[-\s]known fact\]/gi,
    label: 'well known fact',
    classes: 'bg-brand-gold/10 text-brand-gold border-brand-gold/20',
  },
  {
    regex: /\[cited by context\]/gi,
    label: 'cited by context',
    classes: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  },
  {
    regex: /\[cited by context:\s*([^\]]*)\]/gi,
    label: null,
    classes: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    dynamic: true,
    prefix: 'cited by context: ',
  },
  {
    regex: /\[firm context\]/gi,
    label: 'firm context',
    classes: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  },
  {
    regex: /\[UNCERTAIN:\s*([^\]]*)\]/g,
    label: null, // dynamic
    classes: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    dynamic: true,
    prefix: 'UNCERTAIN: ',
  },
  {
    regex: /\[VERIFY:\s*([^\]]*)\]/g,
    label: null,
    classes: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    dynamic: true,
    prefix: 'VERIFY: ',
  },
]

export function ReviewTagLegend({ compact = false }) {
  if (compact) {
    return (
      <div className="mx-auto mb-3 flex w-fit max-w-full flex-wrap items-center justify-center gap-x-4 gap-y-2 border border-brand-line bg-brand-surface/95 px-3 py-2 text-[11px] shadow-sm">
        <span className="font-mono uppercase tracking-widest text-brand-muted">Tag legend:</span>
        {REVIEW_TAGS.map(({ label, text, swatch }) => (
          <span key={label} className="inline-flex items-center gap-1.5 whitespace-nowrap font-sans text-brand-ink">
            <span className={`h-2 w-2 ${swatch}`} aria-hidden="true" />
            <span className="font-bold uppercase tracking-wide">{label}</span>
            <span className="text-brand-muted">({text})</span>
          </span>
        ))}
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center justify-center gap-2">
      {REVIEW_TAGS.map(({ label, text, classes }) => (
        <div key={label} className="flex items-center gap-2 px-3 py-1.5 bg-brand-surface border border-brand-line">
          <span className={`text-[9px] font-bold uppercase tracking-widest font-mono px-1.5 py-0.5 border ${classes}`}>
            {label}
          </span>
          <span className="text-[12px] font-sans text-brand-ink-2">{text}</span>
        </div>
      ))}
    </div>
  )
}

export function transformCitations(text) {
  if (!text) return []

  const parts = []
  let remaining = text
  let key = 0

  while (remaining.length > 0) {
    let earliest = null
    let earliestIndex = Infinity
    let earliestPattern = null
    let earliestMatch = null

    for (const pattern of CITATION_PATTERNS) {
      const r = new RegExp(pattern.regex.source, pattern.regex.flags)
      const m = r.exec(remaining)
      if (m && m.index < earliestIndex) {
        earliest = m[0]
        earliestIndex = m.index
        earliestPattern = pattern
        earliestMatch = m
      }
    }

    if (earliest === null) {
      parts.push(<span key={key++}>{remaining}</span>)
      break
    }

    if (earliestIndex > 0) {
      parts.push(<span key={key++}>{remaining.slice(0, earliestIndex)}</span>)
    }

    const label = earliestPattern.dynamic
      ? earliestPattern.prefix + (earliestMatch[1] || '')
      : earliestPattern.label

    parts.push(
      <span
        key={key++}
        className={`inline-flex items-center px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest font-mono border mx-0.5 align-middle ${earliestPattern.classes}`}
      >
        {label}
      </span>
    )

    remaining = remaining.slice(earliestIndex + earliest.length)
  }

  return parts
}

function CitationParagraph({ children }) {
  if (typeof children === 'string') {
    return <p className="mb-4 leading-relaxed font-sans">{transformCitations(children)}</p>
  }
  if (Array.isArray(children)) {
    const transformed = []
    children.forEach((child, i) => {
      if (typeof child === 'string') {
        transformCitations(child).forEach((node, j) => {
          transformed.push(React.cloneElement(node, { key: `${i}-${j}` }))
        })
      } else {
        transformed.push(React.cloneElement(child, { key: `el-${i}` }))
      }
    })
    return <p className="mb-4 leading-relaxed font-sans">{transformed}</p>
  }
  return <p className="mb-4 leading-relaxed font-sans">{children}</p>
}

export const markdownComponents = {
  p: ({ children }) => <CitationParagraph>{children}</CitationParagraph>,
  h1: ({ children }) => (
    <h1 className="font-serif text-2xl font-semibold text-brand-ink mt-6 mb-4 leading-snug">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="font-sans text-sm font-bold uppercase tracking-widest text-brand-muted mt-8 mb-4 border-b border-brand-line pb-2">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="font-sans text-sm font-bold uppercase tracking-widest text-brand-muted mt-8 mb-4 border-b border-brand-line pb-2">{children}</h3>
  ),
  ul: ({ children }) => <ul className="list-disc pl-5 mb-4 space-y-1.5 text-brand-ink font-sans marker:text-brand-line-2">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5 mb-4 space-y-1.5 text-brand-ink font-sans marker:font-mono marker:text-brand-muted">{children}</ol>,
  li: ({ children }) => <li className="text-[15px] leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-brand-ink">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  a: ({ children, href }) => {
    const isInternalSource = String(href || '').startsWith('#source-')
    return (
      <a
        href={href}
        target={isInternalSource ? undefined : '_blank'}
        rel={isInternalSource ? undefined : 'noreferrer'}
        className="font-semibold text-brand-accent-2 underline decoration-brand-line-2 underline-offset-2 hover:text-brand-ink"
      >
        {children}
      </a>
    )
  },
  blockquote: ({ children }) => (
    <blockquote className="border-l-[3px] border-brand-line-2 pl-4 italic font-serif text-brand-ink-2 my-4 py-1 bg-brand-surface-2">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-4 border border-brand-line">
      <table className="min-w-full text-sm border-collapse bg-brand-bg">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-brand-surface-2 border-b border-brand-line">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-brand-line">{children}</tbody>,
  tr: ({ children }) => <tr className="hover:bg-brand-surface transition-colors">{children}</tr>,
  th: ({ children }) => (
    <th className="px-4 py-3 text-left text-[11px] font-bold text-brand-muted uppercase tracking-widest font-mono">{children}</th>
  ),
  td: ({ children }) => <td className="px-4 py-3 text-[14px] text-brand-ink font-sans">{children}</td>,
  code: ({ children, inline }) =>
    inline ? (
      <code className="bg-brand-line/30 text-brand-accent-2 px-1.5 py-0.5 text-[13px] font-mono border border-brand-line">
        {children}
      </code>
    ) : (
      <pre className="bg-brand-surface-2 p-4 overflow-x-auto text-[13px] font-mono my-4 text-brand-ink border border-brand-line">
        <code>{children}</code>
      </pre>
    ),
  hr: () => <hr className="border-brand-line my-6" />,
}
