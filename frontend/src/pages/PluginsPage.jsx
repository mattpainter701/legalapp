import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getPlugins } from '../api'
import {
  Scale, Lock, Landmark, Building2, UserCircle, Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake
} from 'lucide-react'

const PLUGIN_CONFIG = [
  {
    id: 'commercial-legal',
    icon: Scale,
    name: 'Commercial Legal',
    description: 'Contract review, NDA triage, SaaS agreement analysis, renewal tracking',
  },
  {
    id: 'privacy-legal',
    icon: Lock,
    name: 'Privacy Legal',
    description: 'DPA review, DSAR responses, Privacy Impact Assessments',
  },
  {
    id: 'litigation-legal',
    icon: Landmark,
    name: 'Litigation Legal',
    description: 'Matter intake, portfolio management, demand letters, claim charts',
  },
  {
    id: 'corporate-legal',
    icon: Building2,
    name: 'Corporate Legal',
    description: 'M&A diligence, closing checklists, entity compliance',
  },
  {
    id: 'employment-legal',
    icon: UserCircle,
    name: 'Employment Legal',
    description: 'Hire/termination review, worker classification, leave tracking',
  },
  {
    id: 'product-legal',
    icon: Rocket,
    name: 'Product Legal',
    description: 'Launch reviews, marketing claims check, regulatory triage',
  },
  {
    id: 'ip-legal',
    icon: Lightbulb,
    name: 'IP Legal',
    description: 'Trademark clearance, freedom-to-operate, C&D letters',
  },
  {
    id: 'ai-governance-legal',
    icon: Bot,
    name: 'AI Governance',
    description: 'AI use case triage, impact assessments, vendor AI review',
  },
  {
    id: 'regulatory-legal',
    icon: ClipboardList,
    name: 'Regulatory Legal',
    description: 'Regulatory monitoring, policy gap analysis, NPRM comments',
  },
  {
    id: 'trust-estate-legal',
    icon: Vault,
    name: 'Trust & Estate',
    description: 'Will & trust review, estate tax analysis, probate, estate portfolio',
  },
  {
    id: 'mediation-legal',
    icon: Handshake,
    name: 'Mediation',
    description: 'Mediation intake, briefs, settlement drafting, case tracking',
  },
]

export default function PluginsPage() {
  const navigate = useNavigate()
  const [pluginData, setPluginData] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getPlugins()
      .then((data) => {
        const map = {}
        if (Array.isArray(data)) {
          data.forEach((p) => {
            map[p.plugin_id || p.id] = p
          })
        } else if (data && typeof data === 'object') {
          Object.assign(map, data)
        }
        setPluginData(map)
      })
      .catch((err) => {
        setError('Failed to load plugins.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [])

  const hasProfile = (pluginId) => {
    const p = pluginData[pluginId]
    if (!p) return false
    return !!(p.has_profile || p.profile || p.profile_complete)
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
            {PLUGIN_CONFIG.map((plugin) => {
              const active = hasProfile(plugin.id)
              const Icon = plugin.icon
              return (
                <div
                  key={plugin.id}
                  className="bg-brand-surface border border-brand-line rounded-2xl p-6 flex flex-col hover:shadow-md hover:border-brand-accent hover:-translate-y-1 transition-all duration-200 group cursor-pointer"
                  onClick={() => navigate(`/plugins/${plugin.id}`)}
                >
                  {/* Icon + name */}
                  <div className="flex items-start gap-4 mb-4">
                    <div className="w-12 h-12 rounded-xl bg-brand-bg border border-brand-line flex items-center justify-center text-brand-ink group-hover:bg-brand-ink group-hover:text-brand-surface transition-colors duration-200 shrink-0">
                      <Icon size={24} strokeWidth={1.5} />
                    </div>
                    <div className="flex-1 min-w-0 pt-1">
                      <h3 className="font-serif font-bold text-brand-ink text-lg leading-tight mb-2">
                        {plugin.name}
                      </h3>
                      {/* Profile badge */}
                      <div>
                        {active ? (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold font-sans uppercase tracking-wider bg-green-100 text-green-700 border border-green-200">
                            <span className="w-1.5 h-1.5 bg-green-500 rounded-full" />
                            Active
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold font-sans uppercase tracking-wider bg-amber-100 text-amber-700 border border-amber-200">
                            <span className="w-1.5 h-1.5 bg-amber-500 rounded-full" />
                            Setup Required
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Description */}
                  <p className="text-brand-muted text-sm font-sans leading-relaxed flex-1 mb-6">
                    {plugin.description}
                  </p>

                  {/* Open button */}
                  <button
                    className="w-full py-2.5 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl group-hover:bg-brand-ink group-hover:text-white transition-colors"
                  >
                    Open Workspace
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
