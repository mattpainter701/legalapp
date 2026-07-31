import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { getPlugins, updatePluginEntitlement } from '../api'
import { useAuth } from '../App'
import {
  Scale, Lock, Landmark, Building2, UserCircle, Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake,
  ShoppingCart, Sparkles, CircleAlert, Ban, Settings2
} from 'lucide-react'

const PLUGIN_ICONS = {
  'commercial-legal': Scale,
  'privacy-legal': Lock,
  'litigation-legal': Landmark,
  'corporate-legal': Building2,
  'employment-legal': UserCircle,
  'product-legal': Rocket,
  'ip-legal': Lightbulb,
  'ai-governance-legal': Bot,
  'regulatory-legal': ClipboardList,
  'trust-estate-legal': Vault,
  'mediation-legal': Handshake,
}

// ── State tab definitions ────────────────────────────────────────────────────
const STATE_TABS = [
  { key: 'purchased', label: 'Purchased', icon: ShoppingCart, filter: (p) => p.is_purchased && !p.is_locked && p.entitlement_status !== 'trial' },
  { key: 'trials', label: 'Trials', icon: Sparkles, filter: (p) => p.entitlement_status === 'trial' },
  { key: 'setup-required', label: 'Setup Required', icon: CircleAlert, filter: (p) => (p.is_purchased || p.entitlement_status === 'purchased') && p.setup_status !== 'complete' && !p.profile_is_complete },
  { key: 'available', label: 'Available', icon: ShoppingCart, filter: (p) => !p.is_purchased && !p.is_locked && p.entitlement_status !== 'trial' && !p.is_trial },
  { key: 'locked', label: 'Locked', icon: Ban, filter: (p) => p.is_locked || p.entitlement_status === 'locked' || p.entitlement_status === 'disabled' },
]

function stateFor(plugin) {
  if (plugin.is_locked || plugin.entitlement_status === 'locked' || plugin.entitlement_status === 'disabled') return 'locked'
  if (plugin.entitlement_status === 'trial') return 'trials'
  if ((plugin.is_purchased || plugin.entitlement_status === 'purchased') && plugin.setup_status !== 'complete' && !plugin.profile_is_complete) return 'setup-required'
  if (plugin.is_purchased || plugin.entitlement_status === 'purchased' || plugin.setup_status === 'complete' || plugin.profile_is_complete) return 'purchased'
  return 'available'
}

const STATE_META = {
  purchased:  { badge: 'Active',   badgeCls: 'bg-green-100 text-green-700 border-green-200',   dotCls: 'bg-green-500',  emptyTitle: 'No Purchased Add-ons',    emptyDesc: 'Purchase an add-on from the Available tab to get started.' },
  trials:     { badge: 'Trial',    badgeCls: 'bg-purple-100 text-purple-700 border-purple-200', dotCls: 'bg-purple-500', emptyTitle: 'No Active Trials',        emptyDesc: 'Start a trial from the Available tab to evaluate an add-on.' },
  'setup-required': { badge: 'Setup Required', badgeCls: 'bg-blue-100 text-blue-700 border-blue-200', dotCls: 'bg-blue-500', emptyTitle: 'All Set Up', emptyDesc: 'All purchased add-ons have been configured.' },
  available: { badge: 'Available', badgeCls: 'bg-amber-100 text-amber-700 border-amber-200', dotCls: 'bg-amber-500', emptyTitle: 'All Add-ons Purchased',    emptyDesc: 'Every available add-on is already active or on trial.' },
  locked:    { badge: 'Locked',    badgeCls: 'bg-gray-100 text-gray-600 border-gray-200',     dotCls: 'bg-gray-500',  emptyTitle: 'No Locked Add-ons',        emptyDesc: 'No add-ons have been locked or disabled.' },
}

