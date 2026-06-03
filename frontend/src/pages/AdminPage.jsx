import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAdminUsers, getAdminUsage, getAdminTenant, configureCustomerLLM, resetCustomerLLM } from '../api'
import { useAuth } from '../App'
import { format } from 'date-fns'
import PromptAdminPage from './PromptAdminPage'
import CloudSearchAdmin from './CloudSearchAdmin'
import LicensingPanel from '../components/LicensingPanel'
import PermissionsAudit from '../components/PermissionsAudit'

// ── Reusable primitives ──────────────────────────────────────────────────────

function Spinner() {
  return (
    <div className="flex justify-center py-16">
      <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
    </div>
  )
}

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

/** Simple accessible toggle switch */
function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer ${
        checked ? 'bg-brand-green' : 'bg-brand-line-2'
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
          checked ? 'translate-x-[18px]' : 'translate-x-1'
        }`}
      />
    </button>
  )
}

// ── Tab: Users ───────────────────────────────────────────────────────────────

function UsersTab() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getAdminUsers()
      .then(setUsers)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load users'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Spinner />
  if (error) return <ErrorMsg msg={error} />

  return (
    <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-brand-line bg-brand-bg-soft/50">
            <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">
              Email
            </th>
            <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">
              Name
            </th>
            <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">
              Role
            </th>
            <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">
              Tier
            </th>
            <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">
              Joined
            </th>
            <th className="text-left px-6 py-4 font-semibold text-brand-ink font-sans text-xs uppercase tracking-wider">
              Status
            </th>
            <th className="px-6 py-4" />
          </tr>
        </thead>
        <tbody className="divide-y divide-brand-line">
          {users.map((u) => (
            <tr key={u.id} className="hover:bg-brand-bg-soft transition-colors">
              <td className="px-6 py-4 text-brand-ink font-sans font-medium">{u.email}</td>
              <td className="px-6 py-4 text-brand-ink-2 font-sans">{u.full_name || '—'}</td>
              <td className="px-6 py-4">
                <span
                  className={`inline-flex px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide ${
                    u.role === 'admin'
                      ? 'bg-brand-ink/10 text-brand-ink border border-brand-ink/20'
                      : 'bg-brand-line/50 text-brand-muted border border-brand-line'
                  }`}
                >
                  {u.role}
                </span>
              </td>
              <td className="px-6 py-4 text-brand-ink-2 font-sans capitalize">
                {u.billing_tier || 'free'}
              </td>
              <td className="px-6 py-4 text-brand-muted font-sans text-xs">
                {u.created_at ? format(new Date(u.created_at), 'MMM d, yyyy') : '—'}
              </td>
              <td className="px-6 py-4">
                <span
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold uppercase tracking-wider ${
                    u.is_active !== false
                      ? 'bg-brand-green/10 text-brand-green border border-brand-green/20'
                      : 'bg-brand-rose/10 text-brand-rose border border-brand-rose/20'
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      u.is_active !== false ? 'bg-brand-green' : 'bg-brand-rose'
                    }`}
                  />
                  {u.is_active !== false ? 'Active' : 'Inactive'}
                </span>
              </td>
              <td className="px-6 py-4 text-right">
                <button
                  className="text-xs text-brand-rose hover:text-brand-rose/80 font-sans font-medium transition-colors"
                  onClick={() =>
                    alert(`Deactivate ${u.email}? (API call not wired in this demo)`)
                  }
                >
                  Deactivate
                </button>
              </td>
            </tr>
          ))}
          {users.length === 0 && (
            <tr>
              <td colSpan={7} className="px-6 py-12 text-center text-brand-muted font-sans text-sm">
                No users found.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ── Tab: Usage ───────────────────────────────────────────────────────────────

function UsageTab() {
  const [usage, setUsage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getAdminUsage()
      .then(setUsage)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load usage'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Spinner />
  if (error) return <ErrorMsg msg={error} />

  const formatNumber = (n) => (n != null ? Number(n).toLocaleString() : '—')
  const formatCost = (n) => (n != null ? `$${Number(n).toFixed(4)}` : '—')

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
      <StatCard
        label="Total Requests"
        value={formatNumber(usage?.total_requests)}
        sub="All time"
      />
      <StatCard
        label="Tokens In"
        value={formatNumber(usage?.total_tokens_in)}
        sub="Prompt tokens"
      />
      <StatCard
        label="Tokens Out"
        value={formatNumber(usage?.total_tokens_out)}
        sub="Completion tokens"
      />
      <StatCard
        label="Total Cost"
        value={formatCost(usage?.total_cost)}
        sub="Estimated USD"
      />
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
  if (!tenant)
    return <p className="text-brand-muted text-sm font-sans py-4">No tenant data available.</p>

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
            [
              'Created',
              tenant.created_at
                ? format(new Date(tenant.created_at), 'MMMM d, yyyy')
                : '—',
            ],
            ['Status', tenant.is_active !== false ? 'Active' : 'Inactive'],
          ].map(([label, value]) => (
            <div key={label} className="flex px-8 py-4 items-center">
              <span className="w-48 text-sm text-brand-muted font-sans font-medium tracking-wide flex-shrink-0">
                {label}
              </span>
              <span className="text-sm text-brand-ink font-sans font-medium">{value ?? '—'}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Tab: Settings (case law + model) ─────────────────────────────────────────

function SettingsTab() {
  const [includePublic, setIncludePublic] = useState(() => {
    const stored = localStorage.getItem('clarity_include_public')
    return stored === null ? true : stored === 'true'
  })
  const [usePremium, setUsePremium] = useState(
    () => localStorage.getItem('clarity_use_premium') === 'true'
  )

  const persist = (key, setter) => (val) => {
    setter(val)
    localStorage.setItem(key, String(val))
  }

  return (
    <div className="max-w-3xl space-y-8">
      {/* Case Law Settings */}
      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
          <h3 className="font-serif font-bold text-xl text-brand-ink">Case Law Settings</h3>
          <p className="text-sm text-brand-ink-2 font-sans mt-1">
            Control which legal databases are included in retrieval for all conversations.
          </p>
        </div>
        <div className="divide-y divide-brand-line">
          <div className="flex items-center justify-between px-8 py-5">
            <div>
              <p className="text-sm font-sans font-semibold text-brand-ink">
                Public case law search
              </p>
              <p className="text-xs text-brand-ink-2 font-sans mt-1">
                Include CourtListener public opinions in RAG retrieval
              </p>
            </div>
            <Toggle
              checked={includePublic}
              onChange={persist('clarity_include_public', setIncludePublic)}
              label="Public case law search"
            />
          </div>
        </div>
      </div>

      {/* Model Settings */}
      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
          <h3 className="font-serif font-bold text-xl text-brand-ink">Model Settings</h3>
          <p className="text-sm text-brand-ink-2 font-sans mt-1">
            Configure the default AI model behaviour for new conversations in your tenant.
          </p>
        </div>
        <div className="divide-y divide-brand-line">
          <div className="flex items-center justify-between px-8 py-5">
            <div>
              <p className="text-sm font-sans font-semibold text-brand-ink">Premium model</p>
              <p className="text-xs text-brand-ink-2 font-sans mt-1">
                Use the premium LLM for new conversations (higher cost)
              </p>
            </div>
            <Toggle
              checked={usePremium}
              onChange={persist('clarity_use_premium', setUsePremium)}
              label="Premium model"
            />
          </div>
        </div>
      </div>

      {/* Customer LLM */}
      <CustomerLLMSection />
    </div>
  )
}

function CustomerLLMSection() {
  const [config, setConfig] = useState({ use_customer_llm: false, customer_llm_provider: '', api_key: '', endpoint: '', deployment: '' })
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)

  useEffect(() => { getAdminTenant().catch(() => {}) }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await configureCustomerLLM(config)
      setMsg({ type: 'success', text: 'Saved.' })
    } catch (err) {
      setMsg({ type: 'error', text: err?.response?.data?.detail || 'Failed.' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 4000)
    }
  }

  const handleReset = async () => {
    setSaving(true)
    try {
      await resetCustomerLLM()
      setConfig({ use_customer_llm: false, customer_llm_provider: '', api_key: '', endpoint: '', deployment: '' })
      setMsg({ type: 'success', text: 'Reset to platform LLM.' })
    } catch {
      setMsg({ type: 'error', text: 'Failed.' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 4000)
    }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
      <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
        <h3 className="font-serif font-bold text-xl text-brand-ink">Customer LLM</h3>
        <p className="text-sm text-brand-ink-2 font-sans mt-1">
          Use your firm's own Gemini or Microsoft Copilot subscription instead of the platform LLM.
        </p>
      </div>
      <div className="px-8 py-5 space-y-4">
        {msg && (
          <div className={`px-4 py-2 rounded-lg text-xs font-medium ${msg.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>{msg.text}</div>
        )}
        <label className="flex items-center gap-3">
          <input type="checkbox" checked={config.use_customer_llm} onChange={(e) => setConfig({ ...config, use_customer_llm: e.target.checked })} className="w-4 h-4 rounded border-brand-line" />
          <span className="text-sm font-sans text-brand-ink">Use firm's own LLM subscription</span>
        </label>
        {config.use_customer_llm && (
          <>
            <select value={config.customer_llm_provider} onChange={(e) => setConfig({ ...config, customer_llm_provider: e.target.value })} className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm">
              <option value="">Select provider...</option>
              <option value="gemini">Google Gemini</option>
              <option value="copilot">Microsoft Copilot (Azure OpenAI)</option>
            </select>
            <input type="password" placeholder="API Key" value={config.api_key} onChange={(e) => setConfig({ ...config, api_key: e.target.value })} className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm" />
            <input type="text" placeholder="Endpoint URL" value={config.endpoint} onChange={(e) => setConfig({ ...config, endpoint: e.target.value })} className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm" />
            <input type="text" placeholder="Deployment name" value={config.deployment} onChange={(e) => setConfig({ ...config, deployment: e.target.value })} className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm" />
          </>
        )}
        <div className="flex gap-3 pt-2">
          <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-brand-ink text-white font-sans text-xs font-semibold rounded-lg hover:opacity-90 disabled:opacity-40">
            {saving ? 'Saving...' : 'Save'}
          </button>
          <button onClick={handleReset} disabled={saving} className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft">
            Reset to Platform LLM
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function AdminPage() {
  const { logout: authLogout } = useAuth()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('users')

  const tabs = [
    { id: 'users', label: 'Users' },
    { id: 'licensing', label: 'Licensing' },
    { id: 'usage', label: 'Usage' },
    { id: 'tenant', label: 'Tenant' },
    { id: 'prompts', label: 'Prompts' },
    { id: 'cloud-search', label: 'Cloud Search' },
    { id: 'permissions', label: 'Permissions' },
    { id: 'settings', label: 'Settings' },
  ]

  const handleLogout = () => {
    authLogout()
    navigate('/login')
  }

  return (
    <div className="min-h-screen bg-brand-bg">
      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <div className="w-8 h-8 bg-brand-bg-soft border border-brand-line rounded-lg flex items-center justify-center">
            <svg width="16" height="16" viewBox="0 0 32 32" fill="none">
              <path
                d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z"
                fill="#14253B"
              />
            </svg>
          </div>
          <span className="font-serif font-bold text-lg text-brand-ink">Clarity Legal</span>
          <span className="text-brand-line-2 mx-1">|</span>
          <span className="text-brand-ink-2 font-sans font-medium tracking-wide text-sm">
            Admin Panel
          </span>
        </div>
        <div className="flex items-center gap-6">
          <button
            onClick={() => navigate('/chat')}
            className="text-sm font-medium text-brand-ink-2 hover:text-brand-accent transition-colors font-sans"
          >
            Back to Chat
          </button>
          <button
            onClick={handleLogout}
            className="text-sm font-medium text-brand-ink-2 hover:text-brand-rose transition-colors font-sans"
          >
            Sign out
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-6xl mx-auto px-8 py-12">
        <div className="mb-10">
          <h1 className="text-4xl font-bold font-serif text-brand-ink tracking-tight mb-3">
            Administration
          </h1>
          <p className="text-brand-ink-2 text-base font-sans">
            Manage users, monitor usage, and configure your tenant.
          </p>
        </div>

        {/* Tabs */}
        <div className="border-b border-brand-line mb-8">
          <nav className="-mb-px flex gap-8">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`pb-4 text-[15px] font-sans font-medium border-b-2 transition-all ${
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

        {/* Tab content */}
        <div className="animate-in fade-in duration-300">
          {activeTab === 'users' && <UsersTab />}
          {activeTab === 'licensing' && <LicensingPanel />}
          {activeTab === 'usage' && <UsageTab />}
          {activeTab === 'tenant' && <TenantTab />}
          {activeTab === 'settings' && <SettingsTab />}
          {activeTab === 'prompts' && <PromptAdminPage />}
          {activeTab === 'cloud-search' && <CloudSearchAdmin />}
          {activeTab === 'permissions' && <PermissionsAudit />}
        </div>
      </div>
    </div>
  )
}
