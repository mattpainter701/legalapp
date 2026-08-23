import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  decideWorkspaceMcpAuthorizationRequest,
  getWorkspaceMcpAuthorizationRequest,
} from '../api'
import { normalizeWorkspaceMcpScopes } from '../workspaceMcp'
function Shell({ children }) {
  return <main className="min-h-screen bg-brand-bg flex items-center justify-center px-4 py-12"><div className="w-full max-w-2xl">{children}</div></main>
}

export default function WorkspaceMcpAuthorizePage() {
  const [search] = useSearchParams()
  const navigate = useNavigate()
  const requestId = search.get('request_id')
  const [request, setRequest] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [decision, setDecision] = useState(null)

  useEffect(() => {
    if (!requestId) { setError('This authorization link is missing its request ID.'); return }
    getWorkspaceMcpAuthorizationRequest(requestId)
      .then(setRequest)
      .catch((err) => setError(err?.response?.data?.detail || 'This authorization request is unavailable or has expired.'))
  }, [requestId])

  const scopes = useMemo(() => normalizeWorkspaceMcpScopes(request?.scopes || request?.requested_scopes || request?.scope), [request])
  const complete = (result) => {
    const target = result?.redirect_to
    if (target) window.location.assign(target)
    else navigate('/matters', { replace: true })
  }
  const handleDecision = async (approved) => {
    if (!requestId) return
    setBusy(true); setError(null)
    try {
      const result = await decideWorkspaceMcpAuthorizationRequest(requestId, approved)
      setDecision(approved ? 'approved' : 'denied')
      complete(result)
    } catch (err) {
      setError(err?.response?.data?.detail || 'We could not record your decision. Please try again.')
      setBusy(false)
    }
  }

  return <Shell>
    <section className="bg-brand-surface border border-brand-line rounded-2xl shadow-xl overflow-hidden">
      <header className="px-7 py-6 border-b border-brand-line">
        <p className="text-[11px] uppercase tracking-[0.18em] font-bold text-brand-accent">LawHand workspace connection</p>
        <h1 className="mt-2 text-2xl font-serif font-bold text-brand-ink">Authorize a legal assistant</h1>
        <p className="mt-2 text-sm leading-6 text-brand-ink-2">Review exactly what this connected assistant may access before continuing.</p>
      </header>
      <div className="px-7 py-6 space-y-5">
        {error && <div role="alert" className="px-4 py-3 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">{error}</div>}
        {!error && !request && <div className="py-8 text-center text-sm text-brand-muted">Loading authorization request.</div>}
        {request && <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl bg-brand-bg px-4 py-3"><p className="text-[11px] uppercase tracking-wider font-bold text-brand-muted">Application</p><p className="mt-1 font-semibold text-brand-ink">{request.client_name || request.client?.name || 'Connected assistant'}</p></div>
            <div className="rounded-xl bg-brand-bg px-4 py-3"><p className="text-[11px] uppercase tracking-wider font-bold text-brand-muted">Workspace</p><p className="mt-1 font-semibold text-brand-ink">{request.organization?.name || request.organization_name || request.tenant_name || request.tenant?.name || 'Your LawHand workspace'}</p><p className="text-xs text-brand-ink-2">{request.user?.name || request.user_name || request.user?.display_name || request.user_email || request.user?.email || ''}</p>{request.user?.name && request.user?.email && <p className="text-xs text-brand-muted">{request.user.email}</p>}</div>
          </div>
          <div>
            <h2 className="text-sm font-bold text-brand-ink">Requested access</h2>
            <ul className="mt-2 space-y-2">{scopes.length ? scopes.map((scope) => <li key={scope.id} className="flex gap-2 text-sm text-brand-ink-2"><span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand-accent" />{scope.label}</li>) : <li className="text-sm text-brand-ink-2">No additional scopes requested.</li>}</ul>
          </div>
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-900">
            <p className="font-bold">Review-first safety boundary</p>
            <p className="mt-1 leading-6">LawHand assistants can research and prepare proposals. They cannot approve work, send email, or deliver documents through this connection. Your firm's normal reviewers and audit trail remain in control.</p>
          </div>
          <div className="rounded-xl border border-brand-line bg-brand-bg px-4 py-4 text-sm text-brand-ink-2">
            <p className="font-bold text-brand-ink">What leaves this workspace</p>
            <p className="mt-1 leading-6">Approving lets this application read matter material within the scopes above, so that content is sent to whoever operates it. LawHand governs what its own tools will do — it cannot govern other tools connected to the same assistant. Only approve applications your firm has cleared to handle client-confidential material.</p>
          </div>
          <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <button type="button" disabled={busy} onClick={() => handleDecision(false)} className="px-5 py-2.5 rounded-xl border border-brand-line text-brand-ink font-semibold text-sm hover:bg-brand-bg-soft disabled:opacity-50">Deny access</button>
            <button type="button" disabled={busy} onClick={() => handleDecision(true)} className="px-5 py-2.5 rounded-xl bg-brand-ink text-white font-semibold text-sm hover:bg-brand-ink-2 disabled:opacity-50">{busy ? 'Recording decision.' : 'Approve connection'}</button>
          </div>
          {decision && <p role="status" className="text-xs text-brand-muted text-right">Decision recorded. Returning to the connected assistant.</p>}
        </>}
      </div>
    </section>
  </Shell>
}
