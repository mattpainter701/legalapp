import { useEffect, useState } from 'react'
import api from '../../api'

export default function ArtifactReviewPanel({ task, onUpdated, disabled = false }) {
  const artifactId = task?.pending_action?.artifact_id || task?.delivery?.action_snapshot?.artifact_id
  const [history, setHistory] = useState(null)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    setHistory(null)
    setError('')
    if (artifactId) api.get(`/artifact-reviews/${artifactId}`).then(({ data }) => {
      if (active) setHistory(data)
    }).catch(() => { if (active) setError('Review history could not be loaded. Reopen this task to retry.') })
    return () => { active = false }
  }, [artifactId, task?.version])
  if (!artifactId) return null
  const actions = history?.available_actions || []
  const stale = history && history.task_version !== task.version
  const submit = async (stage, decision) => {
    if (busy || disabled || stale) return
    if ((stage === 'override' || decision === 'request_changes') && !reason.trim()) {
      setError('Enter a reason for this decision.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const endpoint = stage === 'override' ? 'attorney-override' : stage
      const { data } = await api.post(`/tasks/${task.id}/review/${endpoint}`, {
        expected_version: task.version, reason: reason.trim() || null,
        ...(stage === 'override' ? {} : { decision }),
      })
      setReason('')
      onUpdated(data)
    } catch (err) {
      const detail = err.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Review could not be saved. Refresh the task and try again.')
    } finally { setBusy(false) }
  }
  const current = (history?.requirements || []).filter(row => !row.superseded_at)
    .sort((a, b) => a.sequence - b.sequence)
  return (
    <section id="artifact-review-panel" className="rounded-xl border border-brand-line p-4" aria-label="Artifact review">
      <h3 className="font-semibold">Document review · revision {history?.current_revision_no || task.pending_action?.artifact_revision_no || task.delivery?.action_snapshot?.artifact_revision_no}</h3>
      <p className="mt-1 text-xs text-brand-muted">Approval applies to this exact document. Sending it to a client requires a separate delivery review.</p>
      {!history && !error && <p className="mt-2 text-sm">Loading review history…</p>}
      {current.length > 0 && <ol className="my-3 space-y-1 text-sm">{current.map(row => (
        <li key={row.id} className="capitalize">{row.reviewer_role}: {row.status.replaceAll('_', ' ')}</li>
      ))}</ol>}
      {stale && <p role="alert" className="mt-2 text-sm text-amber-800">This task changed. Close and reopen it before reviewing.</p>}
      {actions.length > 0 && <>
        <label className="mt-3 block text-xs font-semibold">Review reason
          <textarea value={reason} onChange={e => setReason(e.target.value)} disabled={busy || disabled} maxLength={2000} className="mt-1 w-full rounded border border-brand-line p-2 text-sm" />
        </label>
        <div className="mt-2 flex flex-wrap gap-2">
          {['staff', 'attorney'].filter(stage => actions.includes(stage)).map(stage => (
            <span key={stage} className="flex gap-2">
              <button type="button" disabled={busy || disabled || stale} className="btn-primary" onClick={() => submit(stage, 'approve')}>{stage === 'staff' ? 'Complete staff review' : 'Approve document'}</button>
              <button type="button" disabled={busy || disabled || stale} className="btn-secondary" onClick={() => submit(stage, 'request_changes')}>Request changes</button>
            </span>
          ))}
          {actions.includes('override') && <button type="button" disabled={busy || disabled || stale} className="btn-secondary" onClick={() => submit('override', 'approve')}>Override staff review</button>}
        </div>
      </>}
      {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
      {(history?.approvals?.length > 0 || history?.deliveries?.length > 0) && <details className="mt-3 text-xs">
        <summary className="cursor-pointer font-semibold">Decision and delivery evidence</summary>
        {(history.approvals || []).map(row => <p key={row.id} className="mt-2 break-all">{row.decision.replaceAll('_', ' ')} · {new Date(row.created_at).toLocaleString()} · revision {row.revision_id}<br />Document SHA-256: {row.document_sha256}{row.reason && <><br />{row.reason}</>}</p>)}
        {(history.deliveries || []).map(row => <p key={row.id} className="mt-2">{row.channel}: {row.status.replaceAll('_', ' ')} · {new Date(row.created_at).toLocaleString()}</p>)}
      </details>}
    </section>
  )
}
