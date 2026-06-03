import React, { useState, useEffect, useCallback } from 'react'
import { getPlatformTenants, getPlatformUsage, getPlatformHealth, getPlatformTenant, updatePlatformTenant, getPlatformLLMProviders } from '../api'
import { Activity, Database, Server, Shield, Users, Zap, Search, ChevronDown, ChevronRight, BarChart3 } from 'lucide-react'

function StatCard({ label, value, sub, icon: Icon }) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-5 shadow-sm">
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs text-brand-muted font-sans uppercase tracking-wider">{label}</p>
        {Icon && <Icon size={16} className="text-brand-muted" />}
      </div>
      <p className="text-2xl font-bold text-brand-ink font-serif">{value ?? '—'}</p>
      {sub && <p className="text-xs text-brand-muted mt-1 font-sans">{sub}</p>}
    </div>
  )
}

function TierBadge({ tier }) {
  const colors = {
    flat: 'bg-brand-accent/10 text-brand-accent border-brand-accent/20',
    payg: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${colors[tier] || colors.payg}`}>
      {tier === 'flat' ? 'Flat-seat' : 'PAYG'}
    </span>
  )
}

function LoginScreen({ onLogin }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await getPlatformUsage(key)
      onLogin(key)
    } catch {
      setError('Invalid platform key')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-brand-bg">
      <div className="bg-brand-surface rounded-2xl border border-brand-line shadow-xl p-8 w-full max-w-sm">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-xl bg-brand-ink flex items-center justify-center">
            <Shield size={20} className="text-brand-surface" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-brand-ink font-serif">Operator Console</h1>
            <p className="text-xs text-brand-muted font-sans">Platform administration</p>
          </div>
        </div>
        {error && <p className="text-sm text-brand-rose bg-brand-rose/10 px-3 py-2 rounded-lg mb-4 font-sans">{error}</p>}
        <form onSubmit={handleSubmit} className="space-y-4">
          <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Platform secret key" className="w-full border border-brand-line rounded-lg px-4 py-2.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface" required />
          <button type="submit" disabled={loading} className="w-full bg-brand-ink text-white py-2.5 rounded-lg text-sm font-medium font-sans hover:bg-brand-ink-2 disabled:opacity-60 transition-colors">
            {loading ? 'Authenticating…' : 'Access Console'}
          </button>
        </form>
      </div>
    </div>
  )
}

function LLMProviderSelect({ tenant, tenantDetail, platformKey, providers, onUpdate, saving, setSaving }) {
  const currentProvider = tenantDetail?.llm_config?.provider || ''
  const currentModel = tenantDetail?.llm_config?.model || ''
  const [selectedProvider, setSelectedProvider] = useState(currentProvider)
  const [selectedModel, setSelectedModel] = useState(currentModel)
  const [saved, setSaved] = useState(false)

  // Sync when tenantDetail changes (e.g. switching expanded tenant)
  useEffect(() => {
    setSelectedProvider(currentProvider)
    setSelectedModel(currentModel)
    setSaved(false)
  }, [tenantDetail?.llm_config?.provider, tenantDetail?.llm_config?.model])

  const selectedProviderObj = providers.find((p) => p.key === selectedProvider)
  const models = selectedProviderObj?.models || []

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      const payload = {}
      if (selectedProvider) {
        payload.llm_provider = selectedProvider
        payload.llm_model = selectedModel || null
      }
      await updatePlatformTenant(platformKey, tenant.id, payload)
      onUpdate(tenant.id, payload)
      setSaved(true)
    } catch { /* save error silently */ }
    finally { setSaving(false) }
  }

  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="flex-1 min-w-[200px]">
        <label className="block text-xs text-brand-muted font-sans mb-1">Provider</label>
        <select
          value={selectedProvider}
          onChange={(e) => {
            setSelectedProvider(e.target.value)
            setSelectedModel('')
            setSaved(false)
          }}
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
        >
          <option value="">Platform default (DeepSeek)</option>
          {providers.map((p) => (
            <option key={p.key} value={p.key} disabled={!p.configured}>
              {p.label} {p.free_tier ? '(free)' : ''} {!p.configured ? '— not configured' : ''}
            </option>
          ))}
        </select>
      </div>
      {models.length > 0 && selectedProvider && (
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs text-brand-muted font-sans mb-1">Model</label>
          <select
            value={selectedModel}
            onChange={(e) => { setSelectedModel(e.target.value); setSaved(false) }}
            className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
          >
            <option value="">Default</option>
            {models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
      )}
      <button
        onClick={handleSave}
        disabled={saving || (selectedProvider === currentProvider && selectedModel === currentModel)}
        className={`px-4 py-2 rounded-lg text-xs font-medium font-sans border transition-colors ${
          saved
            ? 'bg-brand-accent/10 border-brand-accent/20 text-brand-accent'
            : 'bg-brand-ink text-white border-brand-ink hover:bg-brand-ink-2 disabled:opacity-40'
        }`}
      >
        {saved ? '✓ Saved' : saving ? 'Saving…' : 'Apply'}
      </button>
      {selectedProvider && !selectedProviderObj?.configured && (
        <p className="text-xs text-brand-rose font-sans w-full">This provider is not configured at the platform level. Set the required API key in the environment.</p>
      )}
    </div>
  )
}

export default function PlatformPage() {
  const [platformKey, setPlatformKey] = useState(() => sessionStorage.getItem('platform_key') || null)
  const [tab, setTab] = useState('dashboard')
  const [tenants, setTenants] = useState([])
  const [usage, setUsage] = useState(null)
  const [health, setHealth] = useState(null)
  const [providers, setProviders] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [expandedTenant, setExpandedTenant] = useState(null)
  const [tenantDetail, setTenantDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [savingProvider, setSavingProvider] = useState(false)

  const handleLogin = (key) => {
    sessionStorage.setItem('platform_key', key)
    setPlatformKey(key)
  }

  const loadData = useCallback(async () => {
    if (!platformKey) return
    setLoading(true)
    setError(null)
    try {
      const promises = [getPlatformTenants(platformKey, page), getPlatformUsage(platformKey), getPlatformLLMProviders(platformKey)]
      if (tab === 'health') promises.push(getPlatformHealth(platformKey))
      const results = await Promise.all(promises)
      setTenants(results[0].tenants)
      setTotal(results[0].total)
      setUsage(results[1])
      setProviders(results[2].providers)
      if (results[3]) setHealth(results[3])
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load')
      if (e?.response?.status === 403) { sessionStorage.removeItem('platform_key'); setPlatformKey(null) }
    } finally { setLoading(false) }
  }, [platformKey, page, tab])

  useEffect(() => { loadData() }, [loadData])

  const handleUpdate = (id, changes) => {
    setTenants((prev) => prev.map((t) => (t.id === id ? { ...t, ...changes } : t)))
  }

  const toggleTenant = async (id) => {
    if (expandedTenant === id) { setExpandedTenant(null); setTenantDetail(null); return }
    setExpandedTenant(id)
    setLoadingDetail(true)
    try {
      const data = await getPlatformTenant(platformKey, id)
      setTenantDetail(data)
    } catch { setTenantDetail(null) }
    finally { setLoadingDetail(false) }
  }

  const filtered = tenants.filter((t) =>
    !search || t.name.toLowerCase().includes(search.toLowerCase()) || t.domain.toLowerCase().includes(search.toLowerCase())
  )

  if (!platformKey) return <LoginScreen onLogin={handleLogin} />

  const tabs = [
    { id: 'dashboard', label: 'Dashboard', icon: BarChart3 },
    { id: 'tenants', label: 'Tenants', icon: Users },
    { id: 'health', label: 'System', icon: Database },
  ]

  return (
    <div className="min-h-screen bg-brand-bg">
      {/* Top bar */}
      <div className="bg-brand-surface border-b border-brand-line px-6 py-3 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-brand-ink flex items-center justify-center">
            <Shield size={14} className="text-brand-surface" />
          </div>
          <span className="font-serif font-bold text-brand-ink">Operator Console</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-xs text-brand-muted font-mono">{platformKey.slice(0, 8)}…</span>
          <button onClick={() => { sessionStorage.removeItem('platform_key'); setPlatformKey(null); setTenants([]); setUsage(null); setHealth(null) }} className="text-xs text-brand-muted hover:text-brand-rose font-sans transition-colors">Sign out</button>
        </div>
      </div>

      {/* Tab nav */}
      <div className="bg-brand-surface border-b border-brand-line">
        <div className="max-w-7xl mx-auto px-6 flex gap-1">
          {tabs.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} className={`flex items-center gap-2 px-5 py-3 text-sm font-sans font-medium border-b-2 transition-colors ${tab === t.id ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}>
              <t.icon size={16} />
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {error && (
          <div className="mb-6 bg-brand-rose/10 border border-brand-rose/20 rounded-lg px-4 py-3 text-sm text-brand-rose font-sans">{error}</div>
        )}

        {/* ── Dashboard Tab ── */}
        {tab === 'dashboard' && usage && (
          <div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
              <StatCard label="Tenants" value={usage.total_tenants} sub={`${usage.active_tenants} active`} icon={Users} />
              <StatCard label="Total Users" value={usage.total_users} icon={Users} />
              <StatCard label="Requests (30d)" value={usage.requests_30d?.toLocaleString()} icon={Activity} />
              <StatCard label="Revenue (30d)" value={`$${(usage.cost_usd_30d ?? 0).toFixed(2)}`} sub="billed model cost" icon={Zap} />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Top tenants by usage */}
              <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
                <div className="px-5 py-4 border-b border-brand-line">
                  <h2 className="font-serif font-bold text-brand-ink">Top Tenants (30d)</h2>
                </div>
                <div className="divide-y divide-brand-line">
                  {[...tenants].sort((a, b) => (b.requests_30d || 0) - (a.requests_30d || 0)).slice(0, 10).map((t) => (
                    <div key={t.id} className="px-5 py-3 flex items-center justify-between hover:bg-brand-bg transition-colors">
                      <div>
                        <p className="text-sm font-medium text-brand-ink font-sans">{t.name}</p>
                        <p className="text-xs text-brand-muted">{t.domain}</p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-semibold text-brand-ink-2 font-sans">{t.requests_30d?.toLocaleString()} req</p>
                        <p className="text-xs text-brand-muted">${t.cost_usd_30d?.toFixed(2)}</p>
                      </div>
                    </div>
                  ))}
                  {tenants.length === 0 && <p className="px-5 py-8 text-sm text-brand-muted text-center font-sans">No tenants yet</p>}
                </div>
              </div>

              {/* Recent tenants */}
              <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
                <div className="px-5 py-4 border-b border-brand-line">
                  <h2 className="font-serif font-bold text-brand-ink">Recent Tenants</h2>
                </div>
                <div className="divide-y divide-brand-line">
                  {[...tenants].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).slice(0, 10).map((t) => (
                    <div key={t.id} className="px-5 py-3 flex items-center justify-between hover:bg-brand-bg transition-colors">
                      <div>
                        <p className="text-sm font-medium text-brand-ink font-sans">{t.name}</p>
                        <div className="flex items-center gap-2">
                          <TierBadge tier={t.billing_tier} />
                          <span className={`w-2 h-2 rounded-full ${t.is_active ? 'bg-brand-accent' : 'bg-brand-rose'}`} />
                          <span className="text-xs text-brand-muted">{t.is_active ? 'Active' : 'Inactive'}</span>
                        </div>
                      </div>
                      <p className="text-xs text-brand-muted">{new Date(t.created_at).toLocaleDateString()}</p>
                    </div>
                  ))}
                  {tenants.length === 0 && <p className="px-5 py-8 text-sm text-brand-muted text-center font-sans">No tenants yet</p>}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Tenants Tab ── */}
        {tab === 'tenants' && (
          <div>
            {/* Search + actions */}
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-sm">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted" />
                <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by name or domain…" className="w-full pl-9 pr-4 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface" />
              </div>
              <button onClick={loadData} disabled={loading} className="text-xs text-brand-muted hover:text-brand-ink font-sans transition-colors">
                {loading ? 'Loading…' : 'Refresh'}
              </button>
            </div>

            {/* Tenant list */}
            {loading && tenants.length === 0 ? (
              <div className="flex justify-center py-16">
                <div className="w-8 h-8 border-2 border-brand-accent border-t-transparent rounded-full animate-spin" />
              </div>
            ) : (
              <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead className="bg-brand-bg-soft border-b border-brand-line">
                      <tr className="text-xs text-brand-muted uppercase tracking-wider font-sans">
                        <th className="text-left px-5 py-3"></th>
                        <th className="text-left px-5 py-3">Tenant</th>
                        <th className="text-left px-5 py-3">Tier</th>
                        <th className="text-center px-5 py-3">Users</th>
                        <th className="text-center px-5 py-3">Requests (30d)</th>
                        <th className="text-right px-5 py-3">Cost (30d)</th>
                        <th className="text-center px-5 py-3">Status</th>
                        <th className="text-center px-5 py-3">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-brand-line">
                      {filtered.map((t) => (
                        <React.Fragment key={t.id}>
                          <tr className="hover:bg-brand-bg transition-colors cursor-pointer" onClick={() => toggleTenant(t.id)}>
                            <td className="px-5 py-3">{expandedTenant === t.id ? <ChevronDown size={14} className="text-brand-ink" /> : <ChevronRight size={14} className="text-brand-muted" />}</td>
                            <td className="px-5 py-3">
                              <p className="text-sm font-medium text-brand-ink font-sans">{t.name}</p>
                              <p className="text-xs text-brand-muted">{t.domain}</p>
                            </td>
                            <td className="px-5 py-3"><TierBadge tier={t.billing_tier} /></td>
                            <td className="px-5 py-3 text-center text-sm text-brand-ink-2 font-sans">{t.user_count}</td>
                            <td className="px-5 py-3 text-center text-sm text-brand-ink-2 font-sans">{t.requests_30d?.toLocaleString()}</td>
                            <td className="px-5 py-3 text-right text-sm text-brand-ink-2 font-mono">${t.cost_usd_30d?.toFixed(2)}</td>
                            <td className="px-5 py-3 text-center">
                              <span className={`inline-flex items-center gap-1.5 text-xs font-medium font-sans ${t.is_active ? 'text-brand-accent' : 'text-brand-rose'}`}>
                                <span className={`w-2 h-2 rounded-full ${t.is_active ? 'bg-brand-accent' : 'bg-brand-rose'}`} />
                                {t.is_active ? 'Active' : 'Inactive'}
                              </span>
                            </td>
                            <td className="px-5 py-3 text-center">
                              <button onClick={(e) => { e.stopPropagation(); toggleTenant(t.id) }} className="text-xs text-brand-accent hover:underline font-sans">
                                {expandedTenant === t.id ? 'Close' : 'Details'}
                              </button>
                            </td>
                          </tr>
                          {/* Expanded detail row */}
                          {expandedTenant === t.id && (
                            <tr key={`detail-${t.id}`}>
                              <td colSpan={8} className="px-5 py-4 bg-brand-bg-soft">
                                {loadingDetail ? (
                                  <div className="flex justify-center py-4"><div className="w-6 h-6 border-2 border-brand-accent border-t-transparent rounded-full animate-spin" /></div>
                                ) : tenantDetail ? (
                                  <>
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                                    <div>
                                      <h4 className="text-xs font-bold text-brand-ink uppercase tracking-wider mb-3 font-sans">Tenant Info</h4>
                                      <dl className="space-y-2 text-sm">
                                        <div className="flex justify-between"><dt className="text-brand-muted font-sans">Company</dt><dd className="text-brand-ink font-sans">{tenantDetail.company_name || '—'}</dd></div>
                                        <div className="flex justify-between"><dt className="text-brand-muted font-sans">Domain</dt><dd className="text-brand-ink font-sans">{tenantDetail.domain}</dd></div>
                                        <div className="flex justify-between"><dt className="text-brand-muted font-sans">Stripe ID</dt><dd className="text-brand-ink font-mono text-xs">{tenantDetail.stripe_customer_id ? '✓' : '—'}</dd></div>
                                        <div className="flex justify-between"><dt className="text-brand-muted font-sans">Seats</dt><dd className="text-brand-ink font-sans">{tenantDetail.flat_seat_count || '—'}</dd></div>
                                        <div className="flex justify-between"><dt className="text-brand-muted font-sans">Created</dt><dd className="text-brand-ink font-sans">{new Date(tenantDetail.created_at).toLocaleDateString()}</dd></div>
                                      </dl>
                                    </div>
                                    <div>
                                      <h4 className="text-xs font-bold text-brand-ink uppercase tracking-wider mb-3 font-sans">Users</h4>
                                      {tenantDetail.users?.length > 0 ? (
                                        <div className="space-y-1.5 max-h-48 overflow-y-auto">
                                          {tenantDetail.users.map((u) => (
                                            <div key={u.id} className="flex items-center justify-between text-sm py-1">
                                              <div>
                                                <p className="text-brand-ink font-sans font-medium">{u.full_name || u.email}</p>
                                                <p className="text-xs text-brand-muted">{u.email}</p>
                                              </div>
                                              <span className={`text-xs px-1.5 py-0.5 rounded font-sans ${u.role === 'admin' ? 'bg-brand-ink/10 text-brand-ink' : 'bg-brand-muted/10 text-brand-muted'}`}>{u.role}</span>
                                            </div>
                                          ))}
                                        </div>
                                      ) : <p className="text-sm text-brand-muted font-sans">No users listed</p>}
                                    </div>
                                    <div>
                                      <h4 className="text-xs font-bold text-brand-ink uppercase tracking-wider mb-3 font-sans">Actions</h4>
                                      <div className="space-y-3">
                                        <button onClick={() => { const payload = { is_active: !t.is_active }; updatePlatformTenant(platformKey, t.id, payload).then(() => handleUpdate(t.id, payload)) }} className={`w-full px-3 py-2 rounded-lg text-xs font-medium font-sans border transition-colors ${t.is_active ? 'border-brand-rose/30 text-brand-rose hover:bg-brand-rose/5' : 'border-brand-accent/30 text-brand-accent hover:bg-brand-accent/5'}`}>
                                          {t.is_active ? 'Deactivate Tenant' : 'Activate Tenant'}
                                        </button>
                                        <div className="flex gap-2">
                                          <button onClick={() => { const payload = { billing_tier: 'flat' }; updatePlatformTenant(platformKey, t.id, payload).then(() => handleUpdate(t.id, payload)) }} className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium font-sans border transition-colors ${t.billing_tier === 'flat' ? 'bg-brand-accent/10 border-brand-accent/20 text-brand-accent' : 'border-brand-line text-brand-muted hover:border-brand-ink'}`}>
                                            Flat-seat
                                          </button>
                                          <button onClick={() => { const payload = { billing_tier: 'payg' }; updatePlatformTenant(platformKey, t.id, payload).then(() => handleUpdate(t.id, payload)) }} className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium font-sans border transition-colors ${t.billing_tier === 'payg' ? 'bg-brand-amber/10 border-brand-amber/20 text-brand-amber' : 'border-brand-line text-brand-muted hover:border-brand-ink'}`}>
                                            PAYG
                                          </button>
                                        </div>
                                      </div>
                                    </div>
                                  </div>
                                  {/* LLM Provider override — full-width row */}
                                  <div className="mt-4 pt-4 border-t border-brand-line">
                                    <h4 className="text-xs font-bold text-brand-ink uppercase tracking-wider mb-3 font-sans">LLM Provider</h4>
                                    <LLMProviderSelect tenant={t} tenantDetail={tenantDetail} platformKey={platformKey} providers={providers} onUpdate={handleUpdate} saving={savingProvider} setSaving={setSavingProvider} />
                                  </div>
                                  </>
                                ) : (
                                  <p className="text-sm text-brand-rose font-sans">Failed to load tenant detail</p>
                                )}
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
                {/* Pagination */}
                {total > LIMIT && (
                  <div className="flex items-center justify-between px-5 py-3 border-t border-brand-line">
                    <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">← Prev</button>
                    <span className="text-xs text-brand-muted font-sans">Page {page} of {Math.ceil(total / LIMIT)} ({total} total)</span>
                    <button onClick={() => setPage((p) => p + 1)} disabled={page * LIMIT >= total} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">Next →</button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Health Tab ── */}
        {tab === 'health' && (
          <div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
              <StatCard label="Tenants" value={usage?.total_tenants} sub={`${usage?.active_tenants} active`} icon={Users} />
              <StatCard label="Users" value={usage?.total_users} icon={Users} />
              <StatCard label="Requests (30d)" value={usage?.requests_30d?.toLocaleString()} icon={Activity} />
              <StatCard label="Platform Key" value="Configured" sub={platformKey?.slice(0, 8) + '…'} icon={Shield} />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* DB tables */}
              <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
                <div className="px-5 py-4 border-b border-brand-line">
                  <h2 className="font-serif font-bold text-brand-ink flex items-center gap-2"><Database size={18} /> Database Tables</h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead className="bg-brand-bg-soft border-b border-brand-line">
                      <tr className="text-xs text-brand-muted uppercase tracking-wider font-sans">
                        <th className="text-left px-5 py-2">Table</th>
                        <th className="text-right px-5 py-2">Rows</th>
                        <th className="text-right px-5 py-2">Size</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-brand-line">
                      {health?.tables?.map((t) => (
                        <tr key={t.table} className="hover:bg-brand-bg transition-colors">
                          <td className="px-5 py-2.5 text-sm text-brand-ink font-mono">{t.table}</td>
                          <td className="px-5 py-2.5 text-sm text-brand-ink-2 text-right font-sans">{t.rows?.toLocaleString()}</td>
                          <td className="px-5 py-2.5 text-sm text-brand-muted text-right font-sans">{t.size}</td>
                        </tr>
                      )) || <tr><td colSpan={3} className="px-5 py-8 text-sm text-brand-muted text-center font-sans">No data</td></tr>}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Service status */}
              <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm">
                <div className="px-5 py-4 border-b border-brand-line">
                  <h2 className="font-serif font-bold text-brand-ink flex items-center gap-2"><Server size={18} /> Service Status</h2>
                </div>
                <div className="p-5 space-y-4">
                  {(health?.services || providers.length > 0 ? (
                    health?.services || [
                      { name: 'PostgreSQL', online: health?.tables?.length > 0 },
                      { name: 'Redis', online: true },
                      { name: 'API Server', online: true },
                    ].concat(
                      providers.map((p) => ({ name: p.label, online: p.configured }))
                    )
                  ) : [
                    { name: 'PostgreSQL', online: health?.tables?.length > 0 },
                    { name: 'Redis', online: true },
                    { name: 'API Server', online: true },
                  ]).map((s) => (
                    <div key={s.name} className="flex items-center justify-between">
                      <span className="text-sm font-sans text-brand-ink">{s.name}</span>
                      <span className={`inline-flex items-center gap-1.5 text-xs font-medium font-sans ${s.online ? 'text-brand-accent' : 'text-brand-rose'}`}>
                        <span className={`w-2 h-2 rounded-full ${s.online ? 'bg-brand-accent' : 'bg-brand-rose'}`} />
                        {s.online ? 'Online' : 'Offline'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Last check time */}
            {health?.checked_at && (
              <p className="mt-4 text-xs text-brand-muted font-sans text-right">Last check: {new Date(health.checked_at).toLocaleString()}</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
