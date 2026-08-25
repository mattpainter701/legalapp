import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  decideResearchMcpAuthorizationRequest,
  getResearchMcpAuthorizationRequest,
} from '../api'

function Shell({ children }) {
  return <main className="min-h-screen bg-brand-bg flex items-center justify-center px-4 py-12"><div className="w-full max-w-2xl">{children}</div></main>
}

export default function ResearchMcpAuthorizePage() {
  const [search] = useSearchParams()
  const navigate = useNavigate()
  const requestId = search.get('request_id')
  const [request, setRequest] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [decision, setDecision] = useState(null)

  useEffect(() => {
    if (!requestId) {
      setError('This authorization link is missing its request ID.')
      return
    }
    getResearchMcpAuthorizationRequest(requestId)
      .then(setRequest)
      .catch((err) => setError(err?.response?.data?.detail || 'This research authorization request is unavailable or has expired.'))
  }, [requestId])

  const complete = (result) => {
    if (result?.redirect_to) window.location.assign(result.redirect_to)
    else navigate('/product/mcp', { replace: true })
  }

  const handleDecision = async (approved) => {
    if (!requestId) return
    setBusy(true)
    setError(null)
    try {
      const result = await decideResearchMcpAuthorizationRequest(requestId, approved)
      setDecision(approved ? 'approved' : 'denied')
      complete(result)
    } catch (err) {
      setError(err?.response?.data?.detail || 'We could not record your research decision. Please try again.')
      setBusy(false)
    }
  }

  const tenantName = request?.organization?.name || request?.organization_name || request?.tenant_name || request?.tenant?.name || 'Your LawHand account'
  const accountName = request?.user?.name || request?.user_name || request?.user?.display_name || request?.user_email || request?.user?.email || ''

  return <Shell>
    <section className="bg-brand-surface border border-brand-line rounded-2xl shadow-xl overflow-hidden">
      <header className="px-7 py-6 border-b border-brand-line">
        <p className="text-[11px] uppercase tracking-[0.18em] font-bold text-brand-accent">LawHand Research MCP</p>
        <h1 className="mt-2 text-2xl font-serif font-bold text-brand-ink">Authorize legal research access</h1>
        <p className="mt-2 text-sm leading-6 text-brand-ink-2">Review the research-only connection before continuing. This consent does not grant workspace or matter access.</p>
      </header>
      <div className="px-7 py-6 space-y-5">
        {error && <div role="alert" className="px-4 py-3 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">{error}</div>}
        {!error && !request && <div className="py-8 text-center text-sm text-brand-muted">Loading research authorization request.</div>}
        {request && <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl bg-brand-bg px-4 py-3"><p className="text-[11px] uppercase tracking-wider font-bold text-brand-muted">Application</p><p className="mt-1 font-semibold text-brand-ink">{request.client_name || request.client?.name || 'Connected research client'}</p></div>
            <div className="rounded-xl bg-brand-bg px-4 py-3"><p className="text-[11px] uppercase tracking-wider font-bold text-brand-muted">Account / tenant</p><p className="mt-1 font-semibold text-brand-ink">{tenantName}</p>{accountName && <p className="text-xs text-brand-ink-2">{accountName}</p>}</div>
          </div>
          <div>
            <h2 className="text-sm font-bold text-brand-ink">Requested scope</h2>
            <ul className="mt-2 space-y-2"><li className="flex gap-2 text-sm text-brand-ink-2"><span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand-accent" /><span><strong>research:read</strong> — search and read approved public legal authority.</span></li></ul>
          </div>
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-900">
            <p className="font-bold">Research-only boundary</p>
            <p className="mt-1 leading-6">This connection can access approved public legal authority for research. It cannot access workspace matters, documents, tasks, client files, or other tenant content.</p>
          </div>
          <div className="rounded-xl border border-brand-line bg-brand-bg px-4 py-4 text-sm text-brand-ink-2">
            <p className="font-bold text-brand-ink">PAYG metering</p>
            <p className="mt-1 leading-6">Research usage is metered under the applicable LawHand PAYG terms. Pure retrieval does not send prompts to a model gateway; any future synthesis capability will be separately disclosed.</p>
          </div>
          <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <button type="button" disabled={busy} onClick={() => handleDecision(false)} className="px-5 py-2.5 rounded-xl border border-brand-line text-brand-ink font-semibold text-sm hover:bg-brand-bg-soft disabled:opacity-50">Deny access</button>
            <button type="button" disabled={busy} onClick={() => handleDecision(true)} className="px-5 py-2.5 rounded-xl bg-brand-ink text-white font-semibold text-sm hover:bg-brand-ink-2 disabled:opacity-50">{busy ? 'Recording decision.' : 'Approve research access'}</button>
          </div>
          {decision && <p role="status" className="text-xs text-brand-muted text-right">Decision recorded. Returning to the connected research client.</p>}
        </>}
      </div>
    </section>
  </Shell>
}
