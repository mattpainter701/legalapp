import React, { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from '../App'
import {
  getAdminUsers,
  deactivateUser,
  reactivateUser,
  updateUser,
  inviteUser,
  getAdminUsage,
  getUsageByUser,
  getAdminTenant,
  getAdminSettings,
  updateAdminSettings,
  getAlertConfig,
  updateAlertConfig,
  listRoles,
  assignUserRoles,
} from '../api'
import { format } from 'date-fns'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import PromptAdminPage from './PromptAdminPage'
import CloudSearchAdmin from './CloudSearchAdmin'
import MCPPage from './MCPPage'
import RolesTab from './admin/RolesTab'
import SmbAdminPage from './SmbAdminPage'
import LicensingPanel from '../components/LicensingPanel'
import IntegrationsPanel from '../components/IntegrationsPanel'
import TeamsPanel from '../components/TeamsPanel'
import ZoomPanel from '../components/ZoomPanel'
import QBOPanel from '../components/QBOPanel'
import FirmBrandingPanel from '../components/FirmBrandingPanel'
import BillingPage from './BillingPage'
import { Spinner, Toggle } from '../components/ui'
import { ArrowLeft, UserPlus, ChevronDown, ChevronRight, X } from 'lucide-react'

function ErrorMsg({ msg }) {
  return (
    <p className="text-brand-rose text-sm font-sans py-4 bg-brand-rose/10 px-4 rounded-lg border border-brand-rose/20">
      {msg}
    </p>
  )
}

function StatCard({ label, value, sub }) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6 shadow-sm hover:border-brand-line-2 transition-colors">
      <p className="text-xs text-brand-muted font-sans uppercase tracking-wider mb-2 font-medium">
        {label}
      </p>
      <p className="text-3xl font-bold text-brand-ink font-serif tracking-tight">{value ?? '—'}</p>
      {sub && <p className="text-sm text-brand-ink-2 mt-2 font-sans">{sub}</p>}
    </div>
  )
}

const ADMIN_TABS = [
  { id: 'users', label: 'Users' },
  { id: 'roles', label: 'Roles' },
  { id: 'licensing', label: 'Licensing' },
  { id: 'billing', label: 'Subscription' },
  { id: 'usage', label: 'Usage' },
  { id: 'mcp', label: 'MCP Servers' },
  { id: 'tenant', label: 'Tenant' },
  { id: 'prompts', label: 'Prompts' },
  { id: 'cloud-search', label: 'Cloud Search' },
  { id: 'smb', label: 'File Shares' },
  { id: 'integrations', label: 'Integrations' },
  { id: 'teams', label: 'Teams' },
  { id: 'zoom', label: 'Zoom' },
  { id: 'qbo', label: 'QuickBooks' },
  { id: 'settings', label: 'Settings' },
]

const ACCOUNTANT_TABS = ADMIN_TABS.filter((tab) =>
  ['licensing', 'billing', 'usage', 'qbo'].includes(tab.id)
)

// Keep the standalone intake product focused on the few settings needed to
// launch and operate a reception team. Unrelated platform integrations remain
// hidden until the tenant upgrades.
const INTAKE_ADMIN_TABS = ADMIN_TABS.filter((tab) =>
  ['users', 'licensing', 'billing', 'usage', 'tenant', 'zoom', 'settings'].includes(tab.id)
)
const INTAKE_ACCOUNTANT_TABS = INTAKE_ADMIN_TABS.filter((tab) =>
  ['licensing', 'billing', 'usage'].includes(tab.id)
)

// ── Invite Modal ──────────────────────────────────────────────────────────────

function InviteModal({ onClose, onSuccess }) {
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [role, setRole] = useState('user')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!email.trim()) return
    setSaving(true)
    setError(null)
    try {
      await inviteUser({ email: email.trim(), full_name: fullName.trim() || undefined, role })
      onSuccess()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to send invitation.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-brand-surface rounded-2xl border border-brand-line shadow-2xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-6 py-5 border-b border-brand-line">
          <h2 className="font-serif font-bold text-lg text-brand-ink">Invite User</h2>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink transition-colors">
            <X size={18} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          {error && <ErrorMsg msg={error} />}
          <div>
            <label htmlFor="adminpage-email-address" className="block text-xs font-semibold text-brand-ink font-sans mb-1.5 uppercase tracking-wider">
              Email address <span className="text-brand-rose">*</span>
            </label>
            <input id="adminpage-email-address"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="colleague@firm.com"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
          </div>
          <div>
            <label htmlFor="adminpage-full-name" className="block text-xs font-semibold text-brand-ink font-sans mb-1.5 uppercase tracking-wider">
              Full name <span className="text-brand-muted font-normal">(optional)</span>
            </label>
            <input id="adminpage-full-name"
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Jane Smith"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
          </div>
          <div>
            <label htmlFor="adminpage-role" className="block text-xs font-semibold text-brand-ink font-sans mb-1.5 uppercase tracking-wider">
              Role
            </label>
            <select id="adminpage-role"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            >
              <option value="user">User</option>
              <option value="accountant">Accountant</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div className="flex gap-3 pt-2">
            <button
              type="submit"
              disabled={saving || !email.trim()}
              className="flex-1 px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink/90 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Sending invite…' : 'Send invitation'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2.5 border border-brand-line text-brand-ink text-sm font-sans rounded-lg hover:bg-brand-bg-soft transition-colors"
            >
              Cancel
            </button>
          </div>
          <p className="text-xs text-brand-muted font-sans">
            An invitation email will be sent. If your firm has Microsoft or Google connected, it routes through that integration.
          </p>
        </form>
      </div>
    </div>
  )
}

