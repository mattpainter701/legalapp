import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { AlertCircle, ShieldAlert, Check, Copy } from 'lucide-react'
import { markdownComponents } from './legalMarkdown'

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
    <div className="rounded-2xl border border-brand-line bg-brand-surface overflow-hidden shadow-sm">
      <div className="bg-brand-ink px-6 py-4 flex items-center gap-3">
        <ShieldAlert size={18} className="text-brand-gold shrink-0" />
        <span className="text-white text-[14px] font-semibold font-sans tracking-wide">
          Attorney Work Product — Confidential
        </span>
      </div>

      {gates_triggered.length > 0 && (
        <div className="bg-brand-rose/10 border-b border-brand-rose/20 px-6 py-4 space-y-2">
          {gates_triggered.map((gate, i) => (
            <div key={i} className="flex items-start gap-2.5 text-brand-rose">
              <AlertCircle size={16} className="shrink-0 mt-0.5" />
              <p className="text-[14px] font-sans font-medium">{gate}</p>
            </div>
          ))}
        </div>
      )}

      {requires_attorney_review && (
        <div className="bg-brand-amber/10 border-b border-brand-amber/20 px-6 py-4 flex items-center gap-2.5">
          <AlertCircle size={16} className="text-brand-amber shrink-0" />
          <p className="text-brand-amber text-[14px] font-sans font-medium">
            This output requires attorney review before any action is taken.
          </p>
        </div>
      )}

      <div className="px-8 py-6 text-brand-ink">
        <ReactMarkdown components={markdownComponents}>{memo || ''}</ReactMarkdown>
      </div>

      <div className="bg-brand-bg-soft/50 border-t border-brand-line px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4 text-[12px] text-brand-muted font-sans font-medium">
          {model_used && <span>Model: {model_used}</span>}
          {tokens_used != null && <span>Tokens: {tokens_used.toLocaleString()}</span>}
        </div>
        <button
          onClick={handleCopy}
          className="flex items-center gap-2 px-4 py-2 text-[13px] font-sans font-semibold text-brand-ink bg-brand-surface border border-brand-line rounded-lg hover:bg-brand-bg-soft transition-colors shadow-sm"
        >
          {copied ? (
            <><Check size={14} className="text-brand-green" /> Copied</>
          ) : (
            <><Copy size={14} /> Copy Memo</>
          )}
        </button>
      </div>
    </div>
  )
}
