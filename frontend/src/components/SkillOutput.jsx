import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'

// Citation tag definitions: pattern → { label, classes }
const CITATION_PATTERNS = [
  {
    regex: /\[settled\]/gi,
    label: 'settled',
    classes: 'bg-green-100 text-green-800',
  },
  {
    regex: /\[verify-pinpoint\]/gi,
    label: 'verify-pinpoint',
    classes: 'bg-blue-100 text-blue-800',
  },
  {
    regex: /\[verify\]/gi,
    label: 'verify',
    classes: 'bg-amber-100 text-amber-800',
  },
  {
    regex: /\[model knowledge\]/gi,
    label: 'model knowledge',
    classes: 'bg-orange-100 text-orange-800',
  },
  {
    regex: /\[UNCERTAIN:\s*([^\]]*)\]/g,
    label: null, // dynamic
    classes: 'bg-red-100 text-red-800',
    dynamic: true,
    prefix: 'UNCERTAIN: ',
  },
  {
    regex: /\[VERIFY:\s*([^\]]*)\]/g,
    label: null,
    classes: 'bg-amber-100 text-amber-800',
    dynamic: true,
    prefix: 'VERIFY: ',
  },
]

function transformCitations(text) {
  if (!text) return []

  // Split text into segments: plain text and citation tags
  // We'll process the text and return an array of React elements
  const parts = []
  let remaining = text
  let key = 0

  while (remaining.length > 0) {
    let earliest = null
    let earliestIndex = Infinity
    let earliestPattern = null
    let earliestMatch = null

    for (const pattern of CITATION_PATTERNS) {
      // Reset lastIndex for global regexes
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
      // No more citations
      parts.push(<span key={key++}>{remaining}</span>)
      break
    }

    // Text before citation
    if (earliestIndex > 0) {
      parts.push(<span key={key++}>{remaining.slice(0, earliestIndex)}</span>)
    }

    // The citation badge
    const label = earliestPattern.dynamic
      ? earliestPattern.prefix + (earliestMatch[1] || '')
      : earliestPattern.label

    parts.push(
      <span
        key={key++}
        className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium mx-0.5 ${earliestPattern.classes}`}
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
    return <p className="mb-2 leading-relaxed">{transformCitations(children)}</p>
  }
  if (Array.isArray(children)) {
    const transformed = children.flatMap((child, i) => {
      if (typeof child === 'string') {
        return transformCitations(child)
      }
      return [React.cloneElement(child, { key: i })]
    })
    return <p className="mb-2 leading-relaxed">{transformed}</p>
  }
  return <p className="mb-2 leading-relaxed">{children}</p>
}

const markdownComponents = {
  p: ({ children }) => <CitationParagraph>{children}</CitationParagraph>,
  h1: ({ children }) => (
    <h1 className="text-xl font-serif font-bold text-[#1e3a5f] mt-4 mb-2">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-lg font-serif font-semibold text-[#1e3a5f] mt-4 mb-2">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-base font-serif font-semibold text-[#1e3a5f] mt-3 mb-1">{children}</h3>
  ),
  ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside mb-2 space-y-1">{children}</ol>,
  li: ({ children }) => <li className="text-sm leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-gray-900">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-[#1e3a5f] pl-4 italic text-gray-600 my-3">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-3">
      <table className="min-w-full text-sm border-collapse border border-gray-200">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-[#1e3a5f] text-white">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-gray-200">{children}</tbody>,
  tr: ({ children }) => <tr className="even:bg-gray-50">{children}</tr>,
  th: ({ children }) => (
    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide">{children}</th>
  ),
  td: ({ children }) => <td className="px-3 py-2 text-gray-700">{children}</td>,
  code: ({ children, inline }) =>
    inline ? (
      <code className="bg-gray-100 text-[#1e3a5f] px-1 py-0.5 rounded text-xs font-mono">
        {children}
      </code>
    ) : (
      <pre className="bg-gray-100 rounded p-3 overflow-x-auto text-xs font-mono my-2">
        <code>{children}</code>
      </pre>
    ),
  hr: () => <hr className="border-gray-200 my-4" />,
}

export default function SkillOutput({ result }) {
  const [copied, setCopied] = useState(false)

  if (!result) return null

  const {
    memo,
    gates_triggered = [],
    requires_attorney_review = false,
    tokens_used,
    model_used,
  } = result

  const handleCopy = () => {
    if (memo) {
      navigator.clipboard.writeText(memo).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      })
    }
  }

  return (
    <div className="rounded-xl border border-gray-200 overflow-hidden shadow-sm">
      {/* Work-product header */}
      <div className="bg-[#1e3a5f] px-4 py-3 flex items-center gap-2">
        <svg
          className="w-4 h-4 text-white flex-shrink-0"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
          />
        </svg>
        <span className="text-white text-sm font-semibold font-sans">
          Attorney Work Product — Confidential
        </span>
      </div>

      {/* Gates */}
      {gates_triggered.length > 0 && (
        <div className="bg-red-50 border-b border-red-200 p-4 space-y-2">
          {gates_triggered.map((gate, i) => (
            <div key={i} className="flex items-start gap-2 text-red-800">
              <svg
                className="w-4 h-4 flex-shrink-0 mt-0.5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
              <p className="text-sm font-sans font-medium">{gate}</p>
            </div>
          ))}
        </div>
      )}

      {/* Attorney review banner */}
      {requires_attorney_review && (
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-3 flex items-center gap-2">
          <svg
            className="w-4 h-4 text-amber-700 flex-shrink-0"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
          <p className="text-amber-800 text-sm font-sans font-medium">
            This output requires attorney review before any action is taken.
          </p>
        </div>
      )}

      {/* Memo content */}
      <div className="bg-white px-6 py-5">
        <div className="text-sm text-gray-800 leading-relaxed">
          <ReactMarkdown components={markdownComponents}>{memo || ''}</ReactMarkdown>
        </div>
      </div>

      {/* Footer */}
      <div className="bg-gray-50 border-t border-gray-200 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4 text-xs text-gray-400 font-sans">
          {model_used && <span>Model: {model_used}</span>}
          {tokens_used != null && <span>Tokens: {tokens_used.toLocaleString()}</span>}
        </div>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium text-[#1e3a5f] border border-[#1e3a5f] rounded-lg hover:bg-blue-50 transition-colors"
        >
          {copied ? (
            <>
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              Copied
            </>
          ) : (
            <>
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                />
              </svg>
              Copy memo
            </>
          )}
        </button>
      </div>
    </div>
  )
}
