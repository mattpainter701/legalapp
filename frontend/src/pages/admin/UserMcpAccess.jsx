import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ExternalLink, RefreshCw, X } from 'lucide-react'
import {
  getAdminWorkspaceMcpGrants,
  revokeAdminWorkspaceMcpGrant,
} from '../../api'
import { useConfirm } from '../../components/dialog/ConfirmProvider'
import { Toggle } from '../../components/ui'
import { normalizeWorkspaceMcpScopes } from '../../workspaceMcp'

const STATUS_STYLES = {
  ready: 'bg-emerald-500',
  connected: 'bg-emerald-500',
  paused: 'bg-amber-500',
  blocked: 'bg-slate-400',
}

function connectionCount(user) {
  const count = Number(user?.workspace_mcp_active_grant_count)
  return Number.isFinite(count) && count > 0 ? Math.floor(count) : 0
}

export function getUserMcpAccessState(user, workspace = null) {
  const count = connectionCount(user)
  if (user?.is_active === false) {
    return {
      key: 'inactive',
      tone: 'blocked',
      label: 'Inactive account',
      detail: 'MCP access is unavailable',
    }
  }
  if (user?.license_active === false) {
    return {
      key: 'unlicensed',
      tone: 'blocked',
      label: 'Unlicensed',
      detail: 'An active license is required',
    }
  }
  if (workspace?.deployment_enabled === false) {
    return {
      key: 'deployment-disabled',
      tone: 'blocked',
      label: 'Platform unavailable',
      detail: 'LawHand operations must enable MCP',
    }
  }
  if (workspace?.tenant_enabled === false) {
    return {
      key: 'tenant-disabled',
      tone: 'blocked',
      label: 'Disabled by firm',
      detail: 'Tenant MCP access is off',
    }
  }
  if (user?.workspace_mcp_enabled === false) {
    return {
      key: 'user-disabled',
      tone: 'blocked',
      label: 'Disabled for user',
      detail: 'Firm access is off for this user',
    }
  }
  if (user?.privacy_mode === true) {
    return {
      key: 'privacy-mode',
      tone: 'paused',
      label: 'Paused by Privacy Mode',
      detail: 'User must turn it off in Profile',
    }
  }
  if (workspace?.status_available === false) {
    return {
      key: 'status-unavailable',
      tone: 'blocked',
      label: 'Status unavailable',
      detail: 'Tenant MCP status could not be loaded',
    }
  }
  if (count > 0) {
    return {
      key: 'connected',
      tone: 'connected',
      label: `Connected (${count})`,
      detail: `${count} connected ${count === 1 ? 'client' : 'clients'}`,
    }
  }
  return {
    key: 'ready',
    tone: 'ready',
    label: 'Ready to connect',
    detail: 'No active connections',
  }
}

export function UserMcpAccessCell({
  user,
  workspace,
  saving = false,
  onToggle,
  onManage,
}) {
  const state = getUserMcpAccessState(user, workspace)
  const policyEnabled = user.workspace_mcp_enabled !== false
  const policyDisabled = user.is_active === false || saving

  return (
    <div className="min-w-[205px] space-y-2 font-sans">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden="true"
            className={`h-2.5 w-2.5 shrink-0 rounded-full ${STATUS_STYLES[state.tone]}`}
          />
          <span className="text-xs font-semibold leading-4 text-brand-ink">{state.label}</span>
        </div>
        <Toggle
          checked={policyEnabled}
          disabled={policyDisabled}
          label={`Allow Workspace MCP access for ${user.email}`}
          onChange={(value) => onToggle(user, value)}
        />
      </div>
      <div className="flex items-end justify-between gap-3">
        <span className="max-w-[145px] text-[10px] leading-4 text-brand-muted">{state.detail}</span>
        <button
          type="button"
          onClick={() => onManage(user)}
          className="shrink-0 text-[11px] font-semibold text-brand-accent hover:underline focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
          aria-label={`Manage MCP access for ${user.email}`}
        >
          Manage
        </button>
      </div>
    </div>
  )
}

