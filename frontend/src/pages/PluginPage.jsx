import React, { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getPluginProfile, executeSkill } from '../api'
import ColdStartInterview from '../components/ColdStartInterview'
import SkillOutput from '../components/SkillOutput'

// ── Plugin metadata ──────────────────────────────────────────────────────────

const PLUGIN_META = {
  'commercial-legal': {
    icon: '⚖️',
    name: 'Commercial Legal',
    skills: [
      { id: 'contract-review', label: 'Contract Review', description: 'Analyze a contract for key risks, obligations, and recommended edits.' },
      { id: 'nda-triage', label: 'NDA Triage', description: 'Quickly triage an NDA for one-sided provisions and risk factors.' },
      { id: 'saas-review', label: 'SaaS Agreement Review', description: 'Review a SaaS agreement for liability, IP, and data terms.' },
      { id: 'renewal-analysis', label: 'Renewal Analysis', description: 'Analyze contract renewal terms and auto-renewal risk.' },
    ],
    extraLinks: [{ label: 'Renewal Tracker', path: '/plugins/commercial/renewals' }],
  },
  'privacy-legal': {
    icon: '🔒',
    name: 'Privacy Legal',
    skills: [
      { id: 'dpa-review', label: 'DPA Review', description: 'Review a Data Processing Agreement for GDPR/CCPA compliance gaps.' },
      { id: 'dsar-response', label: 'DSAR Response', description: 'Draft a Data Subject Access Request response letter.' },
      { id: 'pia-review', label: 'Privacy Impact Assessment', description: 'Conduct a Privacy Impact Assessment for a new product or process.' },
    ],
    extraLinks: [],
  },
  'litigation-legal': {
    icon: '🏛️',
    name: 'Litigation Legal',
    skills: [
      { id: 'matter-intake', label: 'Matter Intake', description: 'Intake a new litigation matter with risk scoring and conflict check.' },
      { id: 'demand-letter', label: 'Demand Letter', description: 'Draft a demand letter for a litigation dispute.' },
      { id: 'claim-chart', label: 'Claim Chart', description: 'Generate a patent claim chart for infringement or invalidity analysis.' },
      { id: 'case-summary', label: 'Case Summary', description: 'Summarize a case or legal filing for internal use.' },
    ],
    extraLinks: [{ label: 'Matter Portfolio', path: '/plugins/litigation/matters' }],
  },
  'corporate-legal': {
    icon: '🏢',
    name: 'Corporate Legal',
    skills: [
      { id: 'ma-diligence', label: 'M&A Diligence', description: 'Generate an M&A diligence checklist and risk summary.' },
      { id: 'closing-checklist', label: 'Closing Checklist', description: 'Create a closing checklist for a corporate transaction.' },
      { id: 'entity-compliance', label: 'Entity Compliance', description: 'Review entity compliance status and flag deficiencies.' },
    ],
    extraLinks: [],
  },
  'employment-legal': {
    icon: '👔',
    name: 'Employment Legal',
    skills: [
      { id: 'hire-review', label: 'Hire Review', description: 'Review a new hire package for legal compliance risks.' },
      { id: 'termination-review', label: 'Termination Review', description: 'Review a termination for legal risk and recommended steps.' },
      { id: 'worker-classification', label: 'Worker Classification', description: 'Analyze worker classification (employee vs. contractor).' },
      { id: 'leave-analysis', label: 'Leave Analysis', description: 'Review leave policy or leave request for legal compliance.' },
    ],
    extraLinks: [],
  },
  'product-legal': {
    icon: '🚀',
    name: 'Product Legal',
    skills: [
      { id: 'launch-review', label: 'Launch Review', description: 'Review a product launch for legal and regulatory risks.' },
      { id: 'marketing-claims', label: 'Marketing Claims Check', description: 'Check marketing claims for false advertising risk.' },
      { id: 'regulatory-triage', label: 'Regulatory Triage', description: 'Triage a product feature for regulatory classification.' },
    ],
    extraLinks: [],
  },
  'ip-legal': {
    icon: '💡',
    name: 'IP Legal',
    skills: [
      { id: 'trademark-clearance', label: 'Trademark Clearance', description: 'Assess trademark clearance risk for a brand name or mark.' },
      { id: 'fto-analysis', label: 'Freedom-to-Operate', description: 'Analyze freedom-to-operate risk for a product or technology.' },
      { id: 'cd-letter', label: 'C&D Letter', description: 'Draft a cease and desist letter for IP infringement.' },
    ],
    extraLinks: [],
  },
  'ai-governance-legal': {
    icon: '🤖',
    name: 'AI Governance',
    skills: [
      { id: 'ai-use-case-triage', label: 'AI Use Case Triage', description: 'Triage an AI use case for legal and regulatory risk.' },
      { id: 'ai-impact-assessment', label: 'AI Impact Assessment', description: 'Conduct an AI impact assessment for a proposed system.' },
      { id: 'vendor-ai-review', label: 'Vendor AI Review', description: 'Review vendor AI terms for risk and compliance.' },
    ],
    extraLinks: [],
  },
  'regulatory-legal': {
    icon: '📋',
    name: 'Regulatory Legal',
    skills: [
      { id: 'regulatory-monitoring', label: 'Regulatory Monitoring', description: 'Summarize recent regulatory developments in a given area.' },
      { id: 'policy-gap-analysis', label: 'Policy Gap Analysis', description: 'Identify gaps between current policies and regulatory requirements.' },
      { id: 'nprm-comment', label: 'NPRM Comment', description: 'Draft a public comment on a Notice of Proposed Rulemaking.' },
    ],
    extraLinks: [],
  },
}

