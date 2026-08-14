import React, { useState } from 'react'
import { CheckCircle2, ClipboardCheck, LoaderCircle, Mail, Pencil, Send, X } from 'lucide-react'

/**
 * Reviewable work the assistant proposed in chat.
 *
 * The card's job is to make the consequence of Approve unmistakable *before*
 * it is clicked. `approval_effect` is authored server-side for exactly that
 * reason: the chat card and the board card must never be able to describe the
 * same approval differently.
 */

function EmailDraft({ pendingAction, draft, onDraftChange, editing }) {
  if (!pendingAction || pendingAction.type !== 'email_client') return null
  const recipients = pendingAction.to || []

  return (
    <div className="mt-3 border border-brand-line bg-brand-bg/60 p-3">
      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-brand-muted">
        <Mail className="h-3 w-3" strokeWidth={2} />
        <span>Drafted email</span>
      </div>
      <dl className="mt-2 space-y-1 text-[12px] text-brand-ink">
        <div className="flex gap-2">
          <dt className="w-16 shrink-0 font-semibold text-brand-muted">To</dt>
          {/* Recipients are resolved server-side from the matter's parties and
              are deliberately not editable here — an address the attorney could
              retype in chat would bypass that check. */}
          <dd className="break-all">{recipients.join(', ')}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-16 shrink-0 font-semibold text-brand-muted">Subject</dt>
          <dd className="break-words">{pendingAction.subject}</dd>
        </div>
      </dl>
      {editing ? (
        <label className="mt-2 block">
          <span className="sr-only">Email body</span>
          <textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            rows={7}
            className="w-full resize-y border border-brand-line bg-brand-surface p-2 text-[13px] leading-relaxed text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent"
          />
        </label>
      ) : (
        <p className="mt-2 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-brand-ink-2">
          {draft}
        </p>
      )}
    </div>
  )
}

export default function ActionProposalCard({ proposal, onApprove, onDismiss }) {
  const pendingAction = proposal?.pending_action || null
  const isEmail = pendingAction?.type === 'email_client'
  const [draft, setDraft] = useState(pendingAction?.body || '')
  const [editing, setEditing] = useState(false)
  const [state, setState] = useState('proposed')
  const [error, setError] = useState(null)

  const bodyChanged = isEmail && draft !== (pendingAction?.body || '')
  const busy = state === 'approving'

  const handleApprove = async () => {
    setState('approving')
    setError(null)
    try {
      await onApprove(proposal, bodyChanged ? { body: draft } : undefined)
      setState('approved')
    } catch (err) {
      setState('proposed')
      setError(
        err?.response?.data?.detail
          || err?.message
          || 'The task could not be approved.',
      )
    }
  }

  if (state === 'dismissed') return null

  return (
    <section
      data-testid="action-proposal"
      aria-label={`Proposed work: ${proposal.title}`}
      className="mt-4 border border-brand-accent/40 bg-brand-accent/[0.04] p-3 sm:p-4"
    >
      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-brand-accent-2">
        <ClipboardCheck className="h-3.5 w-3.5" strokeWidth={2} />
        <span className="font-bold">Proposed for your approval</span>
      </div>

      <h4 className="mt-1.5 font-serif text-[15px] font-semibold text-brand-ink">
        {proposal.title}
      </h4>

      <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-brand-muted">
        <div className="flex gap-1">
          <dt className="font-semibold">Status</dt>
          <dd className="capitalize">{proposal.status || 'review'}</dd>
        </div>
        {proposal.due_date && (
          <div className="flex gap-1">
            <dt className="font-semibold">Due</dt>
            <dd>{proposal.due_date}</dd>
          </div>
        )}
      </dl>

      <EmailDraft
        pendingAction={pendingAction}
        draft={draft}
        onDraftChange={setDraft}
        editing={editing}
      />

      {/* Stated before the button, not after, so the consequence is read first. */}
      <p className="mt-3 text-[12px] leading-relaxed text-brand-ink-2">
        {proposal.approval_effect}
      </p>

      {state === 'approved' ? (
        <p
          role="status"
          className="mt-3 flex items-start gap-1.5 text-[12px] font-semibold text-brand-accent"
        >
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
          {/* Delivery runs out-of-band after the approval commits, so this must
              not claim the client was contacted — the approval is what we know
              happened. The task's automation record carries the real outcome. */}
          {isEmail
            ? 'Approved. Delivery is in progress — the task shows the outcome once it completes.'
            : 'Approved and moved into active work.'}
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleApprove}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-ink px-3 py-2 text-[12px] font-semibold text-brand-bg transition-colors hover:bg-brand-ink/90 disabled:opacity-50"
          >
            {busy ? (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
            ) : (
              <Send className="h-3.5 w-3.5" strokeWidth={2} />
            )}
            {isEmail ? 'Approve and send' : 'Approve'}
          </button>
          {isEmail && (
            <button
              type="button"
              onClick={() => setEditing(!editing)}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-brand-line px-3 py-2 text-[12px] font-semibold text-brand-ink transition-colors hover:bg-brand-bg disabled:opacity-50"
            >
              <Pencil className="h-3.5 w-3.5" strokeWidth={2} />
              {editing ? 'Done editing' : 'Edit draft'}
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setState('dismissed')
              onDismiss?.(proposal)
            }}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg px-2 py-2 text-[12px] font-semibold text-brand-muted transition-colors hover:text-brand-ink disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" strokeWidth={2} />
            Dismiss
          </button>
          <span className="text-[11px] text-brand-muted">
            Or review it on the work board.
          </span>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-2 text-[12px] font-semibold text-brand-rose">
          {error}
        </p>
      )}
    </section>
  )
}