function formatDate(value) {
  if (!value) return 'Never'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

function isActiveGrant(grant) {
  if (!grant || grant.revoked_at || (grant.status && grant.status !== 'active')) return false
  if (!grant.expires_at) return true
  const expires = new Date(grant.expires_at)
  return Number.isFinite(expires.getTime()) && expires.getTime() > Date.now()
}

const FOCUSABLE = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

export function UserMcpAccessDrawer({
  user,
  workspace,
  saving = false,
  onToggle,
  onClose,
  onConnectionsChanged,
  onNavigateMcp,
}) {
  const confirm = useConfirm()
  const drawerRef = useRef(null)
  const closeRef = useRef(null)
  const previousFocusRef = useRef(null)
  const [grants, setGrants] = useState([])
  const [loading, setLoading] = useState(true)
  const [hasLoadedGrants, setHasLoadedGrants] = useState(false)
  const [error, setError] = useState('')
  const [announcement, setAnnouncement] = useState('')
  const [revoking, setRevoking] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await getAdminWorkspaceMcpGrants(user.id)
      const items = result?.items || result?.grants || (Array.isArray(result) ? result : [])
      setGrants(Array.isArray(items) ? items.filter(isActiveGrant) : [])
      setHasLoadedGrants(true)
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'Could not load this user’s MCP connections.')
    } finally {
      setLoading(false)
    }
  }, [user.id])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    const handleKeyDown = (event) => {
      if (document.querySelector('[role="alertdialog"]')) return
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = Array.from(drawerRef.current?.querySelectorAll(FOCUSABLE) || [])
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      queueMicrotask(() => {
        if (!document.querySelector('[role="dialog"][aria-modal="true"]')) {
          previousFocusRef.current?.focus()
        }
      })
    }
  }, [onClose, user.id])

  const displayedUser = useMemo(() => ({
    ...user,
    workspace_mcp_active_grant_count: hasLoadedGrants
      ? grants.length
      : connectionCount(user),
  }), [grants.length, hasLoadedGrants, user])
  const state = getUserMcpAccessState(displayedUser, workspace)
  const policyEnabled = user.workspace_mcp_enabled !== false

  const changePolicy = async (value) => {
    const changed = await onToggle(user, value)
    if (changed && !value) {
      setGrants([])
      setAnnouncement('Firm access was disabled and all active MCP connections were revoked.')
    }
  }

  const revoke = async (grant) => {
    const name = grant.client_name || 'this assistant'
    const approved = await confirm({
      title: 'Revoke MCP connection?',
      message: `Revoke ${name} for ${user.email}? Its current Workspace MCP access will stop immediately.`,
      confirmLabel: 'Revoke connection',
      destructive: true,
    })
    if (!approved) return
    setRevoking(grant.id)
    setError('')
    setAnnouncement('')
    try {
      await revokeAdminWorkspaceMcpGrant(user.id, grant.id)
      setGrants((current) => current.filter((item) => item.id !== grant.id))
      setAnnouncement(`${name} access was revoked.`)
      onConnectionsChanged?.(user.id)
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'Could not revoke this MCP connection.')
    } finally {
      setRevoking(null)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex justify-end bg-brand-ink/40"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-mcp-drawer-title"
        className="flex h-full w-full max-w-lg flex-col bg-brand-surface shadow-2xl"
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-brand-line px-5 py-5 sm:px-6">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-brand-muted">User permissions</p>
            <h2 id="user-mcp-drawer-title" className="mt-1 font-serif text-xl font-bold text-brand-ink">MCP access</h2>
            <p className="mt-1 truncate text-xs text-brand-muted">{user.full_name || user.email} · {user.email}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink"
            aria-label="Close MCP access drawer"
          >
            <X size={19} aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
          <section aria-labelledby="user-mcp-status-heading">
            <h3 id="user-mcp-status-heading" className="sr-only">Effective MCP status</h3>
            <div className="rounded-xl border border-brand-line bg-brand-bg px-4 py-4">
              <div className="flex items-center gap-2">
                <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${STATUS_STYLES[state.tone]}`} />
                <p className="text-sm font-semibold text-brand-ink">{state.label}</p>
              </div>
              <p className="mt-1 pl-[18px] text-xs leading-5 text-brand-muted">{state.detail}</p>
            </div>
          </section>

          <section className="mt-5 rounded-xl border border-brand-line px-4 py-4" aria-labelledby="user-mcp-policy-heading">
            <div className="flex items-start justify-between gap-5">
              <div>
                <h3 id="user-mcp-policy-heading" className="text-sm font-semibold text-brand-ink">Firm access policy</h3>
                <p className="mt-1 text-xs leading-5 text-brand-muted">
                  Allow this user to authorize external assistants. Privacy Mode remains controlled by the user.
                </p>
              </div>
              <Toggle
                checked={policyEnabled}
                disabled={user.is_active === false || saving}
                label={`Allow Workspace MCP access for ${user.email}`}
                onChange={changePolicy}
              />
            </div>
          </section>

          <section className="mt-6" aria-labelledby="user-mcp-connections-heading">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 id="user-mcp-connections-heading" className="text-sm font-semibold text-brand-ink">Connected clients</h3>
                <p className="mt-1 text-xs text-brand-muted">User-approved OAuth grants only</p>
              </div>
              <button
                type="button"
                onClick={load}
                disabled={loading}
                className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-brand-line px-3 text-xs font-semibold text-brand-ink hover:bg-brand-bg-soft disabled:opacity-50"
              >
                <RefreshCw size={13} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
                Refresh
              </button>
            </div>

            {announcement && <p role="status" className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800">{announcement}</p>}
            {error && (
              <div role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-xs text-red-700">
                <p>{error}</p>
                <button type="button" onClick={load} className="mt-2 font-semibold underline">Try again</button>
              </div>
            )}
            {loading && !hasLoadedGrants ? (
              <p role="status" className="mt-4 rounded-lg bg-brand-bg px-4 py-6 text-center text-sm text-brand-muted">Loading connections…</p>
            ) : error && !hasLoadedGrants ? null : grants.length === 0 ? (
              <div className="mt-4 rounded-lg bg-brand-bg px-4 py-6 text-center">
                <p className="text-sm font-medium text-brand-ink">No active connections</p>
                <p className="mt-1 text-xs leading-5 text-brand-muted">This user has not authorized an assistant, or all prior access has been revoked.</p>
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                {grants.map((grant) => {
                  const scopes = normalizeWorkspaceMcpScopes(grant.scopes || grant.scope || [])
                  return (
                    <article key={grant.id} className="rounded-xl border border-brand-line bg-brand-bg px-4 py-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h4 className="truncate text-sm font-semibold text-brand-ink">{grant.client_name || 'Connected assistant'}</h4>
                          <p className="mt-0.5 truncate font-mono text-[10px] text-brand-muted">{grant.client_id}</p>
                        </div>
                        <button
                          type="button"
                          onClick={() => revoke(grant)}
                          disabled={revoking === grant.id}
                          className="min-h-9 shrink-0 rounded-lg border border-red-200 px-3 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
                        >
                          {revoking === grant.id ? 'Revoking…' : 'Revoke'}
                        </button>
                      </div>
                      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
                        <div><dt className="text-brand-muted">Created</dt><dd className="mt-0.5 text-brand-ink">{formatDate(grant.created_at)}</dd></div>
                        <div><dt className="text-brand-muted">Last used</dt><dd className="mt-0.5 text-brand-ink">{formatDate(grant.last_used_at)}</dd></div>
                        <div><dt className="text-brand-muted">Expires</dt><dd className="mt-0.5 text-brand-ink">{formatDate(grant.expires_at)}</dd></div>
                      </dl>
                      {scopes.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {scopes.map((scope) => <span key={scope.id} className="rounded-md border border-brand-line bg-brand-surface px-2 py-1 text-[10px] text-brand-ink">{scope.label}</span>)}
                        </div>
                      )}
                    </article>
                  )
                })}
              </div>
            )}
          </section>
        </div>

        <footer className="flex shrink-0 flex-col items-stretch gap-3 border-t border-brand-line px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p className="text-[10px] leading-4 text-brand-muted">URLs, tool catalog, and client setup live under MCP Servers.</p>
          <button
            type="button"
            onClick={onNavigateMcp}
            className="inline-flex min-h-9 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-brand-line px-3 text-xs font-semibold text-brand-ink hover:bg-brand-bg-soft"
          >
            MCP Servers <ExternalLink size={13} aria-hidden="true" />
          </button>
        </footer>
      </aside>
    </div>
  )
}