// ── Extra fields per skill ────────────────────────────────────────────────────

function ExtraFields({ skillId, values, onChange }) {
  if (skillId === 'claim-chart') {
    return (
      <div>
        <label className="block text-xs font-medium text-gray-700 font-sans mb-1">Chart Mode</label>
        <select
          value={values.chart_mode || 'infringement'}
          onChange={(e) => onChange({ ...values, chart_mode: e.target.value })}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
        >
          <option value="infringement">Infringement</option>
          <option value="invalidity">Invalidity</option>
          <option value="civil-elements">Civil Elements</option>
        </select>
      </div>
    )
  }

  if (skillId === 'matter-intake') {
    return (
      <div>
        <label className="block text-xs font-medium text-gray-700 font-sans mb-1">Conflicts Status</label>
        <select
          value={values.conflicts_status || 'not_run'}
          onChange={(e) => onChange({ ...values, conflicts_status: e.target.value })}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
        >
          <option value="not_run">Not Run</option>
          <option value="cleared">Cleared</option>
          <option value="conflict_identified">Conflict Identified</option>
          <option value="waived">Waived</option>
        </select>
      </div>
    )
  }

  if (skillId === 'dsar-response') {
    return (
      <div>
        <label className="block text-xs font-medium text-gray-700 font-sans mb-1">Jurisdiction</label>
        <select
          value={values.jurisdiction || 'GDPR'}
          onChange={(e) => onChange({ ...values, jurisdiction: e.target.value })}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
        >
          <option value="GDPR">GDPR (EU)</option>
          <option value="CCPA">CCPA (California)</option>
          <option value="PIPEDA">PIPEDA (Canada)</option>
          <option value="UK_GDPR">UK GDPR</option>
          <option value="other">Other</option>
        </select>
      </div>
    )
  }

  if (skillId === 'termination-review') {
    return (
      <div>
        <label className="block text-xs font-medium text-gray-700 font-sans mb-1">Jurisdiction</label>
        <input
          type="text"
          value={values.jurisdiction || ''}
          onChange={(e) => onChange({ ...values, jurisdiction: e.target.value })}
          placeholder="e.g. California, New York, Federal"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400"
        />
      </div>
    )
  }

  return null
}

// ── Main component ────────────────────────────────────────────────────────────

