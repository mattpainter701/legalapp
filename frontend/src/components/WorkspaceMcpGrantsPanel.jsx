import { useEffect, useState } from 'react'
import { getWorkspaceMcpGrants, revokeWorkspaceMcpGrant } from '../api'
import { normalizeWorkspaceMcpScopes, workspaceMcpOrganizationName } from '../workspaceMcp'
import { ConfirmProvider, useConfirm } from './dialog/ConfirmProvider'

function date(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function isActiveGrant(grant) {
  if (grant?.status && grant.status !== 'active') return false
  return !grant?.revoked_at
}

export default function WorkspaceMcpGrantsPanel({ blockedReason = '' }) {
  return <ConfirmProvider><WorkspaceMcpGrantsPanelContent blockedReason={blockedReason} /></ConfirmProvider>
}

function WorkspaceMcpGrantsPanelContent({ blockedReason = '' }) {
  const confirm = useConfirm()
  const [grants, setGrants] = useState([])
  const [loading, setLoading] = useState(true)
  const [available, setAvailable] = useState(true)
  const [error, setError] = useState(null)
  const [revoking, setRevoking] = useState(null)

  const load = () => {
    setLoading(true)
    setAvailable(true)
    setError(null)
    getWorkspaceMcpGrants()
      .then((result) => {
        const items = result?.items || result?.grants || (Array.isArray(result) ? result : [])
        setGrants(Array.isArray(items) ? items.filter(isActiveGrant) : [])
      })
      .catch((err) => {
        const status = err?.response?.status
        if (status === 403 || status === 404) {
          setAvailable(false)
          setGrants([])
          return
        }
        setError(err?.response?.data?.detail || 'Could not load connected assistants.')
      })
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const revoke = async (grant) => {
    const approved = await confirm({
      title: 'Revoke assistant access?',
      message: `Revoke access for ${grant.client_name || grant.client?.name || 'this assistant'} in ${workspaceMcpOrganizationName(grant)}?`,
      confirmLabel: 'Revoke access',
      destructive: true,
    })
    if (!approved) return
    setRevoking(grant.id); setError(null)
    try {
      await revokeWorkspaceMcpGrant(grant.id)
      setGrants((current) => current.filter((item) => item.id !== grant.id))
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not revoke this connection.')
    } finally { setRevoking(null) }
  }

  if (!available) return null

  return <section className="bg-brand-surface border border-brand-line rounded-xl p-6" aria-labelledby="workspace-mcp-grants-heading">
    <div className="flex items-start justify-between gap-4 flex-wrap">
      <div><h3 id="workspace-mcp-grants-heading" className="text-brand-ink font-sans text-base font-bold">Workspace MCP assistants</h3><p className="mt-1 text-brand-ink-2 font-sans text-xs leading-5">Official URL: <code>https://mcp.getlawhand.com/api/mcp/workspace</code>. The bare hostname is also supported. Review and revoke connected assistants; all access is scope-limited and audit logged.</p></div>
      <button type="button" onClick={load} disabled={loading} className="px-3 py-1.5 border border-brand-line rounded-lg text-xs font-semibold text-brand-ink hover:bg-brand-bg-soft disabled:opacity-50">{loading ? 'Refreshing.' : 'Refresh'}</button>
    </div>
    {blockedReason && <div role="status" className="mt-4 px-3 py-2 bg-amber-50 border border-amber-300 rounded-lg text-amber-950 text-xs font-semibold leading-5">Connected assistants are currently blocked. {blockedReason}</div>}
    {error && <div role="alert" className="mt-4 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-red-700 text-xs font-medium">{error}</div>}
    {loading ? <div className="py-8 text-center text-sm text-brand-muted">Loading connected assistants.</div> :
      grants.length === 0 ? <div className="mt-5 rounded-lg bg-brand-bg px-4 py-5 text-sm text-brand-ink-2">No active Workspace MCP assistants are connected.</div> :
      <div className="mt-5 space-y-3">{grants.map((grant) => {
        const status = grant.status || (grant.revoked_at ? 'revoked' : 'active')
        const scopes = normalizeWorkspaceMcpScopes(grant.scopes || grant.scope || [])
        return <article key={grant.id} className="rounded-xl border border-brand-line bg-brand-bg px-4 py-4">
          <div className="flex items-start justify-between gap-3">
            <div><h4 className="font-semibold text-brand-ink">{grant.client_name || grant.client?.name || 'Connected assistant'}</h4><p className="mt-0.5 text-xs text-brand-ink-2">{workspaceMcpOrganizationName(grant)}</p><span className={`inline-block mt-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${status === 'active' ? 'bg-green-100 text-green-700' : 'bg-brand-bg-soft text-brand-muted'}`}>{status}</span></div>
            {status === 'active' && <button type="button" onClick={() => revoke(grant)} disabled={revoking === grant.id} className="px-3 py-1.5 border border-red-200 rounded-lg text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50">{revoking === grant.id ? 'Revoking.' : 'Revoke'}</button>}
          </div>
          <dl className="mt-3 grid gap-2 sm:grid-cols-3 text-xs"><div><dt className="text-brand-muted">Created</dt><dd className="text-brand-ink">{date(grant.created_at)}</dd></div><div><dt className="text-brand-muted">Expires</dt><dd className="text-brand-ink">{date(grant.expires_at)}</dd></div><div><dt className="text-brand-muted">Last used</dt><dd className="text-brand-ink">{date(grant.last_used_at)}</dd></div></dl>
          <div className="mt-3 flex flex-wrap gap-1.5">{scopes.map((scope) => <span key={scope.id} className="px-2 py-1 rounded-md bg-brand-surface border border-brand-line text-[11px] text-brand-ink">{scope.label}</span>)}</div>
        </article>
      })}</div>}
  </section>
}
