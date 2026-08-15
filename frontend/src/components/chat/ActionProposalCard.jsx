import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ClipboardCheck, FileText, LoaderCircle, Mail, Pencil, Send, X } from 'lucide-react'
import { API_BASE_URL } from '../../api'

// The backend emits document links as origin-relative `/api/...`; re-base them
// so a deployment serving the API from another host still resolves them.
const sourceUrl = (url) => {
  const value = String(url || '').trim()
  if (value.startsWith('/api/')) {
    return API_BASE_URL === '/api' ? value : `${API_BASE_URL}${value.slice('/api'.length)}`
  }
  return /^https?:\/\//i.test(value) ? value : ''
}

/**
 * Reviewable work the assistant proposed in chat.
 *
 * The card's job is to make the consequence of Approve unmistakable *before*
 * it is clicked. `approval_effect` is authored server-side for exactly that
 * reason: the chat card and the board card must never be able to describe the
 * same approval differently.
 */

function EmailDraft({ pendingAction, draft, onDraftChange, editing, immutable = false }) {
  if (!pendingAction || pendingAction.type !== 'email_client') return null
  const recipients = pendingAction.to || []

  return (
    <div className="mt-3 border border-brand-line bg-brand-bg/60 p-3">
      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-brand-muted">
        <Mail className="h-3 w-3" strokeWidth={2} />
        <span>{immutable ? 'Recorded delivery payload' : 'Drafted email'}</span>
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

/**
 * Report what is actually known about delivery.
 *
 * Every branch here is careful about one thing: only `sent` may claim the client
 * was contacted. "Approved" and "sending" are not that, and an unknown outcome
 * says so rather than defaulting to reassurance.
 */
function DeliveryStatus({ isEmail, delivery, polling = false }) {
  if (!isEmail) {
    return (
      <p role="status" className="mt-3 flex items-center gap-1.5 text-[12px] font-semibold text-brand-accent">
        <CheckCircle2 className="h-4 w-4" strokeWidth={2} />
        Approved and moved into active work.
      </p>
    )
  }

  const status = delivery?.status
  const certainty = delivery?.delivery_certainty
    || (status === 'sent' ? 'confirmed_sent' : status === 'failed' ? 'outcome_unknown' : null)
  if (status === 'sent') {
    return (
      <p role="status" className="mt-3 flex items-start gap-1.5 text-[12px] font-semibold text-brand-accent">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
        Sent to the client.
      </p>
    )
  }
  if (status === 'failed') {
    if (certainty === 'not_attempted') {
      return (
        <div role="alert" className="mt-3 flex items-start gap-1.5 text-[12px] font-semibold text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
          <span>
            Email was not sent. {delivery?.error_message || 'Delivery stopped before a provider attempt.'}
            {' '}Resolve the issue, then this exact reviewed draft can be approved again.
          </span>
        </div>
      )
    }
    return (
      <div role="alert" className="mt-3 flex items-start gap-1.5 text-[12px] font-semibold text-brand-rose">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
        <span>
          Delivery was not confirmed. {delivery?.error_message || 'The provider did not confirm delivery.'}
          {' '}Check the connected mailbox&apos;s Sent Items before retrying from
          the work board; an ambiguous retry can send a duplicate.
        </span>
      </div>
    )
  }
  // queued, sending, or we stopped polling. Say what is true: approved, outcome
  // not yet known. Never imply the client has it.
  return (
    <p role="status" className="mt-3 flex items-start gap-1.5 text-[12px] font-semibold text-brand-ink-2">
      {polling ? (
        <LoaderCircle className="mt-0.5 h-4 w-4 shrink-0 animate-spin" strokeWidth={2} />
      ) : (
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
      )}
      Approved. Not yet confirmed sent — the work board shows the delivery
      outcome.
    </p>
  )
}


/**
 * Documents the draft was based on.
 *
 * Server-resolved, so every chip links to a document that exists in this tenant
 * — an attorney can open the source and check the draft against it before
 * approving, which is the whole point of showing them.
 */
function SourceChips({ sources }) {
  if (!sources || sources.length === 0) return null
  return (
    <div className="mt-3">
      <p className="font-mono text-[10px] uppercase tracking-wider text-brand-muted">
        Based on
      </p>
      <ul className="mt-1 flex flex-wrap gap-1.5">
        {sources.map((source) => (
          <li key={source.source_id}>
            {sourceUrl(source.url) ? (
              <a
                href={sourceUrl(source.url)}
                target="_blank"
                rel="noreferrer"
                title={[source.citation, source.locator].filter(Boolean).join(' | ') || source.label}
                className="inline-flex max-w-[15rem] items-center gap-1 rounded-full border border-brand-line bg-brand-surface px-2 py-0.5 text-[11px] text-brand-ink-2 hover:border-brand-accent hover:text-brand-ink"
              >
                <FileText className="h-3 w-3 shrink-0" strokeWidth={2} />
                <span className="truncate">{source.label}</span>
              </a>
            ) : (
              <span className="inline-flex max-w-[15rem] items-center gap-1 rounded-full border border-brand-line bg-brand-surface px-2 py-0.5 text-[11px] text-brand-muted">
                <FileText className="h-3 w-3 shrink-0" strokeWidth={2} />
                <span className="truncate">{source.label}</span>
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function PriorDeliveryAttempts({ history }) {
  const prior = Array.isArray(history) ? history.slice(1) : []
  if (prior.length === 0) return null
  return (
    <section aria-label="Prior delivery attempts" className="mt-3 border-t border-brand-line pt-3">
      <p className="font-mono text-[10px] uppercase tracking-wider text-brand-muted">
        Prior delivery attempts
      </p>
      <div className="mt-2 space-y-2">
        {prior.map((attempt, index) => {
          const snapshot = attempt.action_snapshot || null
          return (
            <details key={attempt.id || `${attempt.created_at || 'attempt'}-${index}`} className="rounded-lg border border-brand-line bg-brand-surface p-2">
              <summary className="cursor-pointer text-[11px] font-semibold text-brand-ink">
                Attempt {prior.length - index}: {attempt.status || 'unknown'}
                {attempt.delivery_certainty ? ` (${attempt.delivery_certainty.replaceAll('_', ' ')})` : ''}
              </summary>
              <DeliveryStatus isEmail delivery={attempt} />
              <EmailDraft
                pendingAction={snapshot}
                draft={snapshot?.body || ''}
                editing={false}
                immutable
              />
              <SourceChips sources={snapshot?.sources || []} />
              {(attempt.provider || attempt.provider_message_id || attempt.delivery_detail) && (
                <dl className="mt-2 space-y-1 text-[11px] text-brand-muted">
                  {attempt.provider && <div><dt className="inline font-semibold">Provider: </dt><dd className="inline">{attempt.provider}</dd></div>}
                  {attempt.provider_message_id && <div><dt className="inline font-semibold">Message ID: </dt><dd className="inline break-all">{attempt.provider_message_id}</dd></div>}
                  {attempt.delivery_detail && <div><dt className="inline font-semibold">Detail: </dt><dd className="inline">{attempt.delivery_detail}</dd></div>}
                </dl>
              )}
            </details>
          )
        })}
      </div>
    </section>
  )
}

const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key)
const KNOWN_DELIVERY_STATUSES = new Set(['queued', 'sending', 'sent', 'failed'])

function immutableDeliveryActionFor(snapshot, delivery) {
  const action = delivery?.action_snapshot
  if (
    !KNOWN_DELIVERY_STATUSES.has(delivery?.status)
    || !action
    || typeof action !== 'object'
    || Array.isArray(action)
    || Object.keys(action).length === 0
  ) return null

  const proposalActionType = snapshot?.action_type || snapshot?.pending_action?.type || null
  const recordedTypes = [action.type, delivery.action_type].filter(Boolean)
  if (
    recordedTypes.length === 0
    || new Set(recordedTypes).size !== 1
    || (proposalActionType && recordedTypes[0] !== proposalActionType)
  ) return null

  return action.type ? action : { ...action, type: recordedTypes[0] }
}

function proposalWithLiveTask(snapshot, task) {
  if (!task) return snapshot
  const hasLiveAction = hasOwn(task, 'pending_action')
  const livePendingAction = hasLiveAction ? task.pending_action : snapshot.pending_action
  const taskDelivery = hasOwn(task, 'delivery') ? task.delivery : snapshot.delivery
  const immutableDeliveryAction = immutableDeliveryActionFor(snapshot, taskDelivery)
  const proposalActionType = snapshot.action_type || snapshot.pending_action?.type || null
  const actionConsumed = Boolean(
    proposalActionType && hasLiveAction && !livePendingAction && immutableDeliveryAction,
  )
  const pendingAction = actionConsumed ? immutableDeliveryAction : livePendingAction
  const actionInvalidated = Boolean(
    proposalActionType && hasLiveAction && !livePendingAction && !actionConsumed,
  )
  const actionType = pendingAction?.type || (actionConsumed ? taskDelivery?.action_type : null)
  const approvalEffect = actionInvalidated
    ? 'The email draft on this historical chat card is no longer attached to the live task. Nothing will be sent from this card.'
    : actionConsumed
      ? 'This email action has already been approved. The immutable delivery payload is shown above and cannot be approved again from this card.'
    : actionType === 'email_client'
      ? `Approving sends this email to ${(pendingAction.to || []).join(', ')}. Edit the draft first if anything is wrong.`
      : 'Approving moves this task into active work. Nothing is sent.'
  return {
    ...snapshot,
    task_id: task.id || snapshot.task_id,
    title: task.title ?? snapshot.title,
    status: task.status ?? snapshot.status,
    due_date: hasOwn(task, 'due_date') ? task.due_date : snapshot.due_date,
    matter_id: hasOwn(task, 'matter_id') ? task.matter_id : snapshot.matter_id,
    version: task.version ?? snapshot.version,
    pending_action: pendingAction,
    action_type: actionType,
    approval_effect: approvalEffect,
    sources: actionConsumed
      ? immutableDeliveryAction?.sources || []
      : pendingAction?.sources || (actionInvalidated ? [] : snapshot.sources || []),
    action_consumed: actionConsumed,
    action_invalidated: actionInvalidated,
    delivery: taskDelivery,
    delivery_history: hasOwn(task, 'delivery_history')
      ? task.delivery_history || []
      : snapshot.delivery_history || [],
  }
}

const taskStatusLabel = (status) => ({
  pending: 'Pending',
  in_progress: 'In Progress',
  waiting: 'Waiting',
  review: 'Review',
  completed: 'Completed',
  cancelled: 'Cancelled',
}[status] || String(status || 'Unknown').replaceAll('_', ' '))

function ResolvedTaskStatus({ isEmail, status, delivery, polling, approvedHere }) {
  if ((isEmail && delivery) || approvedHere) {
    return (
      <DeliveryStatus
        isEmail={isEmail}
        delivery={delivery}
        polling={polling}
      />
    )
  }
  return (
    <p role="status" className="mt-3 flex items-start gap-1.5 text-[12px] font-semibold text-brand-ink-2">
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
      This task is no longer awaiting approval. Current status: {taskStatusLabel(status)}.
      {isEmail ? ' No email delivery is recorded.' : ''}
    </p>
  )
}

function renderSafeError(error, fallback) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    return detail.message
  }
  return typeof error?.message === 'string' && error.message.trim()
    ? error.message
    : fallback
}

export default function ActionProposalCard({
  proposal,
  onApprove,
  onAwaitDelivery,
  onLoadTask,
  onDismiss,
}) {
  const [liveTask, setLiveTask] = useState(null)
  const [verification, setVerification] = useState(onLoadTask ? 'loading' : 'verified')
  const [loadError, setLoadError] = useState(null)
  const [reloadCounter, setReloadCounter] = useState(0)
  const currentProposal = useMemo(
    () => proposalWithLiveTask(proposal, liveTask),
    [liveTask, proposal],
  )
  const pendingAction = currentProposal?.pending_action || null
  const isEmail = pendingAction?.type === 'email_client'
    || currentProposal?.action_type === 'email_client'
    || currentProposal?.delivery?.action_type === 'email_client'
  const [draft, setDraft] = useState(pendingAction?.body || '')
  const [editing, setEditing] = useState(false)
  const [state, setState] = useState('proposed')
  const [delivery, setDelivery] = useState(currentProposal?.delivery || null)
  const [awaitingDelivery, setAwaitingDelivery] = useState(false)
  const [error, setError] = useState(null)
  const [retryRiskAcknowledged, setRetryRiskAcknowledged] = useState(false)
  const deliveryAbortRef = useRef(null)
  const deliveryPollAttemptRef = useRef(null)

  useEffect(() => {
    if (!onLoadTask) {
      setVerification('verified')
      return undefined
    }
    let current = true
    setVerification('loading')
    setLoadError(null)
    Promise.resolve(onLoadTask(proposal.task_id))
      .then((task) => {
        if (!current) return
        if (!task || String(task.id) !== String(proposal.task_id)) {
          throw new Error('The server did not return the proposed task.')
        }
        if (task.status === 'review' && (!Number.isInteger(task.version) || task.version < 1)) {
          throw new Error('The live task did not include a reviewable version. Approval is disabled.')
        }
        setLiveTask(task)
        setDelivery(task.delivery || null)
        setEditing(false)
        setVerification('verified')
      })
      .catch((loadTaskError) => {
        if (!current) return
        setLiveTask(null)
        setVerification('failed')
        setLoadError(renderSafeError(
          loadTaskError,
          'Current task state could not be verified. Approval is disabled.',
        ))
      })
    return () => { current = false }
  }, [onLoadTask, proposal.task_id, reloadCounter])

  useEffect(() => {
    if (!editing) setDraft(pendingAction?.body || '')
  }, [editing, pendingAction?.body])

  useEffect(() => () => {
    const controller = deliveryAbortRef.current
    deliveryAbortRef.current = null
    controller?.abort()
  }, [])

  const bodyChanged = isEmail && draft !== (pendingAction?.body || '')
  const busy = state === 'approving'
  const currentDelivery = delivery || currentProposal?.delivery || null
  const currentDeliveryCertainty = currentDelivery?.delivery_certainty
    || (currentDelivery?.status === 'failed' ? 'outcome_unknown' : null)
  const approvalBlockedByActiveDelivery = isEmail
    && ['queued', 'sending'].includes(currentDelivery?.status)
  const approvalBlockedByConfirmedDelivery = isEmail
    && currentDelivery?.status === 'sent'
  const requiresRetryRiskAcknowledgment = isEmail
    && currentProposal?.status === 'review'
    && currentDelivery?.status === 'failed'
    && currentDeliveryCertainty !== 'not_attempted'

  useEffect(() => {
    setRetryRiskAcknowledged(false)
  }, [currentProposal?.task_id, currentProposal?.version, currentDelivery?.id, currentDelivery?.status])

  const pollForDelivery = useCallback(async (approvedProposal) => {
    if (!onAwaitDelivery || deliveryAbortRef.current) return
    deliveryPollAttemptRef.current = `${approvedProposal.task_id}:${approvedProposal.version || 'unknown'}`
    const abortController = new AbortController()
    deliveryAbortRef.current = abortController
    setAwaitingDelivery(true)
    try {
      setDelivery(await onAwaitDelivery(approvedProposal, { signal: abortController.signal }))
    } catch {
      setDelivery(null)
    } finally {
      if (deliveryAbortRef.current === abortController) {
        deliveryAbortRef.current = null
        setAwaitingDelivery(false)
      }
    }
  }, [onAwaitDelivery])

  useEffect(() => {
    const currentDelivery = delivery || currentProposal?.delivery
    const pollKey = `${currentProposal?.task_id}:${currentProposal?.version || 'unknown'}`
    if (
      verification === 'verified'
      && (currentProposal?.status !== 'review' || currentProposal?.action_consumed)
      && isEmail
      && ['queued', 'sending'].includes(currentDelivery?.status)
      && deliveryPollAttemptRef.current !== pollKey
    ) {
      void pollForDelivery(currentProposal)
    }
  }, [currentProposal, delivery, isEmail, pollForDelivery, verification])

  const handleApprove = async () => {
    setState('approving')
    setError(null)
    try {
      const approvalOptions = {
        ...(bodyChanged ? { body: draft } : {}),
        ...(requiresRetryRiskAcknowledgment && retryRiskAcknowledged
          ? { acknowledge_prior_delivery_risk: true }
          : {}),
      }
      const approvedTask = await onApprove(
        currentProposal,
        Object.keys(approvalOptions).length > 0 ? approvalOptions : undefined,
      )
      const nextTask = approvedTask && typeof approvedTask === 'object'
        ? approvedTask
        : {
            ...liveTask,
            id: currentProposal.task_id,
            status: 'in_progress',
            version: currentProposal.version,
            pending_action: currentProposal.pending_action,
            delivery: currentProposal.delivery || null,
          }
      setLiveTask(nextTask)
      setDelivery(nextTask.delivery || null)
      setVerification('verified')
      setState('approved')
      if (isEmail) void pollForDelivery(proposalWithLiveTask(proposal, nextTask))
    } catch (err) {
      setState('proposed')
      const currentTask = err?.current_task || err?.response?.data?.current_task
      if (currentTask) {
        setLiveTask(currentTask)
        setDelivery(currentTask.delivery || null)
        setEditing(false)
        setVerification('verified')
      }
      setError(renderSafeError(err, 'The task could not be approved.'))
      return
    }
  }

  if (state === 'dismissed') return null

  return (
    <section
      data-testid="action-proposal"
      aria-label={`Proposed work: ${currentProposal.title}`}
      className="mt-4 border border-brand-accent/40 bg-brand-accent/[0.04] p-3 sm:p-4"
    >
      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-brand-accent-2">
        <ClipboardCheck className="h-3.5 w-3.5" strokeWidth={2} />
        <span className="font-bold">
          {verification === 'loading'
            ? 'Checking current task status'
            : currentProposal.action_invalidated
              ? 'Historical proposal'
              : currentProposal.action_consumed
                ? 'Recorded action outcome'
              : currentProposal.status === 'review'
              ? 'Proposed for your approval'
              : 'Current task status'}
        </span>
      </div>

      <h4 className="mt-1.5 font-serif text-[15px] font-semibold text-brand-ink">
        {currentProposal.title}
      </h4>

      <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-brand-muted">
        <div className="flex gap-1">
          <dt className="font-semibold">Status</dt>
          <dd>{taskStatusLabel(currentProposal.status || 'review')}</dd>
        </div>
        {currentProposal.due_date && (
          <div className="flex gap-1">
            <dt className="font-semibold">Due</dt>
            <dd>{currentProposal.due_date}</dd>
          </div>
        )}
      </dl>

      <EmailDraft
        pendingAction={pendingAction}
        draft={draft}
        onDraftChange={setDraft}
        editing={editing}
        immutable={currentProposal.action_consumed}
      />

      <SourceChips sources={currentProposal.sources} />
      <PriorDeliveryAttempts history={currentProposal.delivery_history} />

      {/* Stated before the button, not after, so the consequence is read first. */}
      <p className="mt-3 text-[12px] leading-relaxed text-brand-ink-2">
        {currentProposal.approval_effect}
      </p>

      {verification === 'verified'
        && currentProposal.status === 'review'
        && !currentProposal.action_consumed
        && state !== 'approved'
        && !approvalBlockedByConfirmedDelivery
        && isEmail
        && currentDelivery && (
        <DeliveryStatus isEmail delivery={currentDelivery} />
      )}

      {verification === 'loading' ? (
        <p role="status" className="mt-3 flex items-center gap-1.5 text-[12px] font-semibold text-brand-ink-2">
          <LoaderCircle className="h-4 w-4 animate-spin" strokeWidth={2} />
          Verifying the live task before approval is enabled.
        </p>
      ) : verification === 'failed' ? (
        <div role="alert" className="mt-3 text-[12px] font-semibold text-brand-rose">
          <p>{loadError}</p>
          <button
            type="button"
            onClick={() => setReloadCounter((value) => value + 1)}
            className="mt-2 rounded-lg border border-brand-line px-3 py-2 text-brand-ink hover:bg-brand-bg"
          >
            Retry task status
          </button>
        </div>
      ) : currentProposal.action_invalidated ? (
        <div role="alert" className="mt-3 flex items-start gap-1.5 text-[12px] font-semibold text-brand-rose">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
          This historical email draft is no longer attached to the live task.
          Nothing can be approved or sent from this card; review the current task on the work board.
        </div>
      ) : currentProposal.action_consumed || state === 'approved' || currentProposal.status !== 'review' ? (
        <ResolvedTaskStatus
          isEmail={isEmail}
          status={currentProposal.status}
          delivery={delivery || currentProposal.delivery}
          polling={awaitingDelivery}
          approvedHere={state === 'approved'}
        />
      ) : approvalBlockedByConfirmedDelivery ? (
        <p role="status" className="mt-3 flex items-start gap-1.5 text-[12px] font-semibold text-brand-accent">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
          Delivery is already confirmed sent. This draft cannot be approved again from chat.
        </p>
      ) : approvalBlockedByActiveDelivery ? (
        <p role="status" className="mt-3 text-[12px] font-semibold text-brand-ink-2">
          Another delivery attempt is still {currentDelivery?.status}. Approval stays disabled until its outcome is recorded.
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {requiresRetryRiskAcknowledgment && (
            <label className="mb-1 flex w-full items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-[11px] font-semibold leading-relaxed text-amber-950">
              <input
                type="checkbox"
                checked={retryRiskAcknowledged}
                onChange={(event) => setRetryRiskAcknowledged(event.target.checked)}
                disabled={busy}
                className="mt-0.5"
              />
              I checked the connected mailbox Sent Items and understand that another attempt could send a duplicate.
            </label>
          )}
          <button
            type="button"
            onClick={handleApprove}
            disabled={busy || (requiresRetryRiskAcknowledgment && !retryRiskAcknowledged)}
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
              onDismiss?.(currentProposal)
            }}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg px-2 py-2 text-[12px] font-semibold text-brand-muted transition-colors hover:text-brand-ink disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" strokeWidth={2} />
            Hide for now
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