export default function PluginPage() {
  const { pluginName } = useParams()
  const navigate = useNavigate()

  const meta = PLUGIN_META[pluginName] || {
    icon: '⚙️',
    name: pluginName,
    skills: [],
    extraLinks: [],
  }

  const [profile, setProfile] = useState(null)
  const [profileLoading, setProfileLoading] = useState(true)
  const [showColdStart, setShowColdStart] = useState(false)
  const [selectedSkill, setSelectedSkill] = useState(meta.skills[0] || null)
  const [inputText, setInputText] = useState('')
  const [extraFields, setExtraFields] = useState({})
  const [showExtra, setShowExtra] = useState(false)
  const [usePremium, setUsePremium] = useState(false)
  const [output, setOutput] = useState(null)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState(null)
  const fileInputRef = useRef(null)

  useEffect(() => {
    setProfileLoading(true)
    getPluginProfile(pluginName)
      .then((data) => setProfile(data))
      .catch(() => setProfile(null))
      .finally(() => setProfileLoading(false))
  }, [pluginName])

  const handleFileUpload = (e) => {
    const file = e.target.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      setInputText(ev.target.result || '')
    }
    reader.readAsText(file)
    // Reset file input
    e.target.value = ''
  }

  const handleRunSkill = async () => {
    if (!selectedSkill || !inputText.trim() || running) return
    setRunning(true)
    setRunError(null)
    setOutput(null)
    try {
      const payload = {
        text: inputText,
        use_premium_llm: usePremium,
        ...extraFields,
      }
      const result = await executeSkill(pluginName, selectedSkill.id, payload)
      setOutput(result)
    } catch (err) {
      setRunError(
        err?.response?.data?.detail || err?.message || 'Failed to run skill. Please try again.'
      )
    } finally {
      setRunning(false)
    }
  }

  const handleContinueInChat = () => {
    if (!output?.memo) return
    // Store in session for ChatPage to pick up
    sessionStorage.setItem(
      'pending_chat_message',
      `[From ${meta.name} — ${selectedSkill?.label}]\n\n${output.memo}`
    )
    navigate('/chat')
  }

  const hasExtraFields = (skillId) => {
    return ['claim-chart', 'matter-intake', 'dsar-response', 'termination-review'].includes(skillId)
  }

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
      {/* Cold Start Modal */}
      {showColdStart && (
        <ColdStartInterview
          plugin={pluginName}
          onClose={() => setShowColdStart(false)}
          onProfileSaved={(p) => {
            setProfile(p)
            setShowColdStart(false)
          }}
        />
      )}

      {/* Left sidebar */}
      <div className="w-56 bg-[#1e3a5f] flex flex-col flex-shrink-0 overflow-y-auto">
        {/* Back link */}
        <div className="px-4 pt-4 pb-2">
          <button
            onClick={() => navigate('/plugins')}
            className="flex items-center gap-1.5 text-blue-300 hover:text-white transition-colors text-xs font-sans"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            All Plugins
          </button>
        </div>

        {/* Plugin header */}
        <div className="px-4 py-3 border-b border-blue-800">
          <div className="flex items-center gap-2">
            <span className="text-xl">{meta.icon}</span>
            <span className="text-white font-serif font-semibold text-sm leading-tight">
              {meta.name}
            </span>
          </div>

          {/* Profile status */}
          <div className="mt-2">
            {profileLoading ? (
              <div className="h-4 bg-blue-700 rounded animate-pulse w-24" />
            ) : profile ? (
              <span className="inline-flex items-center gap-1 text-xs text-green-300 font-sans">
                <span className="w-1.5 h-1.5 bg-green-400 rounded-full" />
                Profile Active
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs text-amber-300 font-sans">
                <span className="w-1.5 h-1.5 bg-amber-400 rounded-full" />
                Setup Required
              </span>
            )}
          </div>
        </div>

        {/* Profile buttons */}
        <div className="px-4 py-3 border-b border-blue-800 space-y-2">
          {!profile ? (
            <button
              onClick={() => setShowColdStart(true)}
              className="w-full px-3 py-2 bg-blue-700 hover:bg-blue-600 text-white text-xs font-sans font-medium rounded-lg transition-colors text-left"
            >
              Setup Profile
            </button>
          ) : (
            <button
              onClick={() => setShowColdStart(true)}
              className="w-full px-3 py-2 bg-blue-800 hover:bg-blue-700 text-blue-200 text-xs font-sans font-medium rounded-lg transition-colors text-left"
            >
              View / Edit Profile
            </button>
          )}
        </div>

        {/* Skills */}
        <div className="px-4 py-3 flex-1">
          <p className="text-blue-400 text-xs font-sans uppercase tracking-wider mb-2">Skills</p>
          <nav className="space-y-1">
            {meta.skills.map((skill) => (
              <button
                key={skill.id}
                onClick={() => {
                  setSelectedSkill(skill)
                  setOutput(null)
                  setRunError(null)
                  setExtraFields({})
                  setShowExtra(false)
                }}
                className={`w-full text-left px-3 py-2 rounded-lg text-xs font-sans transition-colors ${
                  selectedSkill?.id === skill.id
                    ? 'bg-white text-[#1e3a5f] font-medium'
                    : 'text-blue-200 hover:bg-blue-800 hover:text-white'
                }`}
              >
                {skill.label}
              </button>
            ))}
          </nav>
        </div>

        {/* Extra links */}
        {meta.extraLinks.length > 0 && (
          <div className="px-4 py-3 border-t border-blue-800">
            {meta.extraLinks.map((link) => (
              <button
                key={link.path}
                onClick={() => navigate(link.path)}
                className="w-full text-left px-3 py-2 rounded-lg text-xs font-sans text-blue-200 hover:bg-blue-800 hover:text-white transition-colors flex items-center gap-2"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
                {link.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Header */}
        <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
          <h1 className="font-serif font-semibold text-[#1e3a5f] text-lg">
            {selectedSkill ? selectedSkill.label : meta.name}
          </h1>
          {selectedSkill && (
            <p className="text-gray-500 text-sm font-sans mt-0.5">{selectedSkill.description}</p>
          )}
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
          {/* Input panel */}
          {selectedSkill && (
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <label className="block text-sm font-medium text-gray-700 font-sans mb-2">
                Input Text
              </label>

              {/* Textarea */}
              <textarea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder={`Paste ${selectedSkill.label.toLowerCase()} content here, or upload a file below…`}
                className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400 resize-none"
                rows={8}
                disabled={running}
              />

              {/* File upload */}
              <div className="mt-2 flex items-center gap-3">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx,.txt,.doc"
                  className="hidden"
                  onChange={handleFileUpload}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-sans text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                  </svg>
                  Or upload file (PDF / DOCX / TXT)
                </button>
                {inputText && (
                  <span className="text-xs text-gray-400 font-sans">
                    {inputText.length.toLocaleString()} characters
                  </span>
                )}
              </div>

              {/* Extra fields toggle */}
              {hasExtraFields(selectedSkill.id) && (
                <div className="mt-3">
                  <button
                    onClick={() => setShowExtra((v) => !v)}
                    className="flex items-center gap-1.5 text-xs text-[#1e3a5f] font-sans font-medium hover:underline"
                  >
                    <svg
                      className={`w-3.5 h-3.5 transition-transform ${showExtra ? 'rotate-90' : ''}`}
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2.5}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                    </svg>
                    Additional Context Fields
                  </button>
                  {showExtra && (
                    <div className="mt-3 p-4 bg-gray-50 rounded-lg border border-gray-200">
                      <ExtraFields
                        skillId={selectedSkill.id}
                        values={extraFields}
                        onChange={setExtraFields}
                      />
                    </div>
                  )}
                </div>
              )}

              {/* Premium toggle + Run button */}
              <div className="mt-4 flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer">
                  <div
                    className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer ${
                      usePremium ? 'bg-[#1e3a5f]' : 'bg-gray-300'
                    }`}
                    onClick={() => setUsePremium((v) => !v)}
                  >
                    <div
                      className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                        usePremium ? 'translate-x-4' : 'translate-x-0.5'
                      }`}
                    />
                  </div>
                  <span className="text-xs text-gray-600 font-sans">Use Premium LLM (Claude Opus 4)</span>
                </label>

                <button
                  onClick={handleRunSkill}
                  disabled={!inputText.trim() || running}
                  className="flex items-center gap-2 px-5 py-2.5 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-xl hover:bg-[#2e4f7a] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {running ? (
                    <>
                      <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                      Running…
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      Run {selectedSkill.label}
                    </>
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Run error */}
          {runError && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 flex items-start gap-2">
              <svg className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p className="text-red-700 text-sm font-sans">{runError}</p>
            </div>
          )}

          {/* Output */}
          {output && (
            <div className="space-y-3">
              <SkillOutput result={output} />

              {/* Continue in chat button */}
              <div className="flex justify-end">
                <button
                  onClick={handleContinueInChat}
                  className="flex items-center gap-2 px-4 py-2 text-sm font-sans text-[#1e3a5f] border border-[#1e3a5f] rounded-lg hover:bg-blue-50 transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                  </svg>
                  Continue conversation in Chat
                </button>
              </div>
            </div>
          )}

          {/* Empty state */}
          {!selectedSkill && (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <span className="text-5xl mb-4">{meta.icon}</span>
              <h3 className="font-serif text-xl font-semibold text-[#1e3a5f] mb-2">{meta.name}</h3>
              <p className="text-gray-500 text-sm font-sans max-w-sm">
                Select a skill from the sidebar to get started.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
