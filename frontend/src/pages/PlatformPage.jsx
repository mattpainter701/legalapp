import React, { useState, useEffect, useCallback, useRef } from 'react'
import { getPlatformTenants, getPlatformUsage, getPlatformHealth, getPlatformTenant, updatePlatformTenant, getPlatformPlans, getPlatformLLMConfig, getPlatformLogs, getPlatformLogsSummary, getPlatformTenantLogs, getPlatformTenantLogsSummary, getPlatformAccessLogs, getPlatformAccessLogsSummary, getLLMProviderPresets, getLLMProviderKeys, addLLMProviderKey, deleteLLMProviderKey, syncEnvKeys, fetchProviderModels, getLLMModelCatalog, refreshLLMModelCatalog, getLLMRoutes, saveLLMRoutes, getLLMGatewayStatus, reloadLLMRoutes, testLLMRoute } from '../api'
import { Activity, AlertTriangle, Database, Server, Shield, Users, Zap, Search, ChevronDown, ChevronRight, BarChart3, FileText, Globe, Key, Plus, Trash2, RefreshCw, CheckCircle, XCircle, Cpu, ArrowDown, ArrowUp, Save } from 'lucide-react'

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

function AliasPill({ label, alias, sub }) {
  return (
    <div className="border border-brand-line rounded-lg px-4 py-3 bg-brand-bg-soft">
      <p className="text-xs font-bold text-brand-ink uppercase tracking-wider font-sans">{label}</p>
      <p className="text-sm text-brand-ink font-mono mt-1">{alias || 'platform default'}</p>
      {sub && <p className="text-[11px] text-brand-muted font-sans mt-1">{sub}</p>}
    </div>
  )
}

function RoutingOverviewPanel({ config, onOpenRouting }) {
  const standardAlias = config?.standard_model || 'clarity-standard'
  const premiumAlias = config?.premium_model || 'clarity-premium'
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden mb-8">
      <div className="px-5 py-4 border-b border-brand-line flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="font-serif font-bold text-brand-ink">AI Gateway Routing</h2>
          <p className="text-xs text-brand-muted font-sans mt-1">LegalApp sends standard and premium work to LiteLLM aliases; the AI Routing tab controls the upstream provider, model, key, and fallback chain.</p>
        </div>
        <button
          onClick={onOpenRouting}
          className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-brand-ink text-white text-xs font-medium font-sans rounded-lg hover:bg-brand-ink-2 transition-colors"
        >
          <Cpu size={14} />
          AI Routing
        </button>
      </div>
      <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-4">
        <AliasPill label="Standard route" alias={standardAlias} sub="Resolved for normal chat, skills, summaries, and drafting." />
        <AliasPill label="Premium route" alias={premiumAlias} sub="Resolved when premium routing is requested." />
      </div>
    </div>
  )
}

function TenantAliasOverride({ tenant, tenantDetail, platformKey, defaultAliases, onUpdate, saving, setSaving }) {
  const config = tenantDetail?.llm_config || {}
  const current = {
    standardModel: config.standard_model || config.model || '',
    premiumModel: config.premium_model || '',
  }
  const [values, setValues] = useState(current)
  const [saved, setSaved] = useState(false)
  const isDirty = useRef(false)

  useEffect(() => {
    if (isDirty.current) return
    setValues(current)
    setSaved(false)
  }, [config.standard_model, config.premium_model, config.model])

  const changed = JSON.stringify(values) !== JSON.stringify(current)

  const setValue = (key, value) => {
    isDirty.current = true
    setValues((prev) => ({ ...prev, [key]: value }))
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      const payload = {
        standard_llm_provider: values.standardModel ? 'litellm' : null,
        standard_llm_model: values.standardModel || null,
        premium_llm_provider: values.premiumModel ? 'litellm' : null,
        premium_llm_model: values.premiumModel || null,
      }
      await updatePlatformTenant(platformKey, tenant.id, payload)
      onUpdate(tenant.id, { llm_config: payload })
      isDirty.current = false
      setSaved(true)
    } catch { /* save error silently */ }
    finally { setSaving(false) }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {[
          ['standardModel', 'Standard alias', defaultAliases.standard],
          ['premiumModel', 'Premium alias', defaultAliases.premium],
        ].map(([key, label, fallback]) => (
          <div key={key}>
            <label className="block text-xs text-brand-muted font-sans mb-1">{label}</label>
            <input
              list={`tenant-aliases-${tenant.id}`}
              value={values[key] || ''}
              onChange={(e) => setValue(key, e.target.value)}
              placeholder={`Inherit ${fallback}`}
              className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
            />
          </div>
        ))}
        <datalist id={`tenant-aliases-${tenant.id}`}>
          {[defaultAliases.standard, defaultAliases.premium, 'clarity-standard', 'clarity-premium'].filter(Boolean).map((alias) => (
            <option key={alias} value={alias} />
          ))}
        </datalist>
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving || !changed}
          className={`px-4 py-2 rounded-lg text-xs font-medium font-sans border transition-colors ${
            saved
              ? 'bg-brand-accent/10 border-brand-accent/20 text-brand-accent'
              : 'bg-brand-ink text-white border-brand-ink hover:bg-brand-ink-2 disabled:opacity-40'
          }`}
        >
          {saved ? 'Saved' : saving ? 'Saving...' : 'Apply Routes'}
        </button>
        <p className="text-xs text-brand-muted font-sans">Blank fields inherit the platform aliases.</p>
      </div>
    </div>
  )
}

function TenantPlanOverride({ tenant, tenantDetail, platformKey, onUpdate }) {
  const currentPlan = tenantDetail?.module_config?.plan || ''
  const [plans, setPlans] = useState([])
  const [value, setValue] = useState(currentPlan)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => { setValue(currentPlan); setSaved(false) }, [currentPlan])

  useEffect(() => {
    let cancelled = false
    getPlatformPlans(platformKey)
      .then((data) => { if (!cancelled) setPlans(data.plans || []) })
      .catch(() => { if (!cancelled) setPlans([]) })
    return () => { cancelled = true }
  }, [platformKey])

  const save = async () => {
    if (!value) return
    setSaving(true)
    setSaved(false)
    try {
      await updatePlatformTenant(platformKey, tenant.id, { plan: value })
      onUpdate(tenant.id, {})
      setSaved(true)
    } catch {
      /* save error silently */
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-end gap-3">
      <div className="flex-1">
        <label className="block text-xs text-brand-muted font-sans mb-1">Plan</label>
        <select
          value={value}
          onChange={(e) => { setValue(e.target.value); setSaved(false) }}
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans bg-brand-surface focus:outline-none focus:ring-2 focus:ring-brand-accent"
        >
          <option value="">(default / full platform)</option>
          {plans.map((p) => (
            <option key={p.id} value={p.id}>{p.label} ({p.id})</option>
          ))}
        </select>
      </div>
      <button
        onClick={save}
        disabled={saving || !value || value === currentPlan}
        className={`px-4 py-2 rounded-lg text-xs font-medium font-sans border transition-colors ${
          saved
            ? 'bg-brand-accent/10 border-brand-accent/20 text-brand-accent'
            : 'bg-brand-ink text-white border-brand-ink hover:bg-brand-ink-2 disabled:opacity-40'
        }`}
      >
        {saved ? 'Saved' : saving ? 'Saving...' : 'Set Plan'}
      </button>
    </div>
  )
}

