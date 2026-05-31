import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getPlugins } from '../api'

const PLUGIN_CONFIG = [
  {
    id: 'commercial-legal',
    icon: '⚖️',
    name: 'Commercial Legal',
    description: 'Contract review, NDA triage, SaaS agreement analysis, renewal tracking',
  },
  {
    id: 'privacy-legal',
    icon: '🔒',
    name: 'Privacy Legal',
    description: 'DPA review, DSAR responses, Privacy Impact Assessments',
  },
  {
    id: 'litigation-legal',
    icon: '🏛️',
    name: 'Litigation Legal',
    description: 'Matter intake, portfolio management, demand letters, claim charts',
  },
  {
    id: 'corporate-legal',
    icon: '🏢',
    name: 'Corporate Legal',
    description: 'M&A diligence, closing checklists, entity compliance',
  },
  {
    id: 'employment-legal',
    icon: '👔',
    name: 'Employment Legal',
    description: 'Hire/termination review, worker classification, leave tracking',
  },
  {
    id: 'product-legal',
    icon: '🚀',
    name: 'Product Legal',
    description: 'Launch reviews, marketing claims check, regulatory triage',
  },
  {
    id: 'ip-legal',
    icon: '💡',
    name: 'IP Legal',
    description: 'Trademark clearance, freedom-to-operate, C&D letters',
  },
  {
    id: 'ai-governance-legal',
    icon: '🤖',
    name: 'AI Governance',
    description: 'AI use case triage, impact assessments, vendor AI review',
  },
  {
    id: 'regulatory-legal',
    icon: '📋',
    name: 'Regulatory Legal',
    description: 'Regulatory monitoring, policy gap analysis, NPRM comments',
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
        // data may be an array of plugin objects or a map
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
    <div className="min-h-screen bg-gray-50">
      {/* Top nav */}
      <div className="bg-[#1e3a5f] text-white px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/chat')}
            className="flex items-center gap-1.5 text-blue-200 hover:text-white transition-colors text-sm font-sans"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Back to Chat
          </button>
          <span className="text-blue-300">|</span>
          <span className="font-serif font-semibold text-lg">Clarity Legal</span>
        </div>
      </div>

      {/* Page header */}
      <div className="max-w-6xl mx-auto px-6 py-10">
        <div className="mb-8">
          <h1 className="font-serif text-3xl font-bold text-[#1e3a5f] mb-2">
            Legal Practice Plugins
          </h1>
          <p className="text-gray-500 font-sans text-base">
            AI-assisted legal workflows with attorney-reviewed outputs
          </p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-6 text-red-700 text-sm font-sans">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-20">
            <div className="w-8 h-8 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {PLUGIN_CONFIG.map((plugin) => {
              const active = hasProfile(plugin.id)
              return (
                <div
                  key={plugin.id}
                  className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col hover:shadow-md transition-shadow"
                >
                  {/* Icon + name */}
                  <div className="flex items-start gap-3 mb-3">
                    <span className="text-3xl leading-none">{plugin.icon}</span>
                    <div className="flex-1 min-w-0">
                      <h3 className="font-serif font-semibold text-[#1e3a5f] text-base leading-tight">
                        {plugin.name}
                      </h3>
                      {/* Profile badge */}
                      <div className="mt-1">
                        {active ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium font-sans bg-green-100 text-green-800">
                            <span className="w-1.5 h-1.5 bg-green-500 rounded-full" />
                            Profile: Active
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium font-sans bg-amber-100 text-amber-800">
                            <span className="w-1.5 h-1.5 bg-amber-500 rounded-full" />
                            Profile: Setup Required
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Description */}
                  <p className="text-gray-500 text-sm font-sans leading-relaxed flex-1 mb-4">
                    {plugin.description}
                  </p>

                  {/* Open button */}
                  <button
                    onClick={() => navigate(`/plugins/${plugin.id}`)}
                    className="w-full py-2 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-lg hover:bg-[#2e4f7a] transition-colors"
                  >
                    Open
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
