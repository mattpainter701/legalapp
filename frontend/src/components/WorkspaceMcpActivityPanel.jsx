import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getWorkspaceMcpActivity, getWorkspaceMcpActiveGrants } from '../api'

const date = value => value ? new Date(value).toLocaleString() : 'Never'
const readable = value => String(value || '').replaceAll('_', ' ')

export default function WorkspaceMcpActivityPanel() {
  const [grants, setGrants] = useState({ items: [], next_offset: null })
  const [activity, setActivity] = useState({ items: [], next_before: null })
  const [grantId, setGrantId] = useState('')
  const [before, setBefore] = useState(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const requestSequence = useRef(0)
  const load = useCallback(async () => {
    const sequence = ++requestSequence.current
    setBusy(true)
    try {
      const [connections, events] = await Promise.all([
        getWorkspaceMcpActiveGrants({ offset }),
        getWorkspaceMcpActivity({ before: before || undefined, grant_id: grantId || undefined }),
      ])
      if (sequence !== requestSequence.current) return
      if (!Array.isArray(connections?.items) || !Array.isArray(events?.items)) throw new Error('Invalid activity response')
      setGrants(connections); setActivity(events); setError(''); setLoaded(true)
    } catch { if (sequence === requestSequence.current) setError('Unable to load firm assistant activity. Firm settings, matter, and document permissions are required.') }
    finally { if (sequence === requestSequence.current) setBusy(false) }
  }, [before, grantId, offset])
  useEffect(() => { load(); return () => { requestSequence.current += 1 } }, [load])
  return <section aria-label="Firm assistant activity" className="rounded-xl border border-brand-line bg-brand-surface p-5 space-y-4">
    <div className="flex justify-between gap-3"><h3 className="font-semibold">Firm assistant activity</h3><button disabled={busy} onClick={load} className="underline text-sm">Refresh activity</button></div>
    <p className="text-sm text-brand-muted">Inspect connections, reads, proposals, and recorded reviews. Activity remains available after a connection is revoked. Assistant inference and metered LawHand AI are billed separately.</p>
    {error && <p role="alert">{error}</p>}
    {!loaded && !error && <p role="status">Loading firm assistant activity…</p>}
    {!error && loaded && <>
      <h4 className="font-medium">Active connections</h4>
      {!grants.items.length && <p className="text-sm">No active connections.</p>}
      <ul className="space-y-2">{grants.items.map(grant => <li key={grant.id} className="rounded border border-brand-line p-3 text-sm">
        <strong>{grant.client_name}</strong> · {grant.user_name || 'Former user'}
        <p>Last used {date(grant.last_used_at)} · Expires {date(grant.expires_at)}</p>
        <p className="break-words">Scopes: {(grant.scopes || []).join(', ')}</p>
        <button className="underline mr-4" onClick={() => { setGrantId(grant.id); setBefore(null) }}>View this connection’s activity</button>
        <Link className="underline" to="/admin?tab=users">Manage access</Link>
      </li>)}</ul>
      <div className="flex gap-4 text-sm">{offset > 0 && <button onClick={() => setOffset(Math.max(0, offset - 50))}>Newer connections</button>}{grants.next_offset != null && <button onClick={() => setOffset(grants.next_offset)}>Older connections</button>}</div>
      <div className="flex gap-4"><h4 className="font-medium">Recorded activity</h4>{(grantId || before) && <button className="underline text-sm" onClick={() => { setGrantId(''); setBefore(null) }}>Show latest firm activity</button>}</div>
      {grantId && <p className="text-xs break-all">Connection {grantId}</p>}
      {!activity.items.length && <p className="text-sm">No activity recorded for this selection.</p>}
      {activity.reviews_truncated && <p role="status">This page contains many historical reviews. Open the review task for the full history.</p>}
      <ol className="space-y-3">{activity.items.map(event => <li key={event.id} className="border-t border-brand-line pt-3 text-sm space-y-1">
        <p><strong>{event.user_name || 'Former user'}</strong> · {event.client_id} · {date(event.created_at)}</p>
        <p>{readable(event.tool_name || event.event_type)} · {readable(event.outcome)}{event.metadata.result_bytes != null && ` · ${event.metadata.result_bytes.toLocaleString()} response bytes`}</p>
        {event.metadata.failure_reason && <p>Reason: {readable(event.metadata.failure_reason)}</p>}
        {event.run && <p><Link className="underline" to={`/matters/${event.run.matter_id}?tab=workflow`}>Workflow run</Link> · {readable(event.run.status)}</p>}
        {event.tasks.map(task => <p key={task.id}><Link className="underline" to={`/tasks/${task.id}`}>Review task</Link> · {readable(task.status)}</p>)}
        {event.artifacts.map(artifact => <p key={artifact.id}>Artifact revision {artifact.revision_no} · {readable(artifact.status)}</p>)}
        {event.reviews.map(review => <details key={review.id}><summary>Review: {readable(review.decision)} · {date(review.created_at)}</summary><p className="break-all">Reviewer {review.reviewer_user_id} · Revision {review.revision_id} · Content SHA-256 {review.content_sha256} · Document SHA-256 {review.document_sha256}</p></details>)}
        <details className="text-xs"><summary>Audit evidence</summary><p className="break-all">Sequence {event.chain_position} · Event {event.event_hash} · Previous {event.previous_event_hash || 'First event'}</p></details>
      </li>)}</ol>
      {activity.next_before != null && <button disabled={busy} className="underline text-sm" onClick={() => setBefore(activity.next_before)}>Older activity</button>}
    </>}
  </section>
}