function LogsTab({ platformKey, tenants }) {
  const [subtab, setSubtab] = useState('system')
  const [systemErrors, setSystemErrors] = useState(null)
  const [systemSummary, setSystemSummary] = useState(null)
  const [logPage, setLogPage] = useState(1)
  const [logLimit, setLogLimit] = useState(50)
  const [logTotal, setLogTotal] = useState(0)
  const [logDays, setLogDays] = useState(7)
  const [logSeverity, setLogSeverity] = useState('')
  const [logType, setLogType] = useState('')
  const [logTenant, setLogTenant] = useState('')
  const [logUnresolved, setLogUnresolved] = useState(false)
  const [logLoading, setLogLoading] = useState(false)

  // Tenant-specific logs
  const [selTenant, setSelTenant] = useState('')
  const [tenantErrors, setTenantErrors] = useState(null)
  const [tenantSummary, setTenantSummary] = useState(null)
  const [tlogPage, setTlogPage] = useState(1)
  const [tlogTotal, setTlogTotal] = useState(0)
  const [tlogLoading, setTlogLoading] = useState(false)

  // API traffic
  const [accessLogs, setAccessLogs] = useState(null)
  const [accessSummary, setAccessSummary] = useState(null)
  const [alogPage, setAlogPage] = useState(1)
  const [alogTotal, setAlogTotal] = useState(0)
  const [alogHours, setAlogHours] = useState(24)
  const [alogLoading, setAlogLoading] = useState(false)
  const [alogTenant, setAlogTenant] = useState('')
  const [alogEndpoint, setAlogEndpoint] = useState('')

  const loadSystemErrors = useCallback(async (pg) => {
    setLogLoading(true)
    try {
      const p = pg || logPage
      const params = { page: p, limit: logLimit, days: logDays }
      if (logSeverity) params.severity = logSeverity
      if (logType) params.error_type = logType
      if (logTenant) params.tenant_id = logTenant
      if (logUnresolved) params.unresolved_only = true
      const [errors, summary] = await Promise.all([
        getPlatformLogs(platformKey, params),
        getPlatformLogsSummary(platformKey, { days: logDays }),
      ])
      setSystemErrors(errors.errors)
      setLogTotal(errors.total)
      setLogLimit(errors.limit)
      setLogPage(errors.page)
      setSystemSummary(summary)
    } catch { /* silent */ }
    finally { setLogLoading(false) }
  }, [platformKey, logPage, logLimit, logDays, logSeverity, logType, logTenant, logUnresolved])

  useEffect(() => { loadSystemErrors(1) }, [loadSystemErrors])

  const loadTenantLogs = useCallback(async (pg) => {
    if (!selTenant) return
    setTlogLoading(true)
    try {
      const p = pg || tlogPage
      const params = { page: p, limit: 50, days: logDays }
      if (logSeverity) params.severity = logSeverity
      const [errors, summary] = await Promise.all([
        getPlatformTenantLogs(platformKey, selTenant, params),
        getPlatformTenantLogsSummary(platformKey, selTenant, { days: logDays }),
      ])
      setTenantErrors(errors.errors)
      setTlogTotal(errors.total)
      setTlogPage(errors.page)
      setTenantSummary(summary)
    } catch { /* silent */ }
    finally { setTlogLoading(false) }
  }, [platformKey, selTenant, tlogPage, logDays, logSeverity])

  useEffect(() => { if (selTenant) loadTenantLogs(1) }, [loadTenantLogs])

  const loadAccessLogs = useCallback(async (pg) => {
    setAlogLoading(true)
    try {
      const p = pg || alogPage
      const params = { page: p, limit: 50, hours: alogHours }
      if (alogTenant) params.tenant_id = alogTenant
      if (alogEndpoint) params.endpoint = alogEndpoint
      const [logs, summary] = await Promise.all([
        getPlatformAccessLogs(platformKey, params),
        getPlatformAccessLogsSummary(platformKey, { hours: alogHours }),
      ])
      setAccessLogs(logs.entries)
      setAlogTotal(logs.total)
      setAlogPage(logs.page)
      setAccessSummary(summary)
    } catch { /* silent */ }
    finally { setAlogLoading(false) }
  }, [platformKey, alogPage, alogHours, alogTenant, alogEndpoint])

  useEffect(() => { loadAccessLogs(1) }, [loadAccessLogs])

  const severityColor = (s) => {
    if (s === 'critical') return 'text-brand-rose bg-brand-rose/10 border-brand-rose/20'
    if (s === 'error') return 'text-red-400 bg-red-400/10 border-red-400/20'
    if (s === 'warning') return 'text-brand-amber bg-brand-amber/10 border-brand-amber/20'
    return 'text-brand-muted bg-brand-muted/10 border-brand-muted/20'
  }

  const statusColor = (code) => {
    if (code >= 500) return 'text-brand-rose'
    if (code >= 400) return 'text-brand-amber'
    if (code >= 200) return 'text-brand-accent'
    return 'text-brand-muted'
  }

  return (
    <div>
      {/* Sub-tabs */}
      <div className="flex gap-1 mb-6 border-b border-brand-line pb-0">
        {[
          { id: 'system', label: 'System Errors', icon: AlertTriangle },
          { id: 'tenant', label: 'Tenant Logs', icon: FileText },
          { id: 'traffic', label: 'API Traffic', icon: Globe },
        ].map((st) => (
          <button key={st.id} onClick={() => setSubtab(st.id)} className={`flex items-center gap-1.5 px-4 py-2 text-xs font-sans font-medium border-b-2 transition-colors -mb-px ${subtab === st.id ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}>
            <st.icon size={14} />
            {st.label}
          </button>
        ))}
      </div>

      {/* ── Filters (shared) ── */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <select value={logDays} onChange={(e) => setLogDays(Number(e.target.value))} className="border border-brand-line rounded-lg px-3 py-1.5 text-xs font-sans bg-brand-surface">
          <option value={1}>24h</option>
          <option value={3}>3d</option>
          <option value={7}>7d</option>
          <option value={30}>30d</option>
        </select>
        {subtab !== 'traffic' && (
          <>
            <select value={logSeverity} onChange={(e) => setLogSeverity(e.target.value)} className="border border-brand-line rounded-lg px-3 py-1.5 text-xs font-sans bg-brand-surface">
              <option value="">All severities</option>
              <option value="critical">Critical</option>
              <option value="error">Error</option>
              <option value="warning">Warning</option>
              <option value="info">Info</option>
            </select>
            {(subtab === 'system') && (
              <>
                <select value={logTenant} onChange={(e) => setLogTenant(e.target.value)} className="border border-brand-line rounded-lg px-3 py-1.5 text-xs font-sans bg-brand-surface max-w-[200px]">
                  <option value="">All tenants</option>
                  {tenants.map((t) => (<option key={t.id} value={t.id}>{t.name}</option>))}
                </select>
                <label className="flex items-center gap-1.5 text-xs text-brand-muted font-sans cursor-pointer">
                  <input type="checkbox" checked={logUnresolved} onChange={(e) => setLogUnresolved(e.target.checked)} className="rounded" />
                  Unresolved only
                </label>
              </>
            )}
          </>
        )}
        {subtab === 'traffic' && (
          <>
            <select value={alogTenant} onChange={(e) => setAlogTenant(e.target.value)} className="border border-brand-line rounded-lg px-3 py-1.5 text-xs font-sans bg-brand-surface max-w-[200px]">
              <option value="">All tenants</option>
              {tenants.map((t) => (<option key={t.id} value={t.id}>{t.name}</option>))}
            </select>
            <input type="text" value={alogEndpoint} onChange={(e) => setAlogEndpoint(e.target.value)} placeholder="Filter endpoint…" className="border border-brand-line rounded-lg px-3 py-1.5 text-xs font-sans bg-brand-surface w-48" />
            <select value={alogHours} onChange={(e) => setAlogHours(Number(e.target.value))} className="border border-brand-line rounded-lg px-3 py-1.5 text-xs font-sans bg-brand-surface">
              <option value={1}>1h</option>
              <option value={6}>6h</option>
              <option value={24}>24h</option>
              <option value={72}>3d</option>
              <option value={168}>7d</option>
            </select>
          </>
        )}
      </div>

      {/* ── System Errors ── */}
      {subtab === 'system' && (
        <div>
          {systemSummary && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <StatCard label="Total Errors" value={systemSummary.total_errors} icon={AlertTriangle} />
              <StatCard label="Unresolved" value={systemSummary.unresolved} sub={systemSummary.total_errors > 0 ? `${((systemSummary.unresolved / systemSummary.total_errors) * 100).toFixed(0)}%` : null} icon={AlertTriangle} />
              <StatCard label="Critical" value={systemSummary.by_severity?.critical || 0} icon={AlertTriangle} />
              <StatCard label="Error" value={systemSummary.by_severity?.error || 0} icon={AlertTriangle} />
            </div>
          )}

          <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-brand-bg-soft border-b border-brand-line">
                  <tr className="text-xs text-brand-muted uppercase tracking-wider font-sans">
                    <th className="text-left px-4 py-2">Tenant</th>
                    <th className="text-left px-4 py-2">Type</th>
                    <th className="text-left px-4 py-2">Severity</th>
                    <th className="text-left px-4 py-2">Message</th>
                    <th className="text-left px-4 py-2">Endpoint</th>
                    <th className="text-right px-4 py-2">Status</th>
                    <th className="text-right px-4 py-2">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {systemErrors?.map((e) => (
                    <tr key={e.id} className="hover:bg-brand-bg transition-colors">
                      <td className="px-4 py-2.5 text-xs text-brand-ink font-sans max-w-[120px] truncate" title={e.tenant_name}>{e.tenant_name}</td>
                      <td className="px-4 py-2.5 text-xs text-brand-muted font-mono">{e.error_type}</td>
                      <td className="px-4 py-2.5"><span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium border ${severityColor(e.severity)}`}>{e.severity}</span></td>
                      <td className="px-4 py-2.5 text-xs text-brand-ink-2 font-sans max-w-[300px] truncate" title={e.message}>{e.message}</td>
                      <td className="px-4 py-2.5 text-xs text-brand-muted font-mono max-w-[150px] truncate">{e.endpoint || '—'}</td>
                      <td className="px-4 py-2.5 text-xs text-brand-muted text-right font-sans">{e.status_code || '—'}</td>
                      <td className="px-4 py-2.5 text-xs text-brand-muted text-right font-sans whitespace-nowrap">{new Date(e.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                  {(!systemErrors || systemErrors.length === 0) && (
                    <tr><td colSpan={7} className="px-5 py-8 text-sm text-brand-muted text-center font-sans">{logLoading ? 'Loading…' : 'No errors found'}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            {logTotal > logLimit && (
              <div className="flex items-center justify-between px-5 py-3 border-t border-brand-line">
                <button onClick={() => setLogPage((p) => Math.max(1, p - 1))} disabled={logPage === 1} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">← Prev</button>
                <span className="text-xs text-brand-muted font-sans">Page {logPage} of {Math.ceil(logTotal / logLimit)}</span>
                <button onClick={() => setLogPage((p) => p + 1)} disabled={logPage * logLimit >= logTotal} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">Next →</button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Tenant Logs ── */}
      {subtab === 'tenant' && (
        <div>
          <div className="flex items-center gap-3 mb-6">
            <select value={selTenant} onChange={(e) => { setSelTenant(e.target.value); setTlogPage(1) }} className="border border-brand-line rounded-lg px-3 py-1.5 text-sm font-sans bg-brand-surface">
              <option value="">Select a tenant…</option>
              {tenants.map((t) => (<option key={t.id} value={t.id}>{t.name} ({t.domain})</option>))}
            </select>
          </div>

          {selTenant && tenantSummary && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <StatCard label="Total Errors" value={tenantSummary.total_errors} icon={AlertTriangle} />
              <StatCard label="Unresolved" value={tenantSummary.unresolved} icon={AlertTriangle} />
              <StatCard label="Critical" value={tenantSummary.by_severity?.critical || 0} icon={AlertTriangle} />
              <StatCard label="Error" value={tenantSummary.by_severity?.error || 0} icon={AlertTriangle} />
            </div>
          )}

          {selTenant && (
            <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-brand-bg-soft border-b border-brand-line">
                    <tr className="text-xs text-brand-muted uppercase tracking-wider font-sans">
                      <th className="text-left px-4 py-2">Type</th>
                      <th className="text-left px-4 py-2">Severity</th>
                      <th className="text-left px-4 py-2">Message</th>
                      <th className="text-left px-4 py-2">User</th>
                      <th className="text-left px-4 py-2">Endpoint</th>
                      <th className="text-right px-4 py-2">Status</th>
                      <th className="text-right px-4 py-2">When</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-brand-line">
                    {tenantErrors?.map((e) => (
                      <tr key={e.id} className="hover:bg-brand-bg transition-colors">
                        <td className="px-4 py-2.5 text-xs text-brand-muted font-mono">{e.error_type}</td>
                        <td className="px-4 py-2.5"><span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium border ${severityColor(e.severity)}`}>{e.severity}</span></td>
                        <td className="px-4 py-2.5 text-xs text-brand-ink-2 font-sans max-w-[300px] truncate" title={e.message}>{e.message}</td>
                        <td className="px-4 py-2.5 text-xs text-brand-muted font-mono">{e.user_id || 'system'}</td>
                        <td className="px-4 py-2.5 text-xs text-brand-muted font-mono max-w-[150px] truncate">{e.endpoint || '—'}</td>
                        <td className="px-4 py-2.5 text-xs text-brand-muted text-right font-sans">{e.status_code || '—'}</td>
                        <td className="px-4 py-2.5 text-xs text-brand-muted text-right font-sans whitespace-nowrap">{new Date(e.created_at).toLocaleString()}</td>
                      </tr>
                    ))}
                    {(!tenantErrors || tenantErrors.length === 0) && (
                      <tr><td colSpan={7} className="px-5 py-8 text-sm text-brand-muted text-center font-sans">{tlogLoading ? 'Loading…' : 'No errors found'}</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {tlogTotal > 50 && (
                <div className="flex items-center justify-between px-5 py-3 border-t border-brand-line">
                  <button onClick={() => setTlogPage((p) => Math.max(1, p - 1))} disabled={tlogPage === 1} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">← Prev</button>
                  <span className="text-xs text-brand-muted font-sans">Page {tlogPage} of {Math.ceil(tlogTotal / 50)}</span>
                  <button onClick={() => setTlogPage((p) => p + 1)} disabled={tlogPage * 50 >= tlogTotal} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">Next →</button>
                </div>
              )}
            </div>
          )}

          {!selTenant && (
            <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm p-8 text-center">
              <FileText size={32} className="text-brand-muted mx-auto mb-3" />
              <p className="text-sm text-brand-muted font-sans">Select a tenant above to view their error logs and diagnostics</p>
            </div>
          )}
        </div>
      )}

      {/* ── API Traffic ── */}
      {subtab === 'traffic' && (
        <div>
          {accessSummary && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <StatCard label="Requests" value={accessSummary.total_requests?.toLocaleString()} sub={`${alogHours}h window`} icon={Globe} />
              <StatCard label="2xx" value={accessSummary.by_status?.['200'] || 0} icon={Activity} />
              <StatCard label="4xx" value={accessSummary.by_status?.['400'] + accessSummary.by_status?.['401'] + accessSummary.by_status?.['403'] + accessSummary.by_status?.['404'] + accessSummary.by_status?.['422'] + accessSummary.by_status?.['429'] || 0} icon={AlertTriangle} />
              <StatCard label="Avg Latency" value={accessSummary.avg_latency_ms ? `${accessSummary.avg_latency_ms}ms` : '—'} icon={Zap} />
            </div>
          )}

          <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-brand-bg-soft border-b border-brand-line">
                  <tr className="text-xs text-brand-muted uppercase tracking-wider font-sans">
                    <th className="text-left px-4 py-2">Tenant</th>
                    <th className="text-left px-4 py-2">Method</th>
                    <th className="text-left px-4 py-2">Endpoint</th>
                    <th className="text-right px-4 py-2">Status</th>
                    <th className="text-right px-4 py-2">Latency</th>
                    <th className="text-right px-4 py-2">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {accessLogs?.map((e) => (
                    <tr key={e.id} className="hover:bg-brand-bg transition-colors">
                      <td className="px-4 py-2.5 text-xs text-brand-ink font-sans max-w-[120px] truncate" title={e.tenant_name}>{e.tenant_name}</td>
                      <td className="px-4 py-2.5 text-xs text-brand-muted font-mono">{e.method}</td>
                      <td className="px-4 py-2.5 text-xs text-brand-ink-2 font-mono max-w-[250px] truncate" title={e.endpoint}>{e.endpoint}</td>
                      <td className={`px-4 py-2.5 text-xs text-right font-mono ${statusColor(e.status_code)}`}>{e.status_code}</td>
                      <td className="px-4 py-2.5 text-xs text-brand-muted text-right font-sans">{e.latency_ms != null ? `${e.latency_ms}ms` : '—'}</td>
                      <td className="px-4 py-2.5 text-xs text-brand-muted text-right font-sans whitespace-nowrap">{new Date(e.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                  {(!accessLogs || accessLogs.length === 0) && (
                    <tr><td colSpan={6} className="px-5 py-8 text-sm text-brand-muted text-center font-sans">{alogLoading ? 'Loading…' : 'No traffic recorded'}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            {alogTotal > 50 && (
              <div className="flex items-center justify-between px-5 py-3 border-t border-brand-line">
                <button onClick={() => setAlogPage((p) => Math.max(1, p - 1))} disabled={alogPage === 1} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">← Prev</button>
                <span className="text-xs text-brand-muted font-sans">Page {alogPage} of {Math.ceil(alogTotal / 50)}</span>
                <button onClick={() => setAlogPage((p) => p + 1)} disabled={alogPage * 50 >= alogTotal} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">Next →</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── AI Routing Tab (Task 1206) ─────────────────────────────────────────────

const emptyRoute = () => ({ key_id: '', provider_id: '', model: '', capacity: 100, alternates: [], fallbacks: [] })
const isCompleteTarget = (target) => Boolean(target?.provider_id && target?.key_id && target?.model)
const modelTarget = (model) => ({ key_id: model.key_id, provider_id: model.provider_id, model: model.id, capacity: 100 })

function preferredModelOptions(models = []) {
  const legalReady = models.filter((model) => model.legal_eligible)
  const source = legalReady.length ? legalReady : models
  return [...source].sort((a, b) => {
    const tierRank = { recommended: 0, usable: 1, limited: 2, excluded: 3 }
    const aRank = tierRank[a.legal_tier] ?? 2
    const bRank = tierRank[b.legal_tier] ?? 2
    if (aRank !== bRank) return aRank - bRank
    if (Boolean(a.latency_eligible) !== Boolean(b.latency_eligible)) return a.latency_eligible ? -1 : 1
    if (Boolean(a.is_free) !== Boolean(b.is_free)) return a.is_free ? -1 : 1
    return String(a.id || '').localeCompare(String(b.id || ''))
  })
}

function routeIssues(route, allKeys) {
  const issues = []
  const hasAny = Boolean(route.provider_id || route.key_id || route.model)
  if (hasAny && !isCompleteTarget(route)) issues.push('Primary route needs provider, key, and model.')
  const key = allKeys.find((k) => k.id === route.key_id)
  if (key && route.provider_id && key.provider_id !== route.provider_id) {
    issues.push('Primary key does not belong to the selected provider.')
  }
  ;(route.alternates || []).forEach((alt, i) => {
    const hasAlternate = Boolean(alt.provider_id || alt.key_id || alt.model)
    if (hasAlternate && !isCompleteTarget(alt)) issues.push(`Balanced target ${i + 1} is incomplete.`)
    const altKey = allKeys.find((k) => k.id === alt.key_id)
    if (altKey && alt.provider_id && altKey.provider_id !== alt.provider_id) {
      issues.push(`Balanced target ${i + 1} key does not match its provider.`)
    }
  })
  ;(route.fallbacks || []).forEach((fb, i) => {
    const hasFallback = Boolean(fb.provider_id || fb.key_id || fb.model)
    if (hasFallback && !isCompleteTarget(fb)) issues.push(`Fallback ${i + 1} is incomplete.`)
    const fbKey = allKeys.find((k) => k.id === fb.key_id)
    if (fbKey && fb.provider_id && fbKey.provider_id !== fb.provider_id) {
      issues.push(`Fallback ${i + 1} key does not match its provider.`)
    }
  })
  return issues
}

function TargetEditor({ value, allKeys, presets, models, modelListId, onChange, compact = false }) {
  const selectedPreset = presets.find((p) => p.id === value.provider_id)
  const keysForPreset = value.provider_id ? allKeys.filter((k) => k.provider_id === value.provider_id) : allKeys
  const placeholder = selectedPreset?.model_placeholder || 'model-id'
  const suggestedModels = preferredModelOptions(models)

  const setField = (field, next) => {
    if (field === 'provider_id') onChange({ ...value, provider_id: next, key_id: '', model: '' })
    else if (field === 'key_id') onChange({ ...value, key_id: next, model: '' })
    else onChange({ ...value, [field]: next })
  }

  return (
    <div className={`grid grid-cols-1 ${compact ? 'md:grid-cols-[1fr_1fr_1.3fr_100px]' : 'lg:grid-cols-[1fr_1fr_1.3fr_110px]'} gap-3`}>
      <div>
        <label className="block text-xs text-brand-muted font-sans mb-1">Upstream provider</label>
        <select
          value={value.provider_id || ''}
          onChange={(e) => setField('provider_id', e.target.value)}
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
        >
          <option value="">Choose provider</option>
          {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {!compact && selectedPreset && <p className="text-[11px] text-brand-muted mt-1 font-sans">{selectedPreset.description}</p>}
      </div>
      <div>
        <label className="block text-xs text-brand-muted font-sans mb-1">Provider key</label>
        <select
          value={value.key_id || ''}
          onChange={(e) => setField('key_id', e.target.value)}
          disabled={!value.provider_id}
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface disabled:opacity-50"
        >
          <option value="">{value.provider_id ? 'Choose key' : 'Pick provider first'}</option>
          {keysForPreset.map((k) => <option key={k.id} value={k.id}>{k.name} (...{k.key_hint})</option>)}
        </select>
      </div>
      <div>
        <label className="block text-xs text-brand-muted font-sans mb-1">Upstream model ID</label>
        <input
          list={modelListId}
          value={value.model || ''}
          onChange={(e) => setField('model', e.target.value)}
          placeholder={placeholder}
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
        />
        <datalist id={modelListId}>
          {suggestedModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </datalist>
        {!compact && suggestedModels.length > 0 && (
          <p className="text-[11px] text-brand-muted mt-1 font-sans">
            Suggestions prioritize legal-ready models under 3s latency when latency data is available. Manual IDs still work.
          </p>
        )}
      </div>
      <div>
        <label className="block text-xs text-brand-muted font-sans mb-1">Capacity</label>
        <input
          type="number"
          min="1"
          max="1000"
          value={value.capacity ?? 100}
          onChange={(e) => setField('capacity', Number(e.target.value))}
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
        />
      </div>
    </div>
  )
}

function RouteFlow({ label, alias, route, presets, keys, balanceCount, fallbackCount }) {
  const preset = presets.find((p) => p.id === route.provider_id)
  const key = keys.find((k) => k.id === route.key_id)
  return (
    <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-3 items-stretch">
      <div className="border border-brand-line rounded-lg px-3 py-2 bg-brand-bg-soft">
        <p className="text-[11px] uppercase tracking-wider text-brand-muted font-sans">App route</p>
        <p className="text-sm text-brand-ink font-sans mt-1">{label}</p>
      </div>
      <div className="hidden md:flex items-center text-brand-muted"><ArrowDown size={14} className="-rotate-90" /></div>
      <div className="border border-brand-line rounded-lg px-3 py-2 bg-brand-bg-soft">
        <p className="text-[11px] uppercase tracking-wider text-brand-muted font-sans">LiteLLM alias</p>
        <p className="text-sm text-brand-ink font-mono mt-1">{alias}</p>
      </div>
      <div className="hidden md:flex items-center text-brand-muted"><ArrowDown size={14} className="-rotate-90" /></div>
      <div className="border border-brand-line rounded-lg px-3 py-2 bg-brand-bg-soft">
        <p className="text-[11px] uppercase tracking-wider text-brand-muted font-sans">Primary target</p>
        <p className="text-sm text-brand-ink font-sans mt-1 truncate" title={route.model || ''}>
          {preset?.name || 'Not selected'}{route.model ? ` / ${route.model}` : ''}
        </p>
        <p className="text-[11px] text-brand-muted font-sans mt-1">
          {key ? `${key.name} key` : 'No key'}{balanceCount ? `, ${balanceCount} balanced` : ''}{fallbackCount ? `, ${fallbackCount} fallback${fallbackCount === 1 ? '' : 's'}` : ''}
        </p>
      </div>
    </div>
  )
}

function RouteCard({ label, route, allKeys, presets, platformKey, catalogModels, onChange }) {
  const [fetchingModels, setFetchingModels] = useState(false)
  const [models, setModels] = useState([])
  const [modelsError, setModelsError] = useState(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)

  const routeKey = label.toLowerCase()
  const alias = `clarity-${routeKey}`
  const selectedPreset = presets.find((p) => p.id === route.provider_id)
  const selectedKey = allKeys.find((k) => k.id === route.key_id)
  const issues = routeIssues(route, allKeys)
  const ready = isCompleteTarget(route) && issues.length === 0
  const balanceCount = (route.alternates || []).filter(isCompleteTarget).length
  const fallbackCount = (route.fallbacks || []).filter(isCompleteTarget).length

  const updateRoute = (next) => {
    onChange({ ...next, alternates: next.alternates || [], fallbacks: next.fallbacks || [] })
    setTestResult(null)
  }
  const setAlternates = (alternates) => updateRoute({ ...route, alternates })
  const updateAlternate = (i, next) => {
    const alternates = [...(route.alternates || [])]
    alternates[i] = next
    setAlternates(alternates)
  }
  const removeAlternate = (i) => setAlternates((route.alternates || []).filter((_, idx) => idx !== i))
  const setFallbacks = (fallbacks) => updateRoute({ ...route, fallbacks })
  const updateFallback = (i, next) => {
    const fallbacks = [...(route.fallbacks || [])]
    fallbacks[i] = next
    setFallbacks(fallbacks)
  }
  const moveFallback = (i, direction) => {
    const fallbacks = [...(route.fallbacks || [])]
    const target = i + direction
    if (target < 0 || target >= fallbacks.length) return
    ;[fallbacks[i], fallbacks[target]] = [fallbacks[target], fallbacks[i]]
    setFallbacks(fallbacks)
  }
  const removeFallback = (i) => setFallbacks((route.fallbacks || []).filter((_, idx) => idx !== i))
  const modelsFor = (target) => {
    const catalog = (catalogModels || []).filter((m) => (
      target.key_id ? m.key_id === target.key_id : !target.provider_id || m.provider_id === target.provider_id
    ))
    if (target.key_id && target.key_id === route.key_id && models.length) return models
    return catalog.length ? catalog : models
  }

  const handleFetchModels = async () => {
    if (!route.key_id) return
    setFetchingModels(true)
    setModelsError(null)
    try {
      const data = await fetchProviderModels(platformKey, route.key_id)
      setModels(data.models || [])
      if (!data.models?.length) setModelsError('No models returned; paste the model ID manually.')
    } catch (e) {
      const status = e?.response?.status
      if (status === 403) setModelsError('Platform access was denied. Sign in again with the current platform key.')
      else if (status === 502) setModelsError(`${e?.response?.data?.detail || 'Provider rejected the model list request.'} Paste a known model ID or check the selected key.`)
      else setModelsError(e?.response?.data?.detail || 'Failed to fetch models')
    } finally { setFetchingModels(false) }
  }

  useEffect(() => {
    setModels([])
    setModelsError(null)
  }, [route.key_id])

  const handleTest = async () => {
    if (!ready) return
    setTesting(true)
    setTestResult(null)
    const startedAt = performance.now()
    try {
      const data = await testLLMRoute(platformKey, { key_id: route.key_id, provider_id: route.provider_id, model: route.model, route: routeKey })
      setTestResult({ ...data, client_roundtrip_ms: Math.round(performance.now() - startedAt) })
    } catch (e) {
      setTestResult({
        ok: false,
        error: e?.response?.data?.detail || 'Test failed',
        client_roundtrip_ms: Math.round(performance.now() - startedAt),
      })
    } finally { setTesting(false) }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
      <div className="px-5 py-4 border-b border-brand-line flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-serif font-bold text-brand-ink">{label}</h3>
            <span className={`text-[11px] font-sans px-2 py-0.5 rounded-full ${ready ? 'bg-brand-accent/10 text-brand-accent' : 'bg-brand-amber/10 text-brand-amber'}`}>
              {ready ? 'Ready' : 'Needs setup'}
            </span>
          </div>
          <p className="text-xs text-brand-muted font-mono mt-1">{alias}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleFetchModels}
            disabled={!route.key_id || fetchingModels}
            title="Fetch models for the selected key"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium border border-brand-line rounded-lg text-brand-ink hover:bg-brand-bg disabled:opacity-40 transition-colors"
          >
            <RefreshCw size={12} className={fetchingModels ? 'animate-spin' : ''} />
            Models
          </button>
          <button
            onClick={handleTest}
            disabled={testing || !ready}
            title="Send a synthetic prompt to this provider and model"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium border border-brand-line rounded-lg text-brand-ink hover:bg-brand-bg disabled:opacity-40 transition-colors"
          >
            {testing ? <RefreshCw size={12} className="animate-spin" /> : <Zap size={12} />}
            Test
          </button>
        </div>
      </div>

      {(issues.length > 0 || modelsError || testResult) && (
        <div className="px-5 py-3 border-b border-brand-line space-y-1 text-xs font-sans">
          {issues.map((issue) => <p key={issue} className="text-brand-rose">{issue}</p>)}
          {modelsError && <p className="text-brand-muted">{modelsError}</p>}
          {testResult && (
            <div className={testResult.ok ? 'text-brand-accent' : 'text-brand-rose'}>
              <p>
                {testResult.ok
                  ? `Test OK with ${testResult.model_used}: ${testResult.response_preview}`
                  : `Test failed: ${testResult.error}`}
              </p>
              <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] text-brand-muted">
                {testResult.client_roundtrip_ms != null && <span className="rounded border border-brand-line px-2 py-0.5">Browser {testResult.client_roundtrip_ms}ms</span>}
                {testResult.server_elapsed_ms != null && <span className="rounded border border-brand-line px-2 py-0.5">Server {testResult.server_elapsed_ms}ms</span>}
                {testResult.provider_latency_ms != null && <span className="rounded border border-brand-line px-2 py-0.5">Provider {testResult.provider_latency_ms}ms</span>}
                {testResult.server_overhead_ms != null && <span className="rounded border border-brand-line px-2 py-0.5">Overhead {testResult.server_overhead_ms}ms</span>}
                {testResult.tokens_per_second != null && <span className="rounded border border-brand-line px-2 py-0.5">{testResult.tokens_per_second} tok/s</span>}
                {testResult.completion_tokens != null && <span className="rounded border border-brand-line px-2 py-0.5">{testResult.completion_tokens} out / {testResult.prompt_tokens || 0} in</span>}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="p-5 space-y-5">
        <RouteFlow label={label} alias={alias} route={route} presets={presets} keys={allKeys} balanceCount={balanceCount} fallbackCount={fallbackCount} />
        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-bold text-brand-ink uppercase tracking-wider font-sans">Primary target</p>
            <p className="text-[11px] text-brand-muted font-sans">
              {selectedPreset?.name || 'No provider'}{selectedKey ? ` / ${selectedKey.name}` : ''}
            </p>
          </div>
          <TargetEditor
            value={route}
            allKeys={allKeys}
            presets={presets}
            models={modelsFor(route)}
            modelListId={`models-${routeKey}-primary`}
            onChange={(next) => updateRoute({ ...next, alternates: route.alternates || [], fallbacks: route.fallbacks || [] })}
          />
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <div>
              <p className="text-xs font-bold text-brand-ink uppercase tracking-wider font-sans">Load-balanced primaries</p>
              <p className="text-[11px] text-brand-muted font-sans">{balanceCount} additional deployment{balanceCount === 1 ? '' : 's'} under {alias}</p>
            </div>
            <button
              onClick={() => setAlternates([...(route.alternates || []), { key_id: '', provider_id: '', model: '', capacity: 100 }])}
              className="flex items-center gap-1.5 text-xs text-brand-accent hover:underline font-sans"
            >
              <Plus size={12} /> Add balanced target
            </button>
          </div>
          {(route.alternates || []).length === 0 && (
            <div className="border border-dashed border-brand-line rounded-lg px-4 py-4 text-sm text-brand-muted font-sans text-center">
              Add another target to let LiteLLM balance this alias across providers, keys, or models.
            </div>
          )}
          <div className="space-y-3">
            {(route.alternates || []).map((alt, i) => (
              <div key={i} className="border border-brand-line rounded-lg p-3 bg-brand-bg">
                <div className="flex items-center justify-between gap-3 mb-3">
                  <p className="text-xs font-mono text-brand-ink">{alias} balanced-{i + 1}</p>
                  <button onClick={() => removeAlternate(i)} title="Remove balanced target" className="p-1.5 text-brand-muted hover:text-brand-rose transition-colors">
                    <Trash2 size={14} />
                  </button>
                </div>
                <TargetEditor
                  value={alt}
                  allKeys={allKeys}
                  presets={presets}
                  models={modelsFor(alt)}
                  modelListId={`models-${routeKey}-alternate-${i}`}
                  compact
                  onChange={(next) => updateAlternate(i, next)}
                />
              </div>
            ))}
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <div>
              <p className="text-xs font-bold text-brand-ink uppercase tracking-wider font-sans">Fallback order</p>
              <p className="text-[11px] text-brand-muted font-sans">{fallbackCount} configured fallback{fallbackCount === 1 ? '' : 's'}</p>
            </div>
            <button
              onClick={() => setFallbacks([...(route.fallbacks || []), { key_id: '', provider_id: '', model: '', capacity: 100 }])}
              className="flex items-center gap-1.5 text-xs text-brand-accent hover:underline font-sans"
            >
              <Plus size={12} /> Add fallback
            </button>
          </div>
          {(route.fallbacks || []).length === 0 && (
            <div className="border border-dashed border-brand-line rounded-lg px-4 py-5 text-sm text-brand-muted font-sans text-center">
              No fallback targets. LiteLLM will only try the primary alias.
            </div>
          )}
          <div className="space-y-3">
            {(route.fallbacks || []).map((fb, i) => (
              <div key={i} className="border border-brand-line rounded-lg p-3 bg-brand-bg">
                <div className="flex items-center justify-between gap-3 mb-3">
                  <p className="text-xs font-mono text-brand-ink">{alias}-fb-{i}</p>
                  <div className="flex items-center gap-1">
                    <button onClick={() => moveFallback(i, -1)} disabled={i === 0} title="Move fallback up" className="p-1.5 text-brand-muted hover:text-brand-ink disabled:opacity-30">
                      <ArrowUp size={14} />
                    </button>
                    <button onClick={() => moveFallback(i, 1)} disabled={i === (route.fallbacks || []).length - 1} title="Move fallback down" className="p-1.5 text-brand-muted hover:text-brand-ink disabled:opacity-30">
                      <ArrowDown size={14} />
                    </button>
                    <button onClick={() => removeFallback(i)} title="Remove fallback" className="p-1.5 text-brand-muted hover:text-brand-rose transition-colors">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <TargetEditor
                  value={fb}
                  allKeys={allKeys}
                  presets={presets}
                  models={modelsFor(fb)}
                  modelListId={`models-${routeKey}-fallback-${i}`}
                  compact
                  onChange={(next) => updateFallback(i, next)}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function KeyVaultPanel({ platformKey, keys, presets, onKeysChange }) {
  const [showAdd, setShowAdd] = useState(false)
  const [newName, setNewName] = useState('')
  const [newProvider, setNewProvider] = useState('')
  const [newKey, setNewKey] = useState('')
  const [adding, setAdding] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [addError, setAddError] = useState(null)
  const [syncResult, setSyncResult] = useState(null)
  const [probingKey, setProbingKey] = useState(null)
  const [keyModels, setKeyModels] = useState({})

  const handleAdd = async (e) => {
    e.preventDefault()
    setAdding(true)
    setAddError(null)
    try {
      await addLLMProviderKey(platformKey, { name: newName, provider_id: newProvider, api_key: newKey })
      const updated = await getLLMProviderKeys(platformKey)
      onKeysChange(updated.keys || [])
      setShowAdd(false)
      setNewName(''); setNewProvider(''); setNewKey('')
    } catch (e) {
      setAddError(e?.response?.data?.detail || 'Failed to add key')
    } finally { setAdding(false) }
  }

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this API key from the vault?')) return
    try {
      await deleteLLMProviderKey(platformKey, id)
      onKeysChange(keys.filter((k) => k.id !== id))
    } catch { /* silent */ }
  }

  const handleSyncEnv = async () => {
    setSyncing(true)
    setSyncResult(null)
    try {
      const data = await syncEnvKeys(platformKey)
      setSyncResult(data)
      const updated = await getLLMProviderKeys(platformKey)
      onKeysChange(updated.keys || [])
    } catch (e) {
      setSyncResult({ synced: [], errors: [e?.response?.data?.detail || 'Sync failed'] })
    } finally { setSyncing(false) }
  }

  const handleProbeKey = async (keyId) => {
    setProbingKey(keyId)
    try {
      const data = await fetchProviderModels(platformKey, keyId)
      setKeyModels((prev) => ({ ...prev, [keyId]: data.models || [] }))
    } catch (e) {
      setKeyModels((prev) => ({ ...prev, [keyId]: { error: e?.response?.data?.detail || 'Probe failed' } }))
    } finally { setProbingKey(null) }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden mb-6">
      <div className="px-5 py-4 border-b border-brand-line flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="flex items-center gap-2">
          <Key size={16} className="text-brand-muted" />
          <h2 className="font-serif font-bold text-brand-ink">API Key Vault</h2>
          <span className="text-xs text-brand-muted font-sans">(keys stored encrypted)</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleSyncEnv}
            disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium border border-brand-line rounded-lg text-brand-ink-2 hover:bg-brand-bg disabled:opacity-40 transition-colors"
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
            Sync from env
          </button>
          <button
            onClick={() => setShowAdd((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium bg-brand-ink text-white rounded-lg hover:bg-brand-ink-2 transition-colors"
          >
            <Plus size={12} /> Add key
          </button>
        </div>
      </div>

      {syncResult && (
        <div className="px-5 py-3 border-b border-brand-line text-xs font-sans">
          {syncResult.synced?.length > 0 && (
            <p className="text-brand-accent">Synced: {syncResult.synced.map((s) => s.env_var).join(', ')}</p>
          )}
          {syncResult.errors?.length > 0 && (
            <p className="text-brand-muted">{syncResult.errors.join(' · ')}</p>
          )}
        </div>
      )}

      {showAdd && (
        <form onSubmit={handleAdd} className="px-5 py-4 border-b border-brand-line bg-brand-bg space-y-3">
          {addError && <p className="text-xs text-brand-rose font-sans">{addError}</p>}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-brand-muted font-sans mb-1">Display name</label>
              <input required value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. OpenCode Production" className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface" />
            </div>
            <div>
              <label className="block text-xs text-brand-muted font-sans mb-1">Provider</label>
              <select required value={newProvider} onChange={(e) => setNewProvider(e.target.value)} className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface">
                <option value="">Select…</option>
                {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-brand-muted font-sans mb-1">API key</label>
              <input required type="password" value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="sk-…" className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface" />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button type="submit" disabled={adding} className="px-4 py-2 text-xs font-medium font-sans bg-brand-ink text-white rounded-lg hover:bg-brand-ink-2 disabled:opacity-40 transition-colors">
              {adding ? 'Saving…' : 'Save key'}
            </button>
            <button type="button" onClick={() => { setShowAdd(false); setAddError(null) }} className="px-4 py-2 text-xs font-medium font-sans text-brand-muted hover:text-brand-ink transition-colors">
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="divide-y divide-brand-line">
        {keys.length === 0 && (
          <div className="px-5 py-8 text-sm text-brand-muted font-sans text-center space-y-2">
            <p>No API keys stored in the vault.</p>
            <p className="text-xs">Click <strong>Sync from env</strong> to import keys from <code className="bg-brand-bg px-1 rounded">DEEPSEEK_API_KEY</code>, <code className="bg-brand-bg px-1 rounded">OPENCODE_API_KEY</code>, or <code className="bg-brand-bg px-1 rounded">OPENROUTER_API_KEY</code>, or <strong>Add key</strong> to enter one manually.</p>
          </div>
        )}
        {keys.map((k) => {
          const preset = presets.find((p) => p.id === k.provider_id)
          const modelsForKey = keyModels[k.id]
          const isProbing = probingKey === k.id
          const modelCount = Array.isArray(modelsForKey) ? modelsForKey.length : null
          const probeError = modelsForKey?.error
          return (
            <div key={k.id} className="px-5 py-3 flex items-center justify-between hover:bg-brand-bg transition-colors gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-sm font-medium text-brand-ink font-sans">{k.name}</p>
                  {preset && (
                    <span className="text-[10px] uppercase tracking-wider text-brand-muted bg-brand-bg px-1.5 py-0.5 rounded font-sans">{preset.name}</span>
                  )}
                </div>
                <p className="text-xs text-brand-muted font-sans mt-0.5">
                  {preset?.description || k.provider_id}{modelCount !== null ? ` · ${modelCount} models available` : ''}
                  {probeError ? ` · ${probeError}` : ''}
                </p>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  onClick={() => handleProbeKey(k.id)}
                  disabled={isProbing}
                  title="Fetch available models for this key"
                  className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-sans border border-brand-line rounded-lg text-brand-ink hover:bg-brand-bg disabled:opacity-40 transition-colors"
                >
                  <RefreshCw size={11} className={isProbing ? 'animate-spin' : ''} />
                  Models
                </button>
                <button onClick={() => handleDelete(k.id)} title="Delete this key from the vault" className="p-1.5 text-brand-muted hover:text-brand-rose transition-colors">
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const CAPABILITY_LABELS = {
  vision: { label: 'Vision', color: 'bg-purple-100 text-purple-700 border-purple-200/60' },
  tool_use: { label: 'Tool Use', color: 'bg-blue-100 text-blue-700 border-blue-200/60' },
  reasoning: { label: 'Reasoning', color: 'bg-orange-100 text-orange-700 border-orange-200/60' },
  research: { label: 'Research', color: 'bg-indigo-100 text-indigo-700 border-indigo-200/60' },
  rag: { label: 'RAG', color: 'bg-teal-100 text-teal-700 border-teal-200/60' },
  legal: { label: 'Legal', color: 'bg-amber-100 text-amber-700 border-amber-200/60' },
  large_context: { label: '100K+ ctx', color: 'bg-green-100 text-green-700 border-green-200/60' },
  ultra_context: { label: '1M+ ctx', color: 'bg-emerald-100 text-emerald-700 border-emerald-200/60' },
  instruction: { label: 'Instruct', color: 'bg-cyan-100 text-cyan-700 border-cyan-200/60' },
  structured_output: { label: 'Structured', color: 'bg-pink-100 text-pink-700 border-pink-200/60' },
}

const LEGAL_TIER_LABELS = {
  recommended: { label: 'Legal-ready', color: 'bg-brand-accent/10 text-brand-accent border-brand-accent/20' },
  usable: { label: 'Usable', color: 'bg-blue-100 text-blue-700 border-blue-200/60' },
  limited: { label: 'Limited', color: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20' },
  excluded: { label: 'Excluded', color: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20' },
}

const EXCLUSION_REASON_LABELS = {
  not_free: 'Paid model',
  not_chat_model: 'Not chat/instruction',
  not_text_chat: 'Not text chat',
  coding_specialized: 'Coding-only/specialized',
  low_context: 'Low context',
  low_output_limit: 'Low output limit',
  slow_latency: 'Latency over 3s',
  not_instruction_tuned: 'Not instruction-tuned',
  insufficient_legal_signals: 'Weak legal/RAG signals',
}

function ApplyRouteMenu({ model, onApply }) {
  const [open, setOpen] = useState(false)
  const canApply = Boolean(model.key_id && model.provider_id && model.id)

  const routeGroups = [
    {
      routeName: 'standard',
      label: 'Standard',
      description: 'Normal chat, summaries, and routine work',
      actions: [
        { placement: 'primary', label: 'Set as primary' },
        { placement: 'alternate', label: 'Add balanced target' },
        { placement: 'fallback', label: 'Add fallback' },
      ],
    },
    {
      routeName: 'premium',
      label: 'Premium',
      description: 'Higher-quality drafting and harder analysis',
      actions: [
        { placement: 'primary', label: 'Set as primary' },
        { placement: 'alternate', label: 'Add balanced target' },
        { placement: 'fallback', label: 'Add fallback' },
      ],
    },
  ]

  const handleSelect = (routeName, placement) => {
    onApply(routeName, placement, model)
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={!canApply}
        title={canApply ? 'Apply this model to a LiteLLM route' : 'This catalog row is missing provider or key metadata'}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium border border-brand-line rounded-lg text-brand-ink hover:bg-brand-bg disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        Apply
        <ChevronDown size={11} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-20 w-72 bg-brand-surface border border-brand-line rounded-lg shadow-lg py-2">
            {routeGroups.map((group, groupIndex) => (
              <div key={group.routeName} className={groupIndex > 0 ? 'border-t border-brand-line pt-2 mt-2' : ''}>
                <div className="px-3 pb-1">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-brand-ink font-sans">{group.label}</p>
                  <p className="text-[11px] text-brand-muted font-sans">{group.description}</p>
                </div>
                {group.actions.map(({ placement, label }) => (
                  <button
                    key={`${group.routeName}-${placement}`}
                    type="button"
                    onClick={() => handleSelect(group.routeName, placement)}
                    className="w-full text-left px-3 py-2 text-xs font-sans text-brand-ink hover:bg-brand-bg transition-colors"
                  >
                    {label}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function ModelCatalogPanel({ catalog, refreshing, onRefresh, onApply }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('recommended')
  const [capFilter, setCapFilter] = useState(null)
  const [showAll, setShowAll] = useState(false)
  const models = catalog?.models || []

  const baseFiltered = models.filter((model) => {
    const q = query.trim().toLowerCase()
    const reasonText = (model.exclusion_reasons || []).map((reason) => EXCLUSION_REASON_LABELS[reason] || reason).join(' ')
    const matchesQuery = !q || [model.id, model.name, model.provider_name, model.key_name, model.legal_tier, reasonText].filter(Boolean).some((value) => String(value).toLowerCase().includes(q))
    const matchesFilter =
      (filter === 'recommended' && model.legal_tier === 'recommended') ||
      (filter === 'free_legal' && model.is_free && model.legal_eligible) ||
      (filter === 'all_free' && model.is_free) ||
      (filter === 'excluded' && model.legal_tier === 'excluded')
    return matchesQuery && matchesFilter
  })

  const capabilityCounts = baseFiltered.reduce((counts, model) => {
    ;(model.capabilities || []).forEach((capability) => {
      counts[capability] = (counts[capability] || 0) + 1
    })
    return counts
  }, {})

  const fullFiltered = baseFiltered.filter((model) => {
    const matchesCap = !capFilter || (model.capabilities || []).includes(capFilter)
    return matchesCap
  })

  const filtered = showAll ? fullFiltered : fullFiltered.slice(0, 60)
  const hiddenCount = fullFiltered.length - filtered.length
  const displayedLabel = showAll ? `${fullFiltered.length} of ${models.length}` : `${Math.min(filtered.length, 60)} of ${fullFiltered.length}`

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden mb-6">
      <div className="px-5 py-4 border-b border-brand-line flex flex-col xl:flex-row xl:items-center xl:justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Globe size={16} className="text-brand-muted" />
            <h2 className="font-serif font-bold text-brand-ink">Live Model Catalog</h2>
          </div>
          <p className="text-xs text-brand-muted font-sans mt-1">
            Fetched from stored provider keys. New/free tags are derived from provider model lists and saved catalog snapshots.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-sans px-2 py-1 rounded-full bg-brand-bg border border-brand-line text-brand-ink">{catalog?.model_count || 0} models</span>
          <span className="text-[11px] font-sans px-2 py-1 rounded-full bg-brand-accent/10 text-brand-accent">{catalog?.free_count || 0} free</span>
          <span className="text-[11px] font-sans px-2 py-1 rounded-full bg-brand-green/10 text-brand-green">{catalog?.free_legal_count || 0} free legal</span>
          <span className="text-[11px] font-sans px-2 py-1 rounded-full bg-brand-rose/10 text-brand-rose">{catalog?.excluded_count || 0} excluded</span>
          <span className="text-[11px] font-sans px-2 py-1 rounded-full bg-brand-amber/10 text-brand-amber">{catalog?.new_count || 0} new</span>
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium border border-brand-line rounded-lg text-brand-ink hover:bg-brand-bg disabled:opacity-40 transition-colors"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh providers
          </button>
        </div>
      </div>

      <div className="px-5 py-3 border-b border-brand-line flex flex-col md:flex-row gap-3">
        <input
          value={query}
          onChange={(e) => { setQuery(e.target.value); setShowAll(false) }}
          placeholder="Search model, provider, or key"
          className="flex-1 border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
        />
        <div className="flex rounded-lg border border-brand-line overflow-hidden shrink-0">
          {[
            ['recommended', 'Recommended'],
            ['free_legal', 'Free Legal'],
            ['all_free', 'All Free'],
            ['excluded', 'Excluded'],
          ].map(([id, label]) => (
            <button
              key={id}
              onClick={() => { setFilter(id); setShowAll(false) }}
              className={`px-3 py-2 text-xs font-sans ${filter === id ? 'bg-brand-ink text-white' : 'bg-brand-surface text-brand-muted hover:text-brand-ink'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="px-5 py-2 border-b border-brand-line flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-brand-muted font-sans mr-1">Filter by capability:</span>
        {Object.entries(CAPABILITY_LABELS).map(([key, { label }]) => {
          const count = capabilityCounts[key] || 0
          const disabled = count === 0 && capFilter !== key
          return (
            <button
              key={key}
              onClick={() => {
                if (!disabled) setCapFilter(capFilter === key ? null : key)
              }}
              disabled={disabled}
              title={disabled ? 'No models in the current result set advertise this capability' : `${count} matching model${count === 1 ? '' : 's'}`}
              className={`text-[10px] font-sans px-2 py-0.5 rounded-full border transition-colors ${
                capFilter === key
                  ? 'bg-brand-ink text-white border-brand-ink'
                  : disabled
                    ? 'text-brand-muted/40 border-brand-line/40 cursor-not-allowed'
                    : 'text-brand-muted border-brand-line/60 hover:text-brand-ink hover:bg-brand-bg'
              }`}
            >
              {label} <span className="opacity-70">{count}</span>
            </button>
          )
        })}
        {capFilter && (
          <button onClick={() => setCapFilter(null)} className="text-[10px] text-brand-muted hover:text-brand-rose font-sans ml-1">
            ✕ clear
          </button>
        )}
      </div>

      {catalog?.errors?.length > 0 && (
        <div className="px-5 py-3 border-b border-brand-line text-xs text-brand-rose font-sans">
          {catalog.errors.slice(0, 3).map((err) => `${err.key_name || err.provider_id}: ${err.error}`).join(' · ')}
        </div>
      )}

      <div className="max-h-[420px] overflow-y-auto divide-y divide-brand-line">
        {fullFiltered.length === 0 && (
          <p className="px-5 py-8 text-sm text-brand-muted font-sans text-center">
            {catalog?.last_refreshed_at ? 'No models match the current filters.' : 'Refresh providers to build the model catalog.'}
          </p>
        )}
        {filtered.map((model) => {
          const pricing = model.pricing
          const hasPricing = pricing && (pricing.prompt !== '0' || pricing.completion !== '0' || (pricing.request && pricing.request !== '0'))
          const tier = LEGAL_TIER_LABELS[model.legal_tier]
          const reasons = model.exclusion_reasons || []
          return (
            <div key={`${model.key_id}-${model.id}`} className="px-5 py-3 flex flex-col xl:flex-row xl:items-start xl:justify-between gap-3 hover:bg-brand-bg transition-colors">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <p className="text-sm font-mono text-brand-ink truncate" title={model.id}>{model.id}</p>
                  {model.is_free && <span className="text-[10px] uppercase tracking-wider font-medium bg-brand-accent/10 text-brand-accent px-1.5 py-0.5 rounded font-sans">Free</span>}
                  {model.is_new && <span className="text-[10px] uppercase tracking-wider font-medium bg-brand-amber/10 text-brand-amber px-1.5 py-0.5 rounded font-sans">New</span>}
                  {tier && (
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border font-sans ${tier.color}`} title={reasons.length ? reasons.map((reason) => EXCLUSION_REASON_LABELS[reason] || reason).join(', ') : tier.label}>
                      {tier.label}
                    </span>
                  )}
                  {(model.eligibility_badges || []).filter((badge) => badge !== tier?.label).map((badge) => (
                    <span key={badge} className="text-[10px] font-medium px-1.5 py-0.5 rounded border font-sans bg-brand-bg text-brand-ink border-brand-line">
                      {badge}
                    </span>
                  ))}
                  {(model.capabilities || []).map(cap => {
                    const cfg = CAPABILITY_LABELS[cap]
                    return cfg ? (
                      <span key={cap} className={`text-[10px] font-medium px-1.5 py-0.5 rounded border font-sans ${cfg.color}`} title={cfg.label}>
                        {cfg.label}
                      </span>
                    ) : null
                  })}
                </div>
                <p className="text-xs text-brand-muted font-sans mt-1">
                  {model.provider_name || model.provider_id} · {model.key_name || 'key'}
                  {model.context_length ? ` · ${Number(model.context_length).toLocaleString()} ctx` : ''}
                  {hasPricing ? ` · $${pricing.prompt}/$${pricing.completion}` : ''}
                  {model.latency_ms != null ? ` · ${model.latency_ms}ms latency` : ''}
                  {model.modality ? ` · ${model.modality}` : ''}
                </p>
                {reasons.length > 0 && (
                  <p className="text-[11px] text-brand-rose font-sans mt-1">
                    Excluded: {reasons.map((reason) => EXCLUSION_REASON_LABELS[reason] || reason).join(', ')}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <ApplyRouteMenu model={model} onApply={onApply} />
              </div>
            </div>
          )
        })}
      </div>

      {(hiddenCount > 0 || showAll) && (
        <div className="px-5 py-2 border-t border-brand-line flex items-center justify-between text-[11px] font-sans">
          <span className="text-brand-muted">{displayedLabel} shown{capFilter ? ` · filtered by ${CAPABILITY_LABELS[capFilter]?.label || capFilter}` : ''}</span>
          <button
            onClick={() => setShowAll(!showAll)}
            className="font-medium text-brand-accent hover:underline"
          >
            {showAll ? 'Show fewer' : `Show all ${fullFiltered.length}`}
          </button>
        </div>
      )}

      {catalog?.last_refreshed_at && (
        <p className="px-5 py-2 border-t border-brand-line text-[11px] text-brand-muted font-sans">
          Last refresh: {new Date(catalog.last_refreshed_at).toLocaleString()}
        </p>
      )}
    </div>
  )
}

function LiteLLMGatewayPanel({ status, checking, reloading, onCheck, onReload }) {
  const current = status?.status || 'unknown'
  const style = {
    online: 'bg-brand-accent/10 text-brand-accent border-brand-accent/20',
    degraded: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    offline: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    disabled: 'bg-brand-bg text-brand-muted border-brand-line',
    unknown: 'bg-brand-bg text-brand-muted border-brand-line',
  }[current] || 'bg-brand-bg text-brand-muted border-brand-line'
  const StatusIcon = current === 'online' ? CheckCircle : current === 'offline' ? XCircle : current === 'degraded' ? AlertTriangle : Server
  const aliases = Object.entries(status?.aliases || {})
  const checkedAt = status?.checked_at ? new Date(status.checked_at).toLocaleTimeString() : null

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden mb-6">
      <div className="px-5 py-4 border-b border-brand-line flex flex-col xl:flex-row xl:items-center xl:justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Server size={16} className="text-brand-muted" />
            <h2 className="font-serif font-bold text-brand-ink">LiteLLM Gateway</h2>
            <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-sans capitalize ${style}`}>
              <StatusIcon size={11} />
              {current}
            </span>
            {checkedAt && <span className="text-[11px] text-brand-muted font-sans">Checked {checkedAt}</span>}
          </div>
          <p className="text-xs text-brand-muted font-mono mt-1 break-all">{status?.base_url || 'No LiteLLM base URL configured'}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onCheck}
            disabled={checking}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium border border-brand-line rounded-lg text-brand-ink hover:bg-brand-bg disabled:opacity-40 transition-colors"
          >
            <RefreshCw size={12} className={checking ? 'animate-spin' : ''} />
            Check Status
          </button>
          <button
            type="button"
            onClick={onReload}
            disabled={reloading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium bg-brand-ink text-white rounded-lg hover:bg-brand-ink-2 disabled:opacity-40 transition-colors"
          >
            <RefreshCw size={12} className={reloading ? 'animate-spin' : ''} />
            Reload LiteLLM
          </button>
        </div>
      </div>

      <div className="px-5 py-3 grid grid-cols-2 md:grid-cols-4 gap-3 border-b border-brand-line">
        <div>
          <p className="text-[10px] uppercase tracking-wider text-brand-muted font-sans">API key</p>
          <p className="text-sm text-brand-ink font-sans">{status?.api_key_configured ? 'Configured' : 'Missing'}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-brand-muted font-sans">Latency</p>
          <p className="text-sm text-brand-ink font-sans">{status?.latency_ms != null ? `${status.latency_ms}ms` : '—'}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-brand-muted font-sans">Models</p>
          <p className="text-sm text-brand-ink font-sans">{status?.models_count ?? '—'}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-brand-muted font-sans">Enabled</p>
          <p className="text-sm text-brand-ink font-sans">{status?.enabled ? 'Yes' : 'No'}</p>
        </div>
      </div>

      <div className="px-5 py-3 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {aliases.length === 0 && (
            <span className="text-xs text-brand-muted font-sans">No aliases reported yet.</span>
          )}
          {aliases.map(([alias, present]) => (
            <span
              key={alias}
              className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] font-mono ${
                present
                  ? 'bg-brand-accent/10 text-brand-accent border-brand-accent/20'
                  : 'bg-brand-amber/10 text-brand-amber border-brand-amber/20'
              }`}
            >
              {present ? <CheckCircle size={11} /> : <AlertTriangle size={11} />}
              {alias}
            </span>
          ))}
        </div>
        {(status?.detail || status?.models_error) && (
          <p className="text-xs text-brand-muted font-sans max-w-3xl">
            {status.detail || status.models_error}
          </p>
        )}
      </div>
    </div>
  )
}

function AIRoutingTab({ platformKey, onAuthError }) {
  const [keys, setKeys] = useState([])
  const [presets, setPresets] = useState([])
  const [catalog, setCatalog] = useState(null)
  const [gatewayStatus, setGatewayStatus] = useState(null)
  const [refreshingCatalog, setRefreshingCatalog] = useState(false)
  const [checkingGateway, setCheckingGateway] = useState(false)
  const [reloadingRoutes, setReloadingRoutes] = useState(false)
  const [standard, setStandard] = useState(emptyRoute)
  const [premium, setPremium] = useState(emptyRoute)
  const [saving, setSaving] = useState(false)
  const [saveResult, setSaveResult] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const keysData = await getLLMProviderKeys(platformKey)
      const presetsData = await getLLMProviderPresets(platformKey)
      const routesData = await getLLMRoutes(platformKey)
      const catalogData = await getLLMModelCatalog(platformKey)
      const gatewayData = await getLLMGatewayStatus(platformKey)
      setKeys(keysData.keys || [])
      setPresets(presetsData.providers || [])
      setCatalog(catalogData)
      setGatewayStatus(gatewayData)
      const std = routesData.standard || {}
      const prem = routesData.premium || {}
      setStandard({ key_id: std.key_id || '', provider_id: std.provider_id || '', model: std.model || '', capacity: std.capacity || 100, alternates: std.alternates || [], fallbacks: std.fallbacks || [] })
      setPremium({ key_id: prem.key_id || '', provider_id: prem.provider_id || '', model: prem.model || '', capacity: prem.capacity || 100, alternates: prem.alternates || [], fallbacks: prem.fallbacks || [] })
    } catch (e) {
      if (e?.response?.status === 403) {
        setLoadError('Platform access was denied. Sign in again with the current platform key.')
        onAuthError?.()
      } else {
        setLoadError(e?.response?.data?.detail || 'Failed to load AI routing configuration.')
      }
    }
    finally { setLoading(false) }
  }, [platformKey, onAuthError])

  useEffect(() => { load() }, [load])

  const validationIssues = [...routeIssues(standard, keys), ...routeIssues(premium, keys)]
  const configuredCount = [standard, premium].filter(isCompleteTarget).length

  const reloadSummary = (data, successPrefix = 'LiteLLM reloaded') => {
    const firstBuildError = data.build_errors?.[0]
    if (data.litellm_updated) {
      return `${successPrefix}: ${data.models_registered || 0} model(s), ${data.fallbacks_registered || 0} fallback(s)`
    }
    return `LiteLLM reload failed: ${data.litellm_error || firstBuildError || 'No complete route targets were available'}`
  }

  const replaceKeys = (nextKeys) => {
    setKeys(nextKeys)
    const validIds = new Set(nextKeys.map((k) => k.id))
    const clearMissingKeys = (route) => ({
      ...route,
      ...(route.key_id && !validIds.has(route.key_id) ? { key_id: '', model: '' } : {}),
      alternates: (route.alternates || []).map((alt) => (
        alt.key_id && !validIds.has(alt.key_id) ? { ...alt, key_id: '', model: '' } : alt
      )),
      fallbacks: (route.fallbacks || []).map((fb) => (
        fb.key_id && !validIds.has(fb.key_id) ? { ...fb, key_id: '', model: '' } : fb
      )),
    })
    setStandard((prev) => clearMissingKeys(prev))
    setPremium((prev) => clearMissingKeys(prev))
  }

  const handleRefreshCatalog = async () => {
    setRefreshingCatalog(true)
    setSaveResult(null)
    try {
      const data = await refreshLLMModelCatalog(platformKey)
      setCatalog(data)
    } catch (e) {
      setSaveResult({ ok: false, error: e?.response?.data?.detail || 'Model catalog refresh failed' })
    } finally { setRefreshingCatalog(false) }
  }

  const handleCheckGateway = async () => {
    setCheckingGateway(true)
    try {
      const data = await getLLMGatewayStatus(platformKey)
      setGatewayStatus(data)
    } catch (e) {
      if (e?.response?.status === 403) onAuthError?.()
      setSaveResult({ ok: false, error: e?.response?.data?.detail || 'LiteLLM status check failed' })
    } finally { setCheckingGateway(false) }
  }

  const handleReloadRoutes = async () => {
    setReloadingRoutes(true)
    setSaveResult(null)
    try {
      const data = await reloadLLMRoutes(platformKey)
      if (data.gateway_status) setGatewayStatus(data.gateway_status)
      setSaveResult({
        ok: Boolean(data.litellm_updated),
        message: reloadSummary(data),
      })
    } catch (e) {
      if (e?.response?.status === 403) onAuthError?.()
      setSaveResult({ ok: false, error: e?.response?.data?.detail || 'LiteLLM reload failed' })
    } finally { setReloadingRoutes(false) }
  }

  const applyModel = (routeName, placement, model) => {
    const setter = routeName === 'standard' ? setStandard : setPremium
    const target = modelTarget(model)
    setter((prev) => {
      if (placement === 'primary') return { ...prev, ...target }
      if (placement === 'alternate') return { ...prev, alternates: [...(prev.alternates || []), target] }
      return { ...prev, fallbacks: [...(prev.fallbacks || []), target] }
    })
    const routeLabel = routeName === 'standard' ? 'Standard' : 'Premium'
    const placementLabel = placement === 'alternate' ? 'balanced target' : placement
    setSaveResult({
      ok: true,
      pending: true,
      message: `Applied ${model.id} to ${routeLabel} ${placementLabel}. Save Routes to reload LiteLLM.`,
    })
  }

  const handleSave = async () => {
    if (validationIssues.length > 0) {
      setSaveResult({ ok: false, error: validationIssues[0] })
      return
    }
    setSaving(true)
    setSaveResult(null)
    try {
      const data = await saveLLMRoutes(platformKey, { standard, premium })
      if (data.gateway_status) setGatewayStatus(data.gateway_status)
      setSaveResult({
        ok: Boolean(data.litellm_updated),
        litellm_updated: data.litellm_updated,
        litellm_error: data.litellm_error || null,
        models_registered: data.models_registered,
        fallbacks_registered: data.fallbacks_registered || 0,
        app_aliases: data.app_aliases || null,
        message: reloadSummary(data, 'Saved and reloaded LiteLLM'),
      })
    } catch (e) {
      setSaveResult({ ok: false, error: e?.response?.data?.detail || 'Save failed' })
    } finally { setSaving(false) }
  }

  if (loading) {
    return <div className="flex justify-center py-16"><div className="w-8 h-8 border-2 border-brand-accent border-t-transparent rounded-full animate-spin" /></div>
  }

  if (loadError) {
    return (
      <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-lg px-4 py-3 text-sm text-brand-rose font-sans">
        {loadError}
      </div>
    )
  }

  return (
    <div>
      <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-4 mb-6">
        <div>
          <h2 className="text-xl font-serif font-bold text-brand-ink">AI Provider Routing</h2>
          <p className="text-sm text-brand-muted font-sans mt-1">Register standard and premium aliases in LiteLLM, then order fallback targets for each route.</p>
          <div className="flex flex-wrap items-center gap-2 mt-3">
            <span className="text-[11px] font-sans px-2 py-1 rounded-full bg-brand-bg border border-brand-line text-brand-ink">clarity-standard</span>
            <span className="text-[11px] font-sans px-2 py-1 rounded-full bg-brand-bg border border-brand-line text-brand-ink">clarity-premium</span>
            <span className={`text-[11px] font-sans px-2 py-1 rounded-full ${validationIssues.length ? 'bg-brand-amber/10 text-brand-amber' : 'bg-brand-accent/10 text-brand-accent'}`}>
              {validationIssues.length ? `${validationIssues.length} issue${validationIssues.length === 1 ? '' : 's'}` : `${configuredCount}/2 primary routes configured`}
            </span>
          </div>
        </div>
        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
          {saveResult && (
            <span className={`text-xs font-sans ${saveResult.ok ? (saveResult.pending ? 'text-brand-amber' : 'text-brand-accent') : 'text-brand-rose'}`}>
              {saveResult.message || (saveResult.ok
                ? `Saved - ${saveResult.app_aliases?.standard || 'clarity-standard'} / ${saveResult.app_aliases?.premium || 'clarity-premium'}${saveResult.litellm_updated ? `, ${saveResult.models_registered} model(s), ${saveResult.fallbacks_registered} fallback(s) reloaded` : `, DB only; LiteLLM not reloaded${saveResult.litellm_error ? ` (${saveResult.litellm_error})` : ''}`}`
                : saveResult.error)}
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={saving || validationIssues.length > 0}
            className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-sm font-medium font-sans rounded-lg hover:bg-brand-ink-2 disabled:opacity-40 transition-colors"
          >
            {saving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
            {saving ? 'Saving…' : 'Save Routes'}
          </button>
        </div>
      </div>

      <LiteLLMGatewayPanel
        status={gatewayStatus}
        checking={checkingGateway}
        reloading={reloadingRoutes}
        onCheck={handleCheckGateway}
        onReload={handleReloadRoutes}
      />

      <KeyVaultPanel platformKey={platformKey} keys={keys} presets={presets} onKeysChange={replaceKeys} />

      <ModelCatalogPanel
        catalog={catalog}
        refreshing={refreshingCatalog}
        onRefresh={handleRefreshCatalog}
        onApply={applyModel}
      />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <RouteCard
          label="Standard"
          route={standard}
          allKeys={keys}
          presets={presets}
          platformKey={platformKey}
          catalogModels={catalog?.models || []}
          onChange={setStandard}
        />
        <RouteCard
          label="Premium"
          route={premium}
          allKeys={keys}
          presets={presets}
          platformKey={platformKey}
          catalogModels={catalog?.models || []}
          onChange={setPremium}
        />
      </div>
    </div>
  )
}

export default function PlatformPage() {
  const [platformKey, setPlatformKey] = useState(() => sessionStorage.getItem('platform_key') || null)
  const [tab, setTab] = useState('dashboard')
  const [tenants, setTenants] = useState([])
  const [usage, setUsage] = useState(null)
  const [health, setHealth] = useState(null)
  const [llmConfig, setLlmConfig] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [expandedTenant, setExpandedTenant] = useState(null)
  const [tenantDetail, setTenantDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [limit, setLimit] = useState(50)
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
      const promises = [getPlatformTenants(platformKey, page), getPlatformUsage(platformKey), getPlatformLLMConfig(platformKey)]
      if (tab === 'health') promises.push(getPlatformHealth(platformKey))
      const results = await Promise.all(promises)
      setTenants(results[0].tenants)
      setTotal(results[0].total)
      setLimit(results[0].limit || 50)
      setUsage(results[1])
      setLlmConfig(results[2].config)
      if (results[3]) setHealth(results[3])
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load')
      if (e?.response?.status === 403) { sessionStorage.removeItem('platform_key'); setPlatformKey(null) }
    } finally { setLoading(false) }
  }, [platformKey, page, tab])

  useEffect(() => { loadData() }, [loadData])

  const handleUpdate = (id, changes) => {
    setTenants((prev) => prev.map((t) => (t.id === id ? { ...t, ...changes } : t)))
    if (expandedTenant === id && changes.llm_config) {
      const cfg = changes.llm_config
      setTenantDetail((prev) => prev ? ({
        ...prev,
        llm_config: {
          ...(prev.llm_config || {}),
          standard_provider: cfg.standard_llm_provider ?? cfg.standard_provider ?? null,
          standard_model: cfg.standard_llm_model ?? cfg.standard_model ?? null,
          premium_provider: cfg.premium_llm_provider ?? cfg.premium_provider ?? null,
          premium_model: cfg.premium_llm_model ?? cfg.premium_model ?? null,
          provider: cfg.standard_llm_provider ?? cfg.standard_provider ?? null,
          model: cfg.standard_llm_model ?? cfg.standard_model ?? null,
        },
      }) : prev)
    }
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
    { id: 'ai-routing', label: 'AI Routing', icon: Cpu },
    { id: 'logs', label: 'Logs', icon: FileText },
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
          <button onClick={() => { sessionStorage.removeItem('platform_key'); setPlatformKey(null); setTenants([]); setUsage(null); setHealth(null); setLlmConfig(null) }} className="text-xs text-brand-muted hover:text-brand-rose font-sans transition-colors">Sign out</button>
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

            <RoutingOverviewPanel config={llmConfig} onOpenRouting={() => setTab('ai-routing')} />

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
                                                <p className="text-brand-ink font-sans font-medium">{u.full_name || `User ${u.id.slice(0, 8)}`}</p>
                                                <p className="text-xs text-brand-muted font-mono">{u.id.slice(0, 8)}&hellip;</p>
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
                                    <h4 className="text-xs font-bold text-brand-ink uppercase tracking-wider mb-3 font-sans">AI Alias Override</h4>
                                    <TenantAliasOverride
                                      tenant={t}
                                      tenantDetail={tenantDetail}
                                      platformKey={platformKey}
                                      defaultAliases={{
                                        standard: llmConfig?.standard_model || 'clarity-standard',
                                        premium: llmConfig?.premium_model || 'clarity-premium',
                                      }}
                                      onUpdate={handleUpdate}
                                      saving={savingProvider}
                                      setSaving={setSavingProvider}
                                    />
                                  </div>
                                  {/* Plan / module bundle override */}
                                  <div className="mt-4 pt-4 border-t border-brand-line">
                                    <h4 className="text-xs font-bold text-brand-ink uppercase tracking-wider mb-3 font-sans">Plan</h4>
                                    <TenantPlanOverride
                                      tenant={t}
                                      tenantDetail={tenantDetail}
                                      platformKey={platformKey}
                                      onUpdate={handleUpdate}
                                    />
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
                {total > limit && (
                  <div className="flex items-center justify-between px-5 py-3 border-t border-brand-line">
                    <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">← Prev</button>
                    <span className="text-xs text-brand-muted font-sans">Page {page} of {Math.ceil(total / limit)} ({total} total)</span>
                    <button onClick={() => setPage((p) => p + 1)} disabled={page * limit >= total} className="text-sm text-brand-muted hover:text-brand-ink disabled:opacity-40 font-sans">Next →</button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Logs Tab ── */}
        {tab === 'logs' && <LogsTab platformKey={platformKey} tenants={tenants} />}

        {/* ── AI Routing Tab ── */}
        {tab === 'ai-routing' && (
          <AIRoutingTab
            platformKey={platformKey}
            onAuthError={() => {
              sessionStorage.removeItem('platform_key')
              setPlatformKey(null)
            }}
          />
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
                  {(health?.services || [
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
