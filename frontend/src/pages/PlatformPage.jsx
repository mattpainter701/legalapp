import React, { useState, useEffect, useCallback } from 'react'
import { getPlatformTenants, getPlatformUsage, getPlatformHealth, getPlatformTenant, updatePlatformTenant, getPlatformLLMProviders, getPlatformLLMConfig, updatePlatformLLMConfig, getPlatformLogs, getPlatformLogsSummary, getPlatformTenantLogs, getPlatformTenantLogsSummary, getPlatformAccessLogs, getPlatformAccessLogsSummary, getLLMProviderPresets, getLLMProviderKeys, addLLMProviderKey, deleteLLMProviderKey, syncEnvKeys, fetchProviderModels, getLLMRoutes, saveLLMRoutes, testLLMRoute } from '../api'
import { Activity, AlertTriangle, Database, Server, Shield, Users, Zap, Search, ChevronDown, ChevronRight, BarChart3, FileText, Globe, Key, Plus, Trash2, RefreshCw, CheckCircle, XCircle, Cpu } from 'lucide-react'

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

function ModelRouteFields({ label, provider, model, providers, modelListId, onProviderChange, onModelChange, defaultLabel = 'Platform default' }) {
  const selectedProviderObj = providers.find((p) => p.key === provider)
  const models = selectedProviderObj?.models || []
  return (
    <div className="grid grid-cols-1 md:grid-cols-[180px_1fr_1fr] gap-3 items-end">
      <div>
        <p className="text-xs font-bold text-brand-ink uppercase tracking-wider font-sans mb-1">{label}</p>
        <p className="text-[11px] text-brand-muted font-sans">{provider || defaultLabel}</p>
      </div>
      <div>
        <label className="block text-xs text-brand-muted font-sans mb-1">Provider</label>
        <select
          value={provider}
          onChange={(e) => onProviderChange(e.target.value)}
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
        >
          <option value="">{defaultLabel}</option>
          {providers.map((p) => (
            <option key={p.key} value={p.key} disabled={!p.configured}>
              {p.label} {p.free_tier ? '(free)' : ''} {!p.configured ? '- not configured' : ''}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="block text-xs text-brand-muted font-sans mb-1">Model</label>
        <input
          list={modelListId}
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
          placeholder="Default, fetched model, or pasted id"
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
        />
        <datalist id={modelListId}>
          {models.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </div>
      {provider && !selectedProviderObj?.configured && (
        <p className="md:col-start-2 md:col-span-2 text-xs text-brand-rose font-sans">This provider is not configured at the platform level.</p>
      )}
    </div>
  )
}

function LLMProviderSelect({ tenant, tenantDetail, platformKey, providers, onUpdate, saving, setSaving }) {
  const config = tenantDetail?.llm_config || {}
  const current = {
    standardProvider: config.standard_provider || config.provider || '',
    standardModel: config.standard_model || config.model || '',
    premiumProvider: config.premium_provider || '',
    premiumModel: config.premium_model || '',
  }
  const [values, setValues] = useState(current)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setValues(current)
    setSaved(false)
  }, [config.standard_provider, config.standard_model, config.premium_provider, config.premium_model, config.provider, config.model])

  const changed = JSON.stringify(values) !== JSON.stringify(current)

  const setValue = (key, value) => {
    setValues((prev) => ({ ...prev, [key]: value }))
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      const payload = {
        standard_llm_provider: values.standardProvider || null,
        standard_llm_model: values.standardModel || null,
        premium_llm_provider: values.premiumProvider || null,
        premium_llm_model: values.premiumModel || null,
      }
      await updatePlatformTenant(platformKey, tenant.id, payload)
      onUpdate(tenant.id, { llm_config: payload })
      setSaved(true)
    } catch { /* save error silently */ }
    finally { setSaving(false) }
  }

  return (
    <div className="space-y-4">
      <ModelRouteFields
        label="Standard"
        provider={values.standardProvider}
        model={values.standardModel}
        providers={providers}
        modelListId={`tenant-standard-models-${tenant.id}`}
        onProviderChange={(value) => { setValue('standardProvider', value); setValue('standardModel', '') }}
        onModelChange={(value) => setValue('standardModel', value)}
      />
      <ModelRouteFields
        label="Premium"
        provider={values.premiumProvider}
        model={values.premiumModel}
        providers={providers}
        modelListId={`tenant-premium-models-${tenant.id}`}
        onProviderChange={(value) => { setValue('premiumProvider', value); setValue('premiumModel', '') }}
        onModelChange={(value) => setValue('premiumModel', value)}
      />
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
        <p className="text-xs text-brand-muted font-sans">Blank fields inherit platform defaults.</p>
      </div>
    </div>
  )
}

function PlatformLLMConfigPanel({ platformKey, providers, config, onSaved }) {
  const current = {
    standardProvider: config?.standard_provider || '',
    standardModel: config?.standard_model || '',
    premiumProvider: config?.premium_provider || '',
    premiumModel: config?.premium_model || '',
  }
  const [values, setValues] = useState(current)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setValues(current)
    setSaved(false)
  }, [config?.standard_provider, config?.standard_model, config?.premium_provider, config?.premium_model])

  const changed = JSON.stringify(values) !== JSON.stringify(current)
  const setValue = (key, value) => {
    setValues((prev) => ({ ...prev, [key]: value }))
    setSaved(false)
  }

  const save = async () => {
    setSaving(true)
    setSaved(false)
    try {
      const resp = await updatePlatformLLMConfig(platformKey, {
        standard_provider: values.standardProvider || null,
        standard_model: values.standardModel || null,
        premium_provider: values.premiumProvider || null,
        premium_model: values.premiumModel || null,
      })
      onSaved(resp.config)
      setSaved(true)
    } catch { /* silent */ }
    finally { setSaving(false) }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden mb-8">
      <div className="px-5 py-4 border-b border-brand-line flex items-center justify-between">
        <div>
          <h2 className="font-serif font-bold text-brand-ink">Global AI Routing</h2>
          <p className="text-xs text-brand-muted font-sans mt-1">Used for every tenant unless a tenant override is set.</p>
        </div>
        <button
          onClick={save}
          disabled={saving || !changed}
          className={`px-4 py-2 rounded-lg text-xs font-medium font-sans border transition-colors ${
            saved
              ? 'bg-brand-accent/10 border-brand-accent/20 text-brand-accent'
              : 'bg-brand-ink text-white border-brand-ink hover:bg-brand-ink-2 disabled:opacity-40'
          }`}
        >
          {saved ? 'Saved' : saving ? 'Saving...' : 'Save Routes'}
        </button>
      </div>
      <div className="p-5 space-y-4">
        <ModelRouteFields
          label="Standard"
          provider={values.standardProvider}
          model={values.standardModel}
          providers={providers}
          modelListId="global-standard-models"
          defaultLabel="Env fallback"
          onProviderChange={(value) => { setValue('standardProvider', value); setValue('standardModel', '') }}
          onModelChange={(value) => setValue('standardModel', value)}
        />
        <ModelRouteFields
          label="Premium"
          provider={values.premiumProvider}
          model={values.premiumModel}
          providers={providers}
          modelListId="global-premium-models"
          defaultLabel="Env fallback"
          onProviderChange={(value) => { setValue('premiumProvider', value); setValue('premiumModel', '') }}
          onModelChange={(value) => setValue('premiumModel', value)}
        />
      </div>
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

function RouteCard({ label, route, allKeys, presets, platformKey, onChange }) {
  const [fetchingModels, setFetchingModels] = useState(false)
  const [models, setModels] = useState([])
  const [modelsError, setModelsError] = useState(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)

  const selectedKey = allKeys.find((k) => k.id === route.key_id)
  const selectedPreset = presets.find((p) => p.id === route.provider_id)
  const keysForPreset = route.provider_id ? allKeys.filter((k) => k.provider_id === route.provider_id) : allKeys

  const set = (field, value) => onChange({ ...route, [field]: value })

  const handleFetchModels = async () => {
    if (!route.key_id) return
    setFetchingModels(true)
    setModelsError(null)
    try {
      const data = await fetchProviderModels(platformKey, route.key_id)
      setModels(data.models || [])
    } catch (e) {
      setModelsError(e?.response?.data?.detail || 'Failed to fetch models')
    } finally { setFetchingModels(false) }
  }

  const handleTest = async () => {
    if (!route.key_id || !route.model || !route.provider_id) return
    setTesting(true)
    setTestResult(null)
    try {
      const data = await testLLMRoute(platformKey, { key_id: route.key_id, provider_id: route.provider_id, model: route.model, route: label.toLowerCase() })
      setTestResult(data)
    } catch (e) {
      setTestResult({ ok: false, error: e?.response?.data?.detail || 'Test failed' })
    } finally { setTesting(false) }
  }

  const addFallback = () => set('fallbacks', [...(route.fallbacks || []), { key_id: '', provider_id: '', model: '' }])
  const removeFallback = (i) => set('fallbacks', route.fallbacks.filter((_, idx) => idx !== i))
  const updateFallback = (i, field, value) => {
    const fbs = [...(route.fallbacks || [])]
    fbs[i] = { ...fbs[i], [field]: value }
    set('fallbacks', fbs)
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
      <div className="px-5 py-4 border-b border-brand-line flex items-center justify-between">
        <h3 className="font-serif font-bold text-brand-ink">{label} Route</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={handleTest}
            disabled={testing || !route.key_id || !route.model}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans font-medium border border-brand-line rounded-lg text-brand-ink hover:bg-brand-bg disabled:opacity-40 transition-colors"
          >
            {testing ? <RefreshCw size={12} className="animate-spin" /> : <Zap size={12} />}
            Test route
          </button>
        </div>
      </div>

      {testResult && (
        <div className={`px-5 py-3 text-xs font-sans border-b border-brand-line ${testResult.ok ? 'bg-brand-accent/5 text-brand-accent' : 'bg-brand-rose/5 text-brand-rose'}`}>
          {testResult.ok
            ? `OK — ${testResult.latency_ms}ms — model: ${testResult.model_used} — "${testResult.response_preview}"`
            : `Error — ${testResult.error}`}
        </div>
      )}

      <div className="p-5 space-y-4">
        {/* Primary provider + key + model */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-brand-muted font-sans mb-1">Provider</label>
            <select
              value={route.provider_id || ''}
              onChange={(e) => { set('provider_id', e.target.value); set('key_id', ''); set('model', '') }}
              className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
            >
              <option value="">Select provider…</option>
              {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            {selectedPreset && <p className="text-[11px] text-brand-muted mt-1">{selectedPreset.description}</p>}
          </div>
          <div>
            <label className="block text-xs text-brand-muted font-sans mb-1">API Key</label>
            <select
              value={route.key_id || ''}
              onChange={(e) => { set('key_id', e.target.value); set('model', '') }}
              className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
            >
              <option value="">Select key…</option>
              {keysForPreset.map((k) => <option key={k.id} value={k.id}>{k.name} (…{k.key_hint})</option>)}
            </select>
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="block text-xs text-brand-muted font-sans">Model</label>
              {selectedPreset?.models_url && (
                <button onClick={handleFetchModels} disabled={!route.key_id || fetchingModels} className="text-[11px] text-brand-accent hover:underline font-sans disabled:opacity-40">
                  {fetchingModels ? 'Fetching…' : 'Fetch models'}
                </button>
              )}
            </div>
            <input
              list={`models-${label}`}
              value={route.model || ''}
              onChange={(e) => set('model', e.target.value)}
              placeholder="Model ID or paste…"
              className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent bg-brand-surface"
            />
            <datalist id={`models-${label}`}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </datalist>
            {modelsError && <p className="text-[11px] text-brand-rose mt-1">{modelsError}</p>}
          </div>
        </div>

        {/* Fallback chain */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-bold text-brand-ink uppercase tracking-wider font-sans">Fallback chain</p>
            <button onClick={addFallback} className="flex items-center gap-1 text-xs text-brand-accent hover:underline font-sans">
              <Plus size={12} /> Add fallback
            </button>
          </div>
          {(route.fallbacks || []).length === 0 && (
            <p className="text-xs text-brand-muted font-sans">No fallbacks configured. Add one to create a fallback chain.</p>
          )}
          {(route.fallbacks || []).map((fb, i) => {
            const fbPreset = presets.find((p) => p.id === fb.provider_id)
            const fbKeys = fb.provider_id ? allKeys.filter((k) => k.provider_id === fb.provider_id) : allKeys
            return (
              <div key={i} className="grid grid-cols-1 md:grid-cols-[1fr_1fr_1fr_auto] gap-2 items-end mb-2 p-3 bg-brand-bg rounded-lg border border-brand-line">
                <div>
                  <label className="block text-[11px] text-brand-muted font-sans mb-1">Provider</label>
                  <select
                    value={fb.provider_id || ''}
                    onChange={(e) => { updateFallback(i, 'provider_id', e.target.value); updateFallback(i, 'key_id', ''); updateFallback(i, 'model', '') }}
                    className="w-full border border-brand-line rounded-lg px-2 py-1.5 text-xs font-sans focus:outline-none focus:ring-1 focus:ring-brand-accent bg-brand-surface"
                  >
                    <option value="">Provider…</option>
                    {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] text-brand-muted font-sans mb-1">Key</label>
                  <select
                    value={fb.key_id || ''}
                    onChange={(e) => updateFallback(i, 'key_id', e.target.value)}
                    className="w-full border border-brand-line rounded-lg px-2 py-1.5 text-xs font-sans focus:outline-none focus:ring-1 focus:ring-brand-accent bg-brand-surface"
                  >
                    <option value="">Key…</option>
                    {fbKeys.map((k) => <option key={k.id} value={k.id}>{k.name} (…{k.key_hint})</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] text-brand-muted font-sans mb-1">Model</label>
                  <input
                    value={fb.model || ''}
                    onChange={(e) => updateFallback(i, 'model', e.target.value)}
                    placeholder="Model ID…"
                    className="w-full border border-brand-line rounded-lg px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-brand-accent bg-brand-surface"
                  />
                </div>
                <button onClick={() => removeFallback(i)} className="p-1.5 text-brand-muted hover:text-brand-rose transition-colors">
                  <Trash2 size={14} />
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function KeyVaultPanel({ platformKey, keys, onKeysChange }) {
  const [presets, setPresets] = useState([])
  const [showAdd, setShowAdd] = useState(false)
  const [newName, setNewName] = useState('')
  const [newProvider, setNewProvider] = useState('')
  const [newKey, setNewKey] = useState('')
  const [adding, setAdding] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [addError, setAddError] = useState(null)
  const [syncResult, setSyncResult] = useState(null)

  useEffect(() => {
    getLLMProviderPresets(platformKey).then((d) => setPresets(d.providers || [])).catch(() => {})
  }, [platformKey])

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

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden mb-6">
      <div className="px-5 py-4 border-b border-brand-line flex items-center justify-between">
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
          <p className="px-5 py-6 text-sm text-brand-muted font-sans text-center">No keys stored. Add one or sync from environment variables.</p>
        )}
        {keys.map((k) => {
          const preset = presets.find((p) => p.id === k.provider_id)
          return (
            <div key={k.id} className="px-5 py-3 flex items-center justify-between hover:bg-brand-bg transition-colors">
              <div>
                <p className="text-sm font-medium text-brand-ink font-sans">{k.name}</p>
                <p className="text-xs text-brand-muted font-sans">{preset?.name || k.provider_id} · …{k.key_hint}</p>
              </div>
              <button onClick={() => handleDelete(k.id)} className="p-1.5 text-brand-muted hover:text-brand-rose transition-colors">
                <Trash2 size={14} />
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function AIRoutingTab({ platformKey }) {
  const [keys, setKeys] = useState([])
  const [presets, setPresets] = useState([])
  const [standard, setStandard] = useState({ key_id: '', provider_id: '', model: '', fallbacks: [] })
  const [premium, setPremium] = useState({ key_id: '', provider_id: '', model: '', fallbacks: [] })
  const [saving, setSaving] = useState(false)
  const [saveResult, setSaveResult] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [keysData, presetsData, routesData] = await Promise.all([
        getLLMProviderKeys(platformKey),
        getLLMProviderPresets(platformKey),
        getLLMRoutes(platformKey),
      ])
      setKeys(keysData.keys || [])
      setPresets(presetsData.providers || [])
      const std = routesData.standard || {}
      const prem = routesData.premium || {}
      setStandard({ key_id: std.key_id || '', provider_id: std.provider_id || '', model: std.model || '', fallbacks: std.fallbacks || [] })
      setPremium({ key_id: prem.key_id || '', provider_id: prem.provider_id || '', model: prem.model || '', fallbacks: prem.fallbacks || [] })
    } catch { /* silent */ }
    finally { setLoading(false) }
  }, [platformKey])

  useEffect(() => { load() }, [load])

  const handleSave = async () => {
    setSaving(true)
    setSaveResult(null)
    try {
      const data = await saveLLMRoutes(platformKey, { standard, premium })
      setSaveResult({ ok: true, litellm_updated: data.litellm_updated, models_registered: data.models_registered })
    } catch (e) {
      setSaveResult({ ok: false, error: e?.response?.data?.detail || 'Save failed' })
    } finally { setSaving(false) }
  }

  if (loading) {
    return <div className="flex justify-center py-16"><div className="w-8 h-8 border-2 border-brand-accent border-t-transparent rounded-full animate-spin" /></div>
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-serif font-bold text-brand-ink">AI Provider Routing</h2>
          <p className="text-sm text-brand-muted font-sans mt-1">Configure LLM providers, API keys, and model routes. Changes are pushed to LiteLLM automatically.</p>
        </div>
        <div className="flex items-center gap-3">
          {saveResult && (
            <span className={`text-xs font-sans ${saveResult.ok ? 'text-brand-accent' : 'text-brand-rose'}`}>
              {saveResult.ok
                ? `Saved — ${saveResult.litellm_updated ? `${saveResult.models_registered} model(s) registered` : 'saved to DB'}`
                : saveResult.error}
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-sm font-medium font-sans rounded-lg hover:bg-brand-ink-2 disabled:opacity-40 transition-colors"
          >
            {saving ? <RefreshCw size={14} className="animate-spin" /> : <CheckCircle size={14} />}
            {saving ? 'Saving…' : 'Save Routes'}
          </button>
        </div>
      </div>

      <KeyVaultPanel platformKey={platformKey} keys={keys} onKeysChange={setKeys} />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <RouteCard
          label="Standard"
          route={standard}
          allKeys={keys}
          presets={presets}
          platformKey={platformKey}
          onChange={setStandard}
        />
        <RouteCard
          label="Premium"
          route={premium}
          allKeys={keys}
          presets={presets}
          platformKey={platformKey}
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
  const [providers, setProviders] = useState([])
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
      const promises = [getPlatformTenants(platformKey, page), getPlatformUsage(platformKey), getPlatformLLMProviders(platformKey), getPlatformLLMConfig(platformKey)]
      if (tab === 'health') promises.push(getPlatformHealth(platformKey))
      const results = await Promise.all(promises)
      setTenants(results[0].tenants)
      setTotal(results[0].total)
      setLimit(results[0].limit || 50)
      setUsage(results[1])
      setProviders(results[2].providers)
      setLlmConfig(results[3].config)
      if (results[4]) setHealth(results[4])
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

            <PlatformLLMConfigPanel platformKey={platformKey} providers={providers} config={llmConfig} onSaved={setLlmConfig} />

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
        {tab === 'ai-routing' && <AIRoutingTab platformKey={platformKey} />}

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