// ── Plugin card ──────────────────────────────────────────────────────────────
export function PluginCard({ plugin, isAdmin, saving, onEntitlement, onNavigate }) {
  const pluginId = plugin.plugin_name || plugin.plugin_id || plugin.id
  const Icon = PLUGIN_ICONS[pluginId] || Settings2
  const state = stateFor(plugin)
  const meta = STATE_META[state]
  const openPlugin = () => onNavigate(plugin.primary_route || `/plugins/${pluginId}`)

  return (
    <div
      className="bg-brand-surface border border-brand-line rounded-2xl p-6 flex flex-col hover:shadow-md hover:border-brand-accent hover:-translate-y-1 transition-all duration-200 group"
    >
      {/* Icon + name */}
      <div className="flex items-start gap-4 mb-4">
        <div className="w-12 h-12 rounded-xl bg-brand-bg border border-brand-line flex items-center justify-center text-brand-ink group-hover:bg-brand-ink group-hover:text-brand-surface transition-colors duration-200 shrink-0">
          <Icon size={24} strokeWidth={1.5} />
        </div>
        <div className="flex-1 min-w-0 pt-1">
          <h3 className="font-serif font-bold text-brand-ink text-lg leading-tight mb-2">
            {plugin.display_name || plugin.name || pluginId}
          </h3>
          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold font-sans uppercase tracking-wider border ${meta.badgeCls}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${meta.dotCls}`} />
            {meta.badge}
          </span>
        </div>
      </div>

      {/* Description */}
      <p className="text-brand-muted text-sm font-sans leading-relaxed flex-1 mb-6">
        {plugin.description}
      </p>

      <div className="mb-5 space-y-2 text-[12px] font-sans text-brand-muted">
        <div>Category: <span className="text-brand-ink font-medium">{plugin.category}</span></div>
        <div>Integrations: <span className="text-brand-ink font-medium">{(plugin.available_integrations || []).join(', ') || 'none connected'}</span></div>
      </div>

      {/* Actions */}
      <div className="space-y-2">
        <button
          type="button"
          onClick={openPlugin}
          className="inline-flex min-h-[44px] min-w-[44px] w-full items-center justify-center py-2.5 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl group-hover:bg-brand-ink group-hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
        >
          {plugin.is_purchased || state === 'purchased' ? 'Open Workspace' : 'View Add-on'}
        </button>
        {isAdmin && (
          <div className="grid grid-cols-2 gap-2">
            {!plugin.is_purchased && plugin.entitlement_status !== 'trial' ? (
              <>
                <button
                  type="button"
                  onClick={() => onEntitlement(pluginId, 'trial')}
                  disabled={saving === `${pluginId}:trial`}
                  className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-ink hover:bg-brand-bg disabled:opacity-50"
                >Trial</button>
                <button
                  type="button"
                  onClick={() => onEntitlement(pluginId, 'purchased')}
                  disabled={saving === `${pluginId}:purchased`}
                  className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center px-3 py-2 text-[12px] font-sans font-semibold rounded-lg bg-brand-ink text-white hover:bg-brand-ink-2 disabled:opacity-50"
                >Purchase</button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => onNavigate(`/plugins/${pluginId}`)}
                  className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-ink hover:bg-brand-bg"
                >Configure</button>
                <button
                  type="button"
                  onClick={() => onEntitlement(pluginId, 'disabled')}
                  disabled={saving === `${pluginId}:disabled`}
                  className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-muted hover:text-brand-ink hover:bg-brand-bg disabled:opacity-50"
                >Disable</button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page component ───────────────────────────────────────────────────────────
export default function PluginsPage() {
  const navigate = useNavigate()
  const { user, refreshUser } = useAuth()
  const [plugins, setPlugins] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [savingPlugin, setSavingPlugin] = useState(null)
  const [activeTab, setActiveTab] = useState('purchased')
  const [notice, setNotice] = useState(null)

  const loadPlugins = () => {
    setLoading(true)
    return getPlugins()
      .then((data) => {
        const list = Array.isArray(data) ? data : data?.plugins || []
        setPlugins(list)
      })
      .catch((err) => {
        setError('Failed to load plugins.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadPlugins() }, [])

  const handleEntitlement = async (pluginId, status) => {
    setSavingPlugin(`${pluginId}:${status}`)
    setError(null)
    try {
      await updatePluginEntitlement(pluginId, { status, source: 'admin' })
      await refreshUser?.()
      await loadPlugins()
      setNotice(`${status === 'disabled' ? 'Disabled' : status === 'trial' ? 'Trial started for' : 'Purchased'} ${pluginId}.`)
      setTimeout(() => setNotice(null), 4000)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to update plugin entitlement.')
    } finally {
      setSavingPlugin(null)
    }
  }

  // Compute per-tab counts and auto-select first non-empty tab
  const tabCounts = useMemo(() => {
    const counts = {}
    for (const tab of STATE_TABS) {
      counts[tab.key] = plugins.filter(tab.filter).length
    }
    return counts
  }, [plugins])

  // Auto-select first non-empty tab on load if current tab is empty
  useEffect(() => {
    if (!loading && tabCounts[activeTab] === 0) {
      const first = STATE_TABS.find((t) => tabCounts[t.key] > 0)
      if (first) setActiveTab(first.key)
    }
  }, [loading, tabCounts, activeTab])

  const filteredPlugins = useMemo(
    () => plugins.filter(STATE_TABS.find((t) => t.key === activeTab)?.filter || (() => true)),
    [plugins, activeTab]
  )

  const isAdmin = user?.role === 'admin'

  return (
    <div className="min-h-screen bg-brand-bg">
      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-6 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/chat')}
            className="flex items-center gap-2 text-brand-muted hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Back to Chat
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-semibold text-lg text-brand-ink">WellPled</span>
        </div>
      </div>

      {/* Page header */}
      <div className="max-w-6xl mx-auto px-6 py-12">
        <div className="mb-10 text-center">
          <h1 className="font-serif text-4xl font-bold text-brand-ink mb-4">
            Add-on Modules
          </h1>
          <p className="text-brand-muted font-sans text-lg max-w-2xl mx-auto">
            Specialized workspaces that extend your legal-safe coworker — each with attorney-reviewed outputs tailored to a practice area. Activate the add-ons your firm needs.
          </p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-8 text-red-700 text-sm font-sans text-center">
            {error}
          </div>
        )}
        {notice && (
          <div className="bg-green-50 border border-green-200 rounded-xl px-4 py-3 mb-8 text-green-700 text-sm font-sans text-center">
            {notice}
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-20">
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <>
            {/* State tabs */}
            <div className="flex flex-wrap gap-2 mb-10 justify-center">
              {STATE_TABS.map((tab) => {
                const TabIcon = tab.icon
                const count = tabCounts[tab.key]
                const isActive = activeTab === tab.key
                return (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-sans font-semibold border transition-all ${
                      isActive
                        ? 'bg-brand-ink text-white border-brand-ink shadow-sm'
                        : 'bg-brand-surface text-brand-muted border-brand-line hover:text-brand-ink hover:border-brand-ink'
                    }`}
                  >
                    <TabIcon size={16} strokeWidth={1.5} />
                    {tab.label}
                    <span className={`inline-flex items-center justify-center min-w-[22px] h-[22px] px-1.5 rounded-full text-[11px] font-bold ${
                      isActive ? 'bg-white/20 text-white' : 'bg-brand-bg text-brand-muted'
                    }`}>
                      {count}
                    </span>
                  </button>
                )
              })}
            </div>

            {/* Plugin grid */}
            {filteredPlugins.length === 0 ? (
              <div className="text-center py-16">
                <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-brand-surface border border-brand-line flex items-center justify-center text-brand-muted">
                  {(() => {
                    const tabDef = STATE_TABS.find((t) => t.key === activeTab)
                    const TabIcon = tabDef?.icon || Settings2
                    return <TabIcon size={32} strokeWidth={1.5} />
                  })()}
                </div>
                <h3 className="font-serif text-xl font-semibold text-brand-ink mb-2">
                  {STATE_META[activeTab]?.emptyTitle || 'Nothing here'}
                </h3>
                <p className="text-brand-muted text-sm font-sans max-w-sm mx-auto">
                  {STATE_META[activeTab]?.emptyDesc || ''}
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {filteredPlugins.map((plugin) => (
                  <PluginCard
                    key={plugin.plugin_name || plugin.plugin_id || plugin.id}
                    plugin={plugin}
                    isAdmin={isAdmin}
                    saving={savingPlugin}
                    onEntitlement={handleEntitlement}
                    onNavigate={navigate}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
