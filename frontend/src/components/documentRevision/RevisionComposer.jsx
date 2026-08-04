import React, { useState } from 'react'
import { Send, Sparkles } from 'lucide-react'

export default function RevisionComposer({
  onSubmit,
  submitting = false,
  disabled = false,
  followUp = false,
}) {
  const [instruction, setInstruction] = useState('')
  const [modelTier, setModelTier] = useState('standard')

  const submit = async () => {
    const value = instruction.trim()
    if (!value || submitting || disabled) return
    const accepted = await onSubmit(value, modelTier)
    if (accepted !== false) setInstruction('')
  }

  return (
    <section className="rounded-2xl border border-brand-line bg-brand-surface p-3 shadow-sm sm:p-4" aria-labelledby="revision-request-title">
      <div className="mb-3 flex items-start gap-2.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-accent/10 text-brand-accent-2">
          <Sparkles size={17} aria-hidden="true" />
        </span>
        <div>
          <h2 id="revision-request-title" className="text-sm font-bold text-brand-ink">
            {followUp ? 'Request another change' : 'Tell the assistant what to change'}
          </h2>
          <p className="mt-0.5 text-xs leading-relaxed text-brand-muted">
            Be exact about names, amounts, sections, and approved firm language. You will review a new private revision before approval.
          </p>
        </div>
      </div>

      <label htmlFor="revision-instruction" className="sr-only">Document change instructions</label>
      <textarea
        id="revision-instruction"
        value={instruction}
        onChange={(event) => setInstruction(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            submit()
          }
        }}
        rows={5}
        disabled={disabled || submitting}
        placeholder="Example: Change the retainer from $2,500 to $3,000 and replace '60 days' with '30 days'."
        className="w-full resize-y rounded-xl border border-brand-line-2 bg-white px-3 py-2.5 text-[15px] leading-relaxed text-brand-ink placeholder:text-brand-muted focus:border-brand-accent focus:outline-none focus:ring-2 focus:ring-brand-accent/15 disabled:bg-brand-bg-soft disabled:opacity-70"
      />

      <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <label htmlFor="revision-model-tier" className="text-xs font-semibold text-brand-muted">Model</label>
          <select
            id="revision-model-tier"
            value={modelTier}
            onChange={(event) => setModelTier(event.target.value)}
            disabled={disabled || submitting}
            className="min-h-10 rounded-lg border border-brand-line bg-white px-2.5 text-xs font-semibold text-brand-ink focus:border-brand-accent focus:outline-none focus:ring-2 focus:ring-brand-accent/15"
          >
            <option value="standard">Standard</option>
            <option value="premium">Premium</option>
          </select>
          <span className="hidden text-[10px] text-brand-muted sm:inline">Use your phone keyboard microphone to dictate.</span>
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={!instruction.trim() || submitting || disabled}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-brand-ink px-4 text-sm font-bold text-white transition-colors hover:bg-brand-ink-2 disabled:cursor-not-allowed disabled:bg-brand-line-2 disabled:text-brand-muted"
        >
          {submitting ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" aria-hidden="true" />
          ) : (
            <Send size={16} aria-hidden="true" />
          )}
          {submitting ? 'Preparing revision…' : followUp ? 'Prepare another revision' : 'Prepare revision'}
        </button>
      </div>
    </section>
  )
}
