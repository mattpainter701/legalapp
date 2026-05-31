import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { format } from 'date-fns'

function CitationCard({ source }) {
  return (
    <div className="border border-gray-200 rounded-lg p-3 bg-gray-50 hover:bg-gray-100 transition-colors">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-[#1e3a5f] text-sm truncate">
            {source.case_name}
          </p>
          {source.citation && (
            <p className="text-[#2e4f7a] text-xs font-mono mt-0.5">{source.citation}</p>
          )}
          {source.court && (
            <p className="text-gray-500 text-xs mt-0.5">{source.court}</p>
          )}
        </div>
      </div>
      {source.excerpt && (
        <p className="mt-2 text-gray-600 text-xs italic leading-relaxed border-l-2 border-[#1e3a5f] pl-2">
          {source.excerpt}
        </p>
      )}
    </div>
  )
}

function SourcesSection({ sources }) {
  const [open, setOpen] = useState(false)

  if (!sources || sources.length === 0) return null

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-[#1e3a5f] font-sans font-medium hover:underline focus:outline-none"
      >
        <svg
          className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2.5}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
        {sources.length} {sources.length === 1 ? 'Source' : 'Sources'}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {sources.map((src, idx) => (
            <CitationCard key={idx} source={src} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function ChatMessage({ message }) {
  const isUser = message.role === 'user'
  const timestamp = message.created_at
    ? format(new Date(message.created_at), 'h:mm a')
    : ''

  if (isUser) {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[75%]">
          <div className="bg-[#1e3a5f] text-white rounded-2xl rounded-tr-sm px-4 py-3">
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{message.content}</p>
          </div>
          {timestamp && (
            <p className="text-right text-xs text-gray-400 mt-1 pr-1">{timestamp}</p>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-[80%]">
        {/* Avatar */}
        <div className="flex items-center gap-2 mb-1.5">
          <div className="w-6 h-6 bg-[#1e3a5f] rounded-full flex items-center justify-center flex-shrink-0">
            <svg
              width="12"
              height="12"
              viewBox="0 0 32 32"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z"
                fill="white"
                fillOpacity="0.9"
              />
            </svg>
          </div>
          <span className="text-xs text-gray-500 font-sans">LegalScribe AI</span>
          {timestamp && (
            <span className="text-xs text-gray-400">{timestamp}</span>
          )}
        </div>

        {/* Message bubble */}
        <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
          <div className="prose-legal text-sm">
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </div>
          <SourcesSection sources={message.sources} />
        </div>
      </div>
    </div>
  )
}
