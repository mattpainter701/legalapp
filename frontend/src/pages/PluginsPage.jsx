import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getPlugins, updatePluginEntitlement } from '../api'
import { useAuth } from '../App'
import {
  Scale, Lock, Landmark, Building2, UserCircle, Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake
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

export default function PluginsPage() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [plugins, setPlugins] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [savingPlugin, setSavingPlugin] = useState(null)

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

  useEffect(() => {
    loadPlugins()
  }, [])

  const iconFor = (pluginId) => {
    return PLUGIN_ICONS[pluginId] || Scale
  }

  const handleEntitlement = async (event, pluginId, status) => {
    event.stopPropagation()
    setSavingPlugin(`${pluginId}:${status}`)
    setError(null)
    try {
      await updatePluginEntitlement(pluginId, { status, source: 'admin' })
      await loadPlugins()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to update plugin entitlement.')
    } finally {
      setSavingPlugin(null)
    }
  }

  const badgeConfig = (plugin) => {
    if (plugin.is_locked || plugin.entitlement_status === 'locked') {
      return ['Locked', 'bg-gray-100 text-gray-600 border-gray-200', 'bg-gray-500']
    }
    if (plugin.entitlement_status === 'disabled') {
      return ['Disabled', 'bg-gray-100 text-gray-600 border-gray-200', 'bg-gray-500']
    }
    if (plugin.entitlement_status === 'trial') {
      return ['Trial', 'bg-purple-100 text-purple-700 border-purple-200', 'bg-purple-500']
    }
    if (plugin.setup_status === 'complete' || plugin.profile_is_complete) {
      return ['Active', 'bg-green-100 text-green-700 border-green-200', 'bg-green-500']
    }
    if (plugin.is_purchased || plugin.entitlement_status === 'purchased') {
      return ['Setup Required', 'bg-blue-100 text-blue-700 border-blue-200', 'bg-blue-500']
    }
    return ['Available', 'bg-amber-100 text-amber-700 border-amber-200', 'bg-amber-500']
  }

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
          <span className="font-serif font-semibold text-lg text-brand-ink">Clarity Legal</span>
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

        {loading ? (
          <div className="flex justify-center py-20">
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {plugins.map((plugin) => {
              const pluginId = plugin.plugin_name || plugin.plugin_id || plugin.id
              const purchased = plugin.is_purchased || plugin.entitlement_status === 'purchased'
              const route = plugin.primary_route || `/plugins/${pluginId}`
              const Icon = iconFor(pluginId)
              const [badge, badgeCls, dotCls] = badgeConfig(plugin)
              return (
                <div
                  key={pluginId}
                  className="bg-brand-surface border border-brand-line rounded-2xl p-6 flex flex-col hover:shadow-md hover:border-brand-accent hover:-translate-y-1 transition-all duration-200 group cursor-pointer"
                  onClick={() => navigate(route)}
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
                      {/* Profile badge */}
                      <div>
                        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold font-sans uppercase tracking-wider border ${badgeCls}`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${dotCls}`} />
                          {badge}
                        </span>
                      </div>
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

                  {/* Open button */}
                  <div className="space-y-2">
                    <button
                      className="w-full py-2.5 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl group-hover:bg-brand-ink group-hover:text-white transition-colors"
                    >
                      {plugin.is_purchased || purchased ? 'Open Workspace' : 'View Add-on'}
                    </button>
                    {user?.role === 'admin' && (
                      <div className="grid grid-cols-2 gap-2">
                        {!plugin.is_purchased && plugin.entitlement_status !== 'trial' ? (
                          <>
                            <button onClick={(e) => handleEntitlement(e, pluginId, 'trial')} disabled={savingPlugin === `${pluginId}:trial`} className="px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-ink hover:bg-brand-bg">
                              Trial
                            </button>
                            <button onClick={(e) => handleEntitlement(e, pluginId, 'purchased')} disabled={savingPlugin === `${pluginId}:purchased`} className="px-3 py-2 text-[12px] font-sans font-semibold rounded-lg bg-brand-ink text-white hover:bg-brand-ink-2">
                              Purchase
                            </button>
                          </>
                        ) : (
                          <>
                            <button onClick={(e) => { e.stopPropagation(); navigate(`/plugins/${pluginId}`) }} className="px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-ink hover:bg-brand-bg">
                              Configure
                            </button>
                            <button onClick={(e) => handleEntitlement(e, pluginId, 'disabled')} disabled={savingPlugin === `${pluginId}:disabled`} className="px-3 py-2 text-[12px] font-sans font-semibold rounded-lg border border-brand-line text-brand-muted hover:text-brand-ink hover:bg-brand-bg">
                              Disable
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