// ── Budget popover ────────────────────────────────────────────────────────────

function BudgetCell({ user, billingTier, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(user.payg_monthly_budget != null ? String(user.payg_monthly_budget) : '')
  const [saving, setSaving] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (editing && ref.current) ref.current.focus()
  }, [editing])

  if (billingTier !== 'payg') return <td className="px-6 py-4 text-brand-muted font-sans text-xs">—</td>

  const budget = user.payg_monthly_budget
  const cost = user.cost_usd || 0
  const pct = budget ? Math.min(100, Math.round((cost / budget) * 100)) : null

  const handleSave = async () => {
    setSaving(true)
    try {
      const parsed = value.trim() === '' ? -1 : parseFloat(value)
      await updateUser(user.id, { payg_monthly_budget: isNaN(parsed) ? -1 : parsed })
      onSaved()
      setEditing(false)
    } catch {
      // ignore — UI stays open
    } finally {
      setSaving(false)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter') handleSave()
    if (e.key === 'Escape') setEditing(false)
  }

  return (
    <td className="px-6 py-4">
      {editing ? (
        <div className="flex items-center gap-1">
          <span className="text-brand-muted text-xs">$</span>
          <input
            ref={ref}
            type="number"
            min="0"
            step="1"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKey}
            placeholder="No cap"
            className="w-20 px-2 py-1 border border-brand-ink/30 rounded text-xs font-sans focus:outline-none focus:ring-1 focus:ring-brand-ink/40"
          />
          <button
            onClick={handleSave}
            disabled={saving}
            className="text-[11px] bg-brand-ink text-white px-2 py-1 rounded font-sans disabled:opacity-40"
          >
            {saving ? '…' : 'OK'}
          </button>
          <button onClick={() => setEditing(false)} className="text-[11px] text-brand-muted hover:text-brand-ink">✕</button>
        </div>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="text-left group"
          title="Click to set monthly budget cap"
        >
          {budget != null ? (
            <div>
              <div className="flex items-center gap-1.5">
                <span className={`text-xs font-sans font-medium ${pct >= 100 ? 'text-brand-rose' : pct >= 80 ? 'text-amber-600' : 'text-brand-ink'}`}>
                  ${budget.toFixed(0)}/mo
                </span>
                {pct != null && (
                  <span className={`text-[10px] font-sans px-1.5 py-0.5 rounded-full font-bold ${pct >= 100 ? 'bg-brand-rose/10 text-brand-rose' : pct >= 80 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'}`}>
                    {pct}%
                  </span>
                )}
              </div>
              <div className="w-20 h-1.5 bg-brand-line rounded-full mt-1 overflow-hidden">
                <div
                  className={`h-full rounded-full ${pct >= 100 ? 'bg-brand-rose' : pct >= 80 ? 'bg-amber-400' : 'bg-green-500'}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          ) : (
            <span className="text-xs text-brand-muted group-hover:text-brand-ink transition-colors font-sans">
              No cap — set
            </span>
          )}
        </button>
      )}
    </td>
  )
}

// ── Rate cell (inline edit for user billing rate) ────────────────────────────

function RateCell({ user, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(user.default_billing_rate != null ? String(user.default_billing_rate) : '')
  const [saving, setSaving] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (editing && ref.current) ref.current.focus()
  }, [editing])

  const handleSave = async () => {
    setSaving(true)
    try {
      const parsed = value.trim() === '' ? -1 : parseFloat(value)
      await updateUser(user.id, { default_billing_rate: isNaN(parsed) ? -1 : parsed })
      onSaved()
      setEditing(false)
    } catch {
    } finally {
      setSaving(false)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter') handleSave()
    if (e.key === 'Escape') setEditing(false)
  }

  const rate = user.default_billing_rate

  return (
    <span>
      {editing ? (
        <span className="inline-flex items-center gap-1">
          <span className="text-brand-muted text-xs">$</span>
          <input
            ref={ref}
            type="number"
            min="0"
            step="0.01"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKey}
            placeholder="0.00"
            className="w-20 px-2 py-1 border border-brand-ink/30 rounded text-xs font-sans focus:outline-none focus:ring-1 focus:ring-brand-ink/40"
          />
          <button
            onClick={handleSave}
            disabled={saving}
            className="text-[11px] bg-brand-ink text-white px-2 py-1 rounded font-sans disabled:opacity-40"
          >
            {saving ? '…' : 'OK'}
          </button>
          <button onClick={() => setEditing(false)} className="text-[11px] text-brand-muted hover:text-brand-ink">✕</button>
        </span>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="text-left group"
          title="Click to set default billing rate"
        >
          {rate != null ? (
            <span className="text-brand-ink font-sans text-xs font-medium group-hover:text-brand-ink/70">
              ${Number(rate).toFixed(2)}/hr
            </span>
          ) : (
            <span className="text-xs text-brand-muted group-hover:text-brand-ink transition-colors font-sans">
              No rate — set
            </span>
          )}
        </button>
      )}
    </span>
  )
}

// ── Per-user role assignment ─────────────────────────────────────────────────

function RoleAssignCell({ user, roles, saving, disabled, onAssign }) {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState([])
  const ref = useRef(null)

  // Seed selection from any role_ids the backend may surface on the user row.
  useEffect(() => {
    if (Array.isArray(user.role_ids)) {
      setSelected(user.role_ids.map((id) => String(id)))
    } else if (Array.isArray(user.roles)) {
      setSelected(user.roles.map((r) => String(r.id ?? r)))
    }
  }, [user])

  useEffect(() => {
    if (!open) return
    const onClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const toggle = (id) =>
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )

  const save = () => {
    setOpen(false)
    onAssign(selected)
  }

  // The badge must reflect what's actually assigned (confirmed role_ids from
  // the backend), not the legacy single `user.role` string — otherwise a
  // successful assignment looks like it did nothing.
  const assignedIds = Array.isArray(user.role_ids)
    ? user.role_ids.map(String)
    : (Array.isArray(user.roles) ? user.roles.map((r) => String(r.id ?? r)) : [])
  const assignedNames = assignedIds
    .map((id) => roles.find((r) => String(r.id) === id)?.name)
    .filter(Boolean)
  const label = assignedNames.length ? assignedNames.join(', ') : user.role

  if (disabled) {
    return (
      <span className="inline-flex px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide bg-brand-line/50 text-brand-muted border border-brand-line">
        {user.role}
      </span>
    )
  }

  return (
    <div className="relative inline-block" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={saving}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide cursor-pointer hover:opacity-80 transition-colors bg-brand-line/50 text-brand-muted border border-brand-line disabled:opacity-50"
        title="Assign roles"
      >
        <span className="max-w-[140px] truncate">{saving ? '…' : label}</span>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-56 bg-brand-surface border border-brand-line rounded-lg shadow-lg p-2">
          {roles.length === 0 ? (
            <p className="text-xs text-brand-muted font-sans px-1 py-1">No roles defined yet.</p>
          ) : (
            <div className="max-h-48 overflow-y-auto space-y-1">
              {roles.map((r) => {
                const id = String(r.id)
                return (
                  <label key={id} className="flex items-center gap-2 text-xs font-sans px-1 py-1 hover:bg-brand-bg-soft rounded cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selected.includes(id)}
                      onChange={() => toggle(id)}
                    />
                    <span className="text-brand-ink">{r.name}{r.is_system ? ' (system)' : ''}</span>
                  </label>
                )
              })}
            </div>
          )}
          <div className="flex justify-end gap-2 mt-2 pt-2 border-t border-brand-line">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-xs font-sans text-brand-muted hover:text-brand-ink px-2 py-1"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={save}
              className="text-xs font-sans font-medium text-white bg-brand-ink rounded px-3 py-1 hover:bg-brand-ink/90"
            >
              Save
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Tab: Users ───────────────────────────────────────────────────────────────

function UsersTab({ billingTier }) {
  const confirmAction = useConfirm()
  const [users, setUsers] = useState([])
  const [usageByUser, setUsageByUser] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [deactivating, setDeactivating] = useState(null)
  const [reactivating, setReactivating] = useState(null)
  const [changingRole, setChangingRole] = useState(null)
  const [availableRoles, setAvailableRoles] = useState([])
  const [showInvite, setShowInvite] = useState(false)
  const [showInactive, setShowInactive] = useState(false)
  const [successMsg, setSuccessMsg] = useState(null)

  const loadUsers = async () => {
    try {
      const [u, usage] = await Promise.all([getAdminUsers(), getUsageByUser(30)])
      setUsers(u)
      const map = {}
      for (const row of usage.users) {
        map[row.user_id] = row
      }
      setUsageByUser(map)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadUsers() }, [])
  useEffect(() => {
    listRoles().then(setAvailableRoles).catch(() => {})
  }, [])

  const flash = (msg) => {
    setSuccessMsg(msg)
    setTimeout(() => setSuccessMsg(null), 4000)
  }

  const handleDeactivate = async (u, force = false) => {
    setDeactivating(u.id)
    try {
      await deactivateUser(u.id, force)
      flash(`${u.email} set inactive.`)
      loadUsers()
    } catch (e) {
      const detail = e?.response?.data?.detail || 'Failed to update user status'
      if (!force && detail.includes('org-wide OAuth consent')) {
        const proceed = await confirmAction({ title: 'Deactivate consent owner?', message: `${detail} Turn this user inactive anyway?`, confirmLabel: 'Deactivate user', destructive: true })
        if (proceed) {
          setDeactivating(null)
          await handleDeactivate(u, true)
          return
        }
      }
      setError(detail)
    } finally {
      setDeactivating(null)
    }
  }

  const handleReactivate = async (u) => {
    setReactivating(u.id)
    try {
      await reactivateUser(u.id)
      flash(`${u.email} set active.`)
      loadUsers()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to reactivate user')
    } finally {
      setReactivating(null)
    }
  }

  const handleActiveToggle = (u) => {
    if (u.is_active === false) {
      handleReactivate(u)
    } else {
      handleDeactivate(u)
    }
  }

  const handleAssignRoles = async (u, roleIds) => {
    if (!await confirmAction({ title: 'Update role assignments?', message: `Apply these role changes for ${u.email}?`, confirmLabel: 'Update roles' })) return
    setChangingRole(u.id)
    try {
      await assignUserRoles(u.id, roleIds)
      flash(`Roles updated for ${u.email}.`)
      loadUsers()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to update roles')
    } finally {
      setChangingRole(null)
    }
  }

  if (loading) return <Spinner />
  if (error) return <ErrorMsg msg={error} />

  const activeUsers = users.filter((u) => u.is_active !== false)
  const inactiveUsers = users.filter((u) => u.is_active === false)
  const displayUsers = showInactive ? users : activeUsers

  return (
    <div className="space-y-4">
      {showInvite && (
        <InviteModal
          onClose={() => setShowInvite(false)}
          onSuccess={() => {
            setShowInvite(false)
            flash('Invitation sent.')
            loadUsers()
          }}
        />
      )}

      {/* Toolbar */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <p className="text-sm text-brand-muted font-sans">
            {activeUsers.length} active{inactiveUsers.length > 0 && `, ${inactiveUsers.length} inactive`}
          </p>
          {inactiveUsers.length > 0 && (
            <button
              onClick={() => setShowInactive((v) => !v)}
              className="text-xs text-brand-muted hover:text-brand-ink font-sans underline transition-colors"
            >
              {showInactive ? 'Hide inactive' : 'Show inactive'}
            </button>
          )}
        </div>
        <button
          onClick={() => setShowInvite(true)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink/90 transition-colors"
        >
          <UserPlus size={15} />
          Invite user
        </button>
      </div>

      {successMsg && (
        <div className="px-4 py-2.5 bg-green-50 border border-green-200 rounded-lg text-green-700 text-sm font-sans">
          {successMsg}
        </div>
      )}

      <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-brand-line bg-brand-bg-soft/50">
              <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">User</th>
              <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Role</th>
              <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Rate</th>
              <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Joined</th>
              <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Usage (30d)</th>
              {billingTier === 'payg' && (
                <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Budget cap</th>
              )}
              <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Active</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-line">
            {displayUsers.map((u) => {
              const usage = usageByUser[u.id]
              const isInactive = u.is_active === false
              return (
                <tr
                  key={u.id}
                  className={`hover:bg-brand-bg-soft transition-colors ${isInactive ? 'opacity-60' : ''}`}
                >
                  <td className="px-6 py-4">
                    <p className="text-brand-ink font-sans font-medium text-sm">{u.full_name || u.email}</p>
                    {u.full_name && <p className="text-brand-muted font-sans text-xs">{u.email}</p>}
                  </td>
                  <td className="px-6 py-4">
                    <RoleAssignCell
                      user={u}
                      roles={availableRoles}
                      saving={changingRole === u.id}
                      disabled={isInactive}
                      onAssign={(roleIds) => handleAssignRoles(u, roleIds)}
                    />
                  </td>
                  <td className="px-6 py-4">
                    <RateCell
                      user={u}
                      onSaved={() => { flash('Rate updated.'); loadUsers() }}
                    />
                  </td>
                  <td className="px-6 py-4 text-brand-muted font-sans text-xs">
                    {u.created_at ? format(new Date(u.created_at), 'MMM d, yyyy') : '—'}
                  </td>
                  <td className="px-6 py-4">
                    {usage ? (
                      <div>
                        <p className="text-brand-ink font-sans text-xs font-medium">${usage.total_cost_usd.toFixed(2)}</p>
                        <p className="text-brand-muted font-sans text-[11px]">{(usage.total_tokens_in + usage.total_tokens_out).toLocaleString()} tokens</p>
                      </div>
                    ) : (
                      <span className="text-brand-muted font-sans text-xs">No usage</span>
                    )}
                  </td>
                  {billingTier === 'payg' && (
                    <BudgetCell
                      user={{ ...u, cost_usd: usage?.total_cost_usd }}
                      billingTier={billingTier}
                      onSaved={() => { flash('Budget updated.'); loadUsers() }}
                    />
                  )}
                  <td className="px-6 py-4">
                    <div className="inline-flex items-center gap-3">
                      <button
                        type="button"
                        onClick={() => handleActiveToggle(u)}
                        disabled={deactivating === u.id || reactivating === u.id}
                        aria-pressed={!isInactive}
                        aria-label={`${u.email} is ${isInactive ? 'inactive' : 'active'}`}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-brand-ink/20 disabled:opacity-50 ${
                          !isInactive ? 'bg-brand-green' : 'bg-gray-300'
                        }`}
                      >
                        <span
                          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                            !isInactive ? 'translate-x-6' : 'translate-x-1'
                          }`}
                        />
                      </button>
                      <span className={`text-xs font-sans font-medium ${!isInactive ? 'text-brand-green' : 'text-brand-muted'}`}>
                        {deactivating === u.id || reactivating === u.id
                          ? 'Saving...'
                          : !isInactive ? 'Active' : 'Inactive'}
                      </span>
                    </div>
                  </td>
                </tr>
              )
            })}
            {displayUsers.length === 0 && (
              <tr>
                <td colSpan={billingTier === 'payg' ? 7 : 6} className="px-6 py-12 text-center text-brand-muted font-sans text-sm">
                  No users found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Tab: Usage ───────────────────────────────────────────────────────────────

function UsageTab() {
  const [summary, setSummary] = useState(null)
  const [byUser, setByUser] = useState(null)
  const [days, setDays] = useState(30)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = async (d) => {
    setLoading(true)
    setError(null)
    try {
      const [s, u] = await Promise.all([getAdminUsage(), getUsageByUser(d)])
      setSummary(s)
      setByUser(u)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load usage')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(days) }, [days])

  const formatNumber = (n) => (n != null ? Number(n).toLocaleString() : '—')
  const formatCost = (n) => (n != null ? `$${Number(n).toFixed(4)}` : '—')

  if (loading) return <Spinner />
  if (error) return <ErrorMsg msg={error} />

  return (
    <div className="space-y-8">
      {/* Summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard label="Total Requests" value={formatNumber(summary?.request_count)} sub="Last 30 days" />
        <StatCard label="Tokens In" value={formatNumber(summary?.total_tokens_in)} sub="Prompt tokens" />
        <StatCard label="Tokens Out" value={formatNumber(summary?.total_tokens_out)} sub="Completion tokens" />
        <StatCard label="Total Cost" value={formatCost(summary?.total_cost_usd)} sub="Estimated USD" />
      </div>

      {/* Per-user breakdown */}
      <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <h3 className="font-serif font-bold text-brand-ink">Per-User Breakdown</h3>
          <div className="flex items-center gap-2">
            {[7, 30, 90].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`px-3 py-1 rounded-lg text-xs font-sans font-medium transition-colors ${
                  days === d ? 'bg-brand-ink text-white' : 'bg-brand-bg-soft text-brand-muted hover:text-brand-ink'
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-brand-line bg-brand-bg-soft/50">
                <th className="text-left px-6 py-3 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">User</th>
                <th className="text-right px-6 py-3 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Requests</th>
                <th className="text-right px-6 py-3 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Tokens in</th>
                <th className="text-right px-6 py-3 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Tokens out</th>
                <th className="text-right px-6 py-3 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">Cost (USD)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-brand-line">
              {(byUser?.users || []).map((row) => (
                <tr key={row.user_id} className="hover:bg-brand-bg-soft transition-colors">
                  <td className="px-6 py-3 text-brand-ink font-sans text-sm">{row.user_email}</td>
                  <td className="px-6 py-3 text-right text-brand-ink-2 font-sans text-sm">{row.request_count.toLocaleString()}</td>
                  <td className="px-6 py-3 text-right text-brand-ink-2 font-sans text-sm">{row.total_tokens_in.toLocaleString()}</td>
                  <td className="px-6 py-3 text-right text-brand-ink-2 font-sans text-sm">{row.total_tokens_out.toLocaleString()}</td>
                  <td className="px-6 py-3 text-right text-brand-ink font-mono text-sm font-medium">${row.total_cost_usd.toFixed(4)}</td>
                </tr>
              ))}
              {(byUser?.users || []).length === 0 && (
                <tr>
                  <td colSpan={5} className="px-6 py-8 text-center text-brand-muted font-sans text-sm">No usage data.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Tab: Tenant ──────────────────────────────────────────────────────────────

function TenantTab() {
  const [tenant, setTenant] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getAdminTenant()
      .then(setTenant)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load tenant'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Spinner />
  if (error) return <ErrorMsg msg={error} />
  if (!tenant) return <p className="text-brand-muted text-sm font-sans py-4">No tenant data available.</p>

  return (
    <div className="max-w-3xl">
      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
          <h3 className="font-serif font-bold text-xl text-brand-ink">Tenant Information</h3>
        </div>
        <div className="divide-y divide-brand-line">
          {[
            ['Tenant ID', tenant.id],
            ['Name', tenant.name],
            ['Domain', tenant.domain],
            ['Billing Tier', tenant.billing_tier],
            ['Max Users', tenant.max_users],
            ['Max Documents', tenant.max_documents],
            ['Created', tenant.created_at ? format(new Date(tenant.created_at), 'MMMM d, yyyy') : '—'],
            ['Status', tenant.is_active !== false ? 'Active' : 'Inactive'],
          ].map(([label, value]) => (
            <div key={label} className="flex px-8 py-4 items-center">
              <span className="w-48 text-sm text-brand-muted font-sans font-medium tracking-wide flex-shrink-0">{label}</span>
              <span className="text-sm text-brand-ink font-sans font-medium">{value ?? '—'}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Settings ─────────────────────────────────────────────────────────────────

function AlertsSection() {
  const [cfg, setCfg] = useState({
    spend_alert_usd: '',
    spend_alert_pct: 80,
    alert_emails: '',
    weekly_digest_enabled: true,
  })
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)

  useEffect(() => {
    getAlertConfig()
      .then((data) => {
        setCfg({
          spend_alert_usd: data.spend_alert_usd != null ? String(data.spend_alert_usd) : '',
          spend_alert_pct: data.spend_alert_pct ?? 80,
          alert_emails: (data.alert_emails || []).join(', '),
          weekly_digest_enabled: data.weekly_digest_enabled !== false,
        })
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateAlertConfig({
        spend_alert_usd: cfg.spend_alert_usd !== '' ? parseFloat(cfg.spend_alert_usd) : null,
        spend_alert_pct: Number(cfg.spend_alert_pct),
        alert_emails: cfg.alert_emails.split(',').map((e) => e.trim()).filter(Boolean),
        weekly_digest_enabled: cfg.weekly_digest_enabled,
      })
      setMsg({ type: 'success', text: 'Alert settings saved.' })
    } catch (err) {
      setMsg({ type: 'error', text: err?.response?.data?.detail || 'Failed to save.' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 4000)
    }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
      <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
        <h3 className="font-serif font-bold text-xl text-brand-ink">Alerts &amp; Budgets</h3>
        <p className="text-sm text-brand-ink-2 font-sans mt-1">
          Configure spend alerts and digest emails. Alerts are sent via your connected Microsoft/Google integration, or SMTP.
        </p>
      </div>
      <div className="px-8 py-5 space-y-5">
        {msg && (
          <div className={`px-4 py-2.5 rounded-lg text-sm font-sans ${msg.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
            {msg.text}
          </div>
        )}

        {/* Monthly threshold */}
        <div>
          <label htmlFor="admin-monthly-spend-threshold" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
            Monthly spend alert threshold (USD)
          </label>
          <div className="flex items-center gap-3">
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted text-sm">$</span>
              <input id="admin-monthly-spend-threshold"
                type="number"
                min="0"
                step="10"
                value={cfg.spend_alert_usd}
                onChange={(e) => setCfg((c) => ({ ...c, spend_alert_usd: e.target.value }))}
                placeholder="No limit"
                className="pl-7 pr-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 w-36 placeholder:text-brand-muted"
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-brand-muted font-sans">Alert at</span>
              <select
                aria-label="Spend alert percentage"
                value={cfg.spend_alert_pct}
                onChange={(e) => setCfg((c) => ({ ...c, spend_alert_pct: Number(e.target.value) }))}
                className="px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
              >
                {[50, 70, 80, 90, 100].map((p) => <option key={p} value={p}>{p}%</option>)}
              </select>
              <span className="text-sm text-brand-muted font-sans">of threshold</span>
            </div>
          </div>
          <p className="text-xs text-brand-muted font-sans mt-1.5">Leave blank to disable tenant-wide spend alerts.</p>
        </div>

        {/* Alert recipients */}
        <div>
          <label htmlFor="adminpage-alert-recipients" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
            Alert recipients
          </label>
          <input id="adminpage-alert-recipients"
            type="text"
            value={cfg.alert_emails}
            onChange={(e) => setCfg((c) => ({ ...c, alert_emails: e.target.value }))}
            placeholder="admin@firm.com, billing@firm.com"
            className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
          />
          <p className="text-xs text-brand-muted font-sans mt-1.5">Comma-separated email addresses. Leave blank to alert all admins.</p>
        </div>

        {/* Weekly digest */}
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-sans font-semibold text-brand-ink">Weekly usage digest</p>
            <p className="text-xs text-brand-ink-2 font-sans mt-0.5">Email a weekly summary of token usage and costs to alert recipients.</p>
          </div>
          <Toggle
            checked={cfg.weekly_digest_enabled}
            onChange={(v) => setCfg((c) => ({ ...c, weekly_digest_enabled: v }))}
            label="Weekly digest"
          />
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={handleSave}
            disabled={saving || !loaded}
            className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink/90 disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving…' : 'Save alerts'}
          </button>
        </div>
      </div>
    </div>
  )
}

function FeatureFlagsSection({ settings, onUpdate }) {
  const [s, setS] = useState(settings)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)

  const flags = [
    { key: 'enable_auto_memory', label: 'Auto memory', desc: 'Automatically build per-user memory from conversations' },
    { key: 'enable_pii_detection', label: 'PII detection', desc: 'Flag and suppress personally identifiable information in outputs' },
    { key: 'enable_skill_routing', label: 'Skill routing', desc: 'Route queries to domain-specific legal skills automatically' },
    { key: 'enable_matter_context', label: 'Matter context', desc: 'Inject active matter context into chat and skills' },
    { key: 'enable_task_board', label: 'Legal Work Board', desc: 'Offer the board workflow alongside the deadline list for this firm' },
  ]

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateAdminSettings({
        enable_auto_memory: s.enable_auto_memory,
        enable_pii_detection: s.enable_pii_detection,
        enable_skill_routing: s.enable_skill_routing,
        enable_matter_context: s.enable_matter_context,
        enable_task_board: s.enable_task_board,
        max_requests_per_minute: s.max_requests_per_minute || null,
        max_daily_tokens: s.max_daily_tokens || null,
      })
      onUpdate(s)
      setMsg({ type: 'success', text: 'Settings saved.' })
    } catch (err) {
      setMsg({ type: 'error', text: err?.response?.data?.detail || 'Failed.' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 4000)
    }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
      <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
        <h3 className="font-serif font-bold text-xl text-brand-ink">Feature Flags</h3>
        <p className="text-sm text-brand-ink-2 font-sans mt-1">Enable or disable platform features for all users in this tenant.</p>
      </div>
      <div className="divide-y divide-brand-line">
        {flags.map(({ key, label, desc }) => (
          <div key={key} className="flex items-center justify-between px-8 py-5">
            <div>
              <p className="text-sm font-sans font-semibold text-brand-ink">{label}</p>
              <p className="text-xs text-brand-ink-2 font-sans mt-0.5">{desc}</p>
            </div>
            <Toggle
              checked={!!s[key]}
              onChange={(v) => setS((prev) => ({ ...prev, [key]: v }))}
              label={label}
            />
          </div>
        ))}

        {/* Rate limits */}
        <div className="px-8 py-5 space-y-4">
          <p className="text-sm font-sans font-semibold text-brand-ink">Rate limits</p>
          <div className="flex gap-6">
            <div>
              <label htmlFor="adminpage-requests-minute" className="block text-xs text-brand-muted font-sans mb-1">Requests / minute</label>
              <input id="adminpage-requests-minute"
                type="number"
                min="0"
                value={s.max_requests_per_minute ?? ''}
                onChange={(e) => setS((prev) => ({ ...prev, max_requests_per_minute: e.target.value ? parseInt(e.target.value) : null }))}
                placeholder="No limit"
                className="w-28 px-3 py-2 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
              />
            </div>
            <div>
              <label htmlFor="adminpage-tokens-day" className="block text-xs text-brand-muted font-sans mb-1">Tokens / day</label>
              <input id="adminpage-tokens-day"
                type="number"
                min="0"
                value={s.max_daily_tokens ?? ''}
                onChange={(e) => setS((prev) => ({ ...prev, max_daily_tokens: e.target.value ? parseInt(e.target.value) : null }))}
                placeholder="No limit"
                className="w-32 px-3 py-2 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
              />
            </div>
          </div>
        </div>

        <div className="px-8 py-5 flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink/90 disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          {msg && (
            <span className={`text-sm font-sans ${msg.type === 'success' ? 'text-brand-green' : 'text-brand-rose'}`}>{msg.text}</span>
          )}
        </div>
      </div>
    </div>
  )
}

function SettingsTab() {
  const [includePublic, setIncludePublic] = useState(true)
  const [modelOverride, setModelOverride] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const [existingConfig, setExistingConfig] = useState({})
  const [featureSettings, setFeatureSettings] = useState(null)

  useEffect(() => {
    getAdminSettings()
      .then((s) => {
        setModelOverride(s.default_llm_model || '')
        const cfg = s.custom_config || {}
        setExistingConfig(cfg)
        setIncludePublic(cfg.include_public_case_law !== false)
        setFeatureSettings(s)
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateAdminSettings({
        default_llm_provider: modelOverride ? 'litellm' : null,
        default_llm_model: modelOverride || null,
        custom_config: { ...existingConfig, include_public_case_law: includePublic },
      })
      setExistingConfig((prev) => ({ ...prev, include_public_case_law: includePublic }))
      setMsg({ type: 'success', text: 'Settings saved.' })
    } catch (err) {
      setMsg({ type: 'error', text: err?.response?.data?.detail || 'Failed to save.' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 4000)
    }
  }

  return (
    <div className="max-w-3xl space-y-8">
      {/* Case Law */}
      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
          <h3 className="font-serif font-bold text-xl text-brand-ink">Case Law Settings</h3>
          <p className="text-sm text-brand-ink-2 font-sans mt-1">Control which legal databases are included in retrieval.</p>
        </div>
        <div className="divide-y divide-brand-line">
          <div className="flex items-center justify-between px-8 py-5">
            <div>
              <p className="text-sm font-sans font-semibold text-brand-ink">Public case law search</p>
              <p className="text-xs text-brand-ink-2 font-sans mt-1">Include CourtListener public opinions in RAG retrieval</p>
            </div>
            <Toggle checked={includePublic} onChange={setIncludePublic} label="Public case law search" />
          </div>
        </div>
      </div>

      {/* LiteLLM Gateway */}
      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
          <h3 className="font-serif font-bold text-xl text-brand-ink">LiteLLM Gateway</h3>
          <p className="text-sm text-brand-ink-2 font-sans mt-1">
            Optional tenant gateway alias override. Provider routing and fallback chains are managed in LiteLLM.
          </p>
        </div>
        <div className="divide-y divide-brand-line px-8 py-5 space-y-5">
          <div>
            <label htmlFor="adminpage-standard-alias-override" className="block text-sm font-sans font-semibold text-brand-ink mb-2">
              Standard alias override <span className="text-brand-ink-2 font-normal">(optional)</span>
            </label>
            <input id="adminpage-standard-alias-override"
              type="text"
              value={modelOverride}
              onChange={(e) => setModelOverride(e.target.value)}
              placeholder="e.g. clarity-standard, clarity-standard-openrouter-free"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
            <p className="text-xs text-brand-muted font-sans mt-1.5">Leave blank to use the platform standard alias.</p>
          </div>
          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={handleSave}
              disabled={saving || !loaded}
              className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink/90 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            {msg && (
              <span className={`text-sm font-sans ${msg.type === 'success' ? 'text-brand-green' : 'text-brand-rose'}`}>{msg.text}</span>
            )}
          </div>
        </div>
      </div>

      {/* Feature Flags */}
      {featureSettings && (
        <FeatureFlagsSection settings={featureSettings} onUpdate={(s) => setFeatureSettings(s)} />
      )}

      {/* Firm Branding */}
      <FirmBrandingPanel />

      {/* Alerts & Budgets */}
      <AlertsSection />
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function AdminPage() {
  const { user } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialTab = searchParams.get('tab')
  const tabs = user?.plan === 'intake-only'
    ? (user?.role === 'accountant' ? INTAKE_ACCOUNTANT_TABS : INTAKE_ADMIN_TABS)
    : (user?.role === 'accountant' ? ACCOUNTANT_TABS : ADMIN_TABS)
  const defaultTab = tabs[0]?.id || 'licensing'
  const [activeTab, setActiveTab] = useState(
    tabs.some((t) => t.id === initialTab) ? initialTab : defaultTab
  )
  const [billingTier, setBillingTier] = useState('payg')
  const [tabsCollapsed, setTabsCollapsed] = useState(false)

  // Fetch billing tier once so Users and Licensing tabs can use it
  useEffect(() => {
    getAdminTenant()
      .then((t) => setBillingTier(t.billing_tier || 'payg'))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const tab = searchParams.get('tab')
    if (tab && tabs.some((t) => t.id === tab) && tab !== activeTab) {
      setActiveTab(tab)
    } else if (!tabs.some((t) => t.id === activeTab)) {
      setActiveTab(defaultTab)
      setSearchParams(defaultTab === 'users' ? {} : { tab: defaultTab })
    } else if (tab && !tabs.some((t) => t.id === tab)) {
      setActiveTab(defaultTab)
      setSearchParams(defaultTab === 'users' ? {} : { tab: defaultTab })
    }
  }, [searchParams, activeTab, tabs, defaultTab, setSearchParams])

  const selectTab = (tab) => {
    setActiveTab(tab)
    setSearchParams(tab === 'users' ? {} : { tab })
  }

  return (
    <div className="">
      {/* Content */}
      <div className="max-w-6xl mx-auto px-4 md:px-8 py-8 md:py-12">
        <div className="mb-8 md:mb-10">
          <h1 className="text-3xl md:text-4xl font-bold font-serif text-brand-ink tracking-tight mb-3">
            Administration
          </h1>
          <p className="text-brand-ink-2 text-base font-sans">
            Manage users, monitor usage, and configure your tenant.
          </p>
        </div>

        {/* Collapsible Tabs */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <button
              onClick={() => setTabsCollapsed(!tabsCollapsed)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium text-brand-muted hover:text-brand-ink bg-brand-surface border border-brand-line rounded-lg transition-colors flex-shrink-0"
              title={tabsCollapsed ? 'Show tabs' : 'Hide tabs'}
            >
              {tabsCollapsed ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span className="hidden sm:inline">{tabsCollapsed ? 'Show tabs' : 'Hide tabs'}</span>
            </button>
            {tabsCollapsed && (
              <>
                <span className="text-sm font-sans font-semibold text-brand-ink">{tabs.find(t => t.id === activeTab)?.label || 'Admin'}</span>
                <select
                  value={activeTab}
                  onChange={(e) => selectTab(e.target.value)}
                  className="text-xs font-sans px-2 py-1.5 border border-brand-line rounded-lg bg-brand-surface text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent ml-auto"
                >
                  {tabs.map(t => (
                    <option key={t.id} value={t.id}>{t.label}</option>
                  ))}
                </select>
              </>
            )}
          </div>
          {!tabsCollapsed && (
            <div className="border-b border-brand-line overflow-x-auto">
              <nav className="-mb-px flex gap-4 md:gap-6">
                {tabs.map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => selectTab(tab.id)}
                    className={`pb-4 text-[13px] md:text-[14px] font-sans font-medium border-b-2 transition-all whitespace-nowrap flex-shrink-0 ${
                      activeTab === tab.id
                        ? 'border-brand-accent text-brand-ink'
                        : 'border-transparent text-brand-muted hover:text-brand-ink hover:border-brand-line-2'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </nav>
            </div>
          )}
        </div>

        {/* Tab content */}
        <div className="animate-in fade-in duration-300">
          {activeTab === 'users' && <UsersTab billingTier={billingTier} />}
          {activeTab === 'roles' && <RolesTab />}
          {activeTab === 'licensing' && <LicensingPanel />}
          {activeTab === 'billing' && <BillingPage embedded />}
          {activeTab === 'usage' && <UsageTab />}
          {activeTab === 'mcp' && <MCPPage embedded />}
          {activeTab === 'tenant' && <TenantTab />}
          {activeTab === 'settings' && <SettingsTab />}
          {activeTab === 'prompts' && <PromptAdminPage />}
          {activeTab === 'cloud-search' && <CloudSearchAdmin />}
          {activeTab === 'smb' && <SmbAdminPage />}
          {activeTab === 'integrations' && <IntegrationsPanel />}
          {activeTab === 'teams' && <TeamsPanel />}
          {activeTab === 'zoom' && <ZoomPanel />}
          {activeTab === 'qbo' && <QBOPanel />}
        </div>
      </div>
    </div>
  )
}
