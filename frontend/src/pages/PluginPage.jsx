import React, { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getPluginProfile, executeSkill } from '../api'
import ColdStartInterview from '../components/ColdStartInterview'
import SkillOutput from '../components/SkillOutput'
import {
  Scale, Lock, Landmark, Building2, UserCircle, Rocket, Lightbulb, Bot, ClipboardList, Vault, Handshake,
  ArrowLeft, FileUp, Settings2, Play
} from 'lucide-react'

// ── Plugin metadata ──────────────────────────────────────────────────────────

const PLUGIN_META = {
  'commercial-legal': {
    icon: Scale,
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
    icon: Lock,
    name: 'Privacy Legal',
    skills: [
      { id: 'dpa-review', label: 'DPA Review', description: 'Review a Data Processing Agreement for GDPR/CCPA compliance gaps.' },
      { id: 'dsar-response', label: 'DSAR Response', description: 'Draft a Data Subject Access Request response letter.' },
      { id: 'pia-review', label: 'Privacy Impact Assessment', description: 'Conduct a Privacy Impact Assessment for a new product or process.' },
    ],
    extraLinks: [],
  },
  'litigation-legal': {
    icon: Landmark,
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
    icon: Building2,
    name: 'Corporate Legal',
    skills: [
      { id: 'ma-diligence', label: 'M&A Diligence', description: 'Generate an M&A diligence checklist and risk summary.' },
      { id: 'closing-checklist', label: 'Closing Checklist', description: 'Create a closing checklist for a corporate transaction.' },
      { id: 'entity-compliance', label: 'Entity Compliance', description: 'Review entity compliance status and flag deficiencies.' },
    ],
    extraLinks: [],
  },
  'employment-legal': {
    icon: UserCircle,
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
    icon: Rocket,
    name: 'Product Legal',
    skills: [
      { id: 'launch-review', label: 'Launch Review', description: 'Review a product launch for legal and regulatory risks.' },
      { id: 'marketing-claims', label: 'Marketing Claims Check', description: 'Check marketing claims for false advertising risk.' },
      { id: 'regulatory-triage', label: 'Regulatory Triage', description: 'Triage a product feature for regulatory classification.' },
    ],
    extraLinks: [],
  },
  'ip-legal': {
    icon: Lightbulb,
    name: 'IP Legal',
    skills: [
      { id: 'trademark-clearance', label: 'Trademark Clearance', description: 'Assess trademark clearance risk for a brand name or mark.' },
      { id: 'fto-analysis', label: 'Freedom-to-Operate', description: 'Analyze freedom-to-operate risk for a product or technology.' },
      { id: 'cd-letter', label: 'C&D Letter', description: 'Draft a cease and desist letter for IP infringement.' },
    ],
    extraLinks: [],
  },
  'ai-governance-legal': {
    icon: Bot,
    name: 'AI Governance',
    skills: [
      { id: 'ai-use-case-triage', label: 'AI Use Case Triage', description: 'Triage an AI use case for legal and regulatory risk.' },
      { id: 'ai-impact-assessment', label: 'AI Impact Assessment', description: 'Conduct an AI impact assessment for a proposed system.' },
      { id: 'vendor-ai-review', label: 'Vendor AI Review', description: 'Review vendor AI terms for risk and compliance.' },
    ],
    extraLinks: [],
  },
  'regulatory-legal': {
    icon: ClipboardList,
    name: 'Regulatory Legal',
    skills: [
      { id: 'regulatory-monitoring', label: 'Regulatory Monitoring', description: 'Summarize recent regulatory developments in a given area.' },
      { id: 'policy-gap-analysis', label: 'Policy Gap Analysis', description: 'Identify gaps between current policies and regulatory requirements.' },
      { id: 'nprm-comment', label: 'NPRM Comment', description: 'Draft a public comment on a Notice of Proposed Rulemaking.' },
    ],
    extraLinks: [],
  },
  'trust-estate-legal': {
    icon: Vault,
    name: 'Trust & Estate',
    skills: [
      { id: 'will-drafting-review', label: 'Will & Trust Review', description: 'Review a will or trust instrument for drafting risks and gaps.' },
      { id: 'estate-tax-analysis', label: 'Estate Tax Analysis', description: 'Analyze estate and gift tax exposure and planning options.' },
      { id: 'probate-checklist', label: 'Probate Checklist', description: 'Generate a probate administration checklist with key deadlines.' },
      { id: 'beneficiary-review', label: 'Beneficiary Review', description: 'Review beneficiary designations and funding for consistency.' },
    ],
    extraLinks: [{ label: 'Estate Portfolio', path: '/plugins/trust-estate/estates' }],
  },
  'mediation-legal': {
    icon: Handshake,
    name: 'Mediation',
    skills: [
      { id: 'mediation-intake', label: 'Mediation Intake', description: 'Intake a new mediation matter with party and dispute details.' },
      { id: 'mediation-brief', label: 'Mediation Brief', description: 'Draft a confidential mediation brief summarizing positions.' },
      { id: 'settlement-agreement', label: 'Settlement Agreement', description: 'Draft a settlement memorandum of understanding from agreed terms.' },
      { id: 'caucus-summary', label: 'Caucus Summary', description: 'Summarize a private caucus session and next-step recommendations.' },
    ],
    extraLinks: [{ label: 'Mediation Cases', path: '/plugins/mediation/cases' }],
  },
}

// ── Extra fields per skill ────────────────────────────────────────────────────

function ExtraFields({ skillId, values, onChange }) {
  const inputClasses = "w-full border border-brand-line rounded-lg px-4 py-2.5 text-sm font-sans focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface text-brand-ink transition-all"
  const labelClasses = "block text-xs font-semibold text-brand-ink uppercase tracking-wide mb-2"

  if (skillId === 'claim-chart') {
    return (
      <div>
        <label className={labelClasses}>Chart Mode</label>
        <select
          value={values.chart_mode || 'infringement'}
          onChange={(e) => onChange({ ...values, chart_mode: e.target.value })}
          className={inputClasses}
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
        <label className={labelClasses}>Conflicts Status</label>
        <select
          value={values.conflicts_status || 'not_run'}
          onChange={(e) => onChange({ ...values, conflicts_status: e.target.value })}
          className={inputClasses}
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
        <label className={labelClasses}>Jurisdiction</label>
        <select
          value={values.jurisdiction || 'GDPR'}
          onChange={(e) => onChange({ ...values, jurisdiction: e.target.value })}
          className={inputClasses}
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
        <label className={labelClasses}>Jurisdiction</label>
        <input
          type="text"
          value={values.jurisdiction || ''}
          onChange={(e) => onChange({ ...values, jurisdiction: e.target.value })}
          placeholder="e.g. California, New York, Federal"
          className={inputClasses}
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
    icon: Settings2,
    name: pluginName,
    skills: [],
    extraLinks: [],
  }
  const Icon = meta.icon

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

  // Reset skill/output/error state when the plugin route param changes so
  // stale state from a previously-viewed plugin doesn't bleed through.
  useEffect(() => {
    const currentMeta = PLUGIN_META[pluginName]
    setSelectedSkill(currentMeta?.skills[0] || null)
    setOutput(null)
    setRunError(null)
    setInputText('')
    setExtraFields({})
    setShowExtra(false)
  }, [pluginName])

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
    <div className="flex h-screen bg-brand-bg overflow-hidden relative">
      {/* Background noise */}
      <div
        className="absolute inset-0 opacity-[0.02] pointer-events-none z-0"
        style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}
      />

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
      <div className="w-[280px] bg-brand-surface border-r border-brand-line flex flex-col flex-shrink-0 z-10">
        {/* Back link */}
        <div className="px-6 pt-6 pb-2">
          <button
            onClick={() => navigate('/plugins')}
            className="flex items-center gap-2 text-brand-muted hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <ArrowLeft size={16} />
            All Plugins
          </button>
        </div>

        {/* Plugin header */}
        <div className="px-6 py-5 border-b border-brand-line">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-xl bg-brand-bg border border-brand-line flex items-center justify-center text-brand-ink shrink-0">
              <Icon size={20} />
            </div>
            <h2 className="text-brand-ink font-serif font-bold text-xl leading-tight">
              {meta.name}
            </h2>
          </div>

          {/* Profile status */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex-1">
              {profileLoading ? (
                <div className="h-5 bg-brand-line rounded-full animate-pulse w-24" />
              ) : profile ? (
                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-sans font-semibold uppercase tracking-wider text-green-700 bg-green-100 border border-green-200">
                  <span className="w-1.5 h-1.5 bg-green-500 rounded-full" />
                  Profile Active
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-sans font-semibold uppercase tracking-wider text-amber-700 bg-amber-100 border border-amber-200">
                  <span className="w-1.5 h-1.5 bg-amber-500 rounded-full" />
                  Setup Required
                </span>
              )}
            </div>
          </div>

          <button
            onClick={() => setShowColdStart(true)}
            className="w-full px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-[13px] font-sans font-medium rounded-lg hover:bg-brand-bg hover:border-brand-ink transition-all"
          >
            {profile ? 'Edit Profile' : 'Setup Profile'}
          </button>
        </div>

        {/* Skills */}
        <div className="px-4 py-6 flex-1 overflow-y-auto">
          <p className="px-2 text-xs font-semibold text-brand-muted uppercase tracking-widest mb-3 font-sans">
            Workflows
          </p>
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
                className={`w-full flex items-center text-left px-3 py-2.5 rounded-lg text-sm font-sans transition-all relative ${
                  selectedSkill?.id === skill.id
                    ? 'bg-brand-bg text-brand-ink font-semibold'
                    : 'text-brand-muted hover:bg-brand-bg hover:text-brand-ink font-medium'
                }`}
              >
                {selectedSkill?.id === skill.id && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-4 bg-brand-accent rounded-r-full" />
                )}
                <span className="ml-1">{skill.label}</span>
              </button>
            ))}
          </nav>

          {/* Extra links */}
          {meta.extraLinks.length > 0 && (
            <div className="mt-8">
              <p className="px-2 text-xs font-semibold text-brand-muted uppercase tracking-widest mb-3 font-sans">
                Resources
              </p>
              <nav className="space-y-1">
                {meta.extraLinks.map((link) => (
                  <button
                    key={link.path}
                    onClick={() => navigate(link.path)}
                    className="w-full flex items-center gap-2 text-left px-4 py-2.5 rounded-lg text-sm font-sans font-medium text-brand-muted hover:bg-brand-bg hover:text-brand-ink transition-colors"
                  >
                    <ClipboardList size={16} className="text-brand-muted" />
                    {link.label}
                  </button>
                ))}
              </nav>
            </div>
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden relative z-10">
        {/* Header */}
        <div className="bg-brand-surface border-b border-brand-line px-10 py-8 flex-shrink-0">
          <h1 className="font-serif font-bold text-3xl text-brand-ink tracking-tight mb-2">
            {selectedSkill ? selectedSkill.label : meta.name}
          </h1>
          {selectedSkill && (
            <p className="text-brand-muted text-[15px] font-sans max-w-3xl">{selectedSkill.description}</p>
          )}
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto px-10 py-8">
          <div className="max-w-4xl space-y-8 pb-12">

            {/* Input panel */}
            {selectedSkill && (
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-8 shadow-sm">

                <div className="flex items-center justify-between mb-4">
                  <label className="text-[15px] font-semibold text-brand-ink font-sans">
                    Input Materials
                  </label>
                  <div className="flex items-center gap-3">
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".pdf,.docx,.txt,.doc"
                      className="hidden"
                      onChange={handleFileUpload}
                    />
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      className="flex items-center gap-2 px-3 py-1.5 text-xs font-sans font-medium text-brand-muted border border-brand-line rounded-lg hover:border-brand-ink hover:text-brand-ink transition-colors"
                    >
                      <FileUp size={14} />
                      Upload File
                    </button>
                  </div>
                </div>

                {/* Textarea */}
                <div className="relative">
                  <textarea
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    placeholder={`Paste ${selectedSkill.label.toLowerCase()} content here, or upload a file...`}
                    className="w-full border border-brand-line rounded-xl px-5 py-4 text-[15px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent placeholder-brand-muted resize-y min-h-[240px] bg-brand-surface leading-relaxed"
                    disabled={running}
                  />
                  {inputText && (
                    <div className="absolute bottom-4 right-4 bg-brand-surface border border-brand-line px-2.5 py-1 rounded-md text-[11px] font-mono text-brand-muted shadow-sm">
                      {inputText.length.toLocaleString()} chars
                    </div>
                  )}
                </div>

                {/* Extra fields toggle */}
                {hasExtraFields(selectedSkill.id) && (
                  <div className="mt-6 border-t border-brand-line pt-6">
                    <button
                      onClick={() => setShowExtra((v) => !v)}
                      className="flex items-center gap-2 text-sm text-brand-ink font-sans font-semibold hover:text-brand-accent transition-colors"
                    >
                      <Settings2 size={16} />
                      Additional Context
                      <svg
                        className={`w-4 h-4 text-brand-muted transition-transform ml-1 ${showExtra ? 'rotate-180' : ''}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={2}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                      </svg>
                    </button>
                    {showExtra && (
                      <div className="mt-5 p-6 bg-brand-bg rounded-xl border border-brand-line">
                        <ExtraFields
                          skillId={selectedSkill.id}
                          values={extraFields}
                          onChange={setExtraFields}
                        />
                      </div>
                    )}
                  </div>
                )}

                {/* Action Footer */}
                <div className="mt-8 pt-6 border-t border-brand-line flex items-center justify-between">
                  <label className="flex items-center gap-3 cursor-pointer group">
                    <div
                      className={`relative w-11 h-6 rounded-full transition-colors ${
                        usePremium ? 'bg-brand-ink' : 'bg-brand-line'
                      }`}
                      onClick={() => setUsePremium((v) => !v)}
                    >
                      <div
                        className={`absolute top-0.5 w-5 h-5 bg-white rounded-full shadow-sm transition-transform ${
                          usePremium ? 'translate-x-[22px]' : 'translate-x-0.5'
                        }`}
                      />
                    </div>
                    <div className="flex flex-col">
                      <span className="text-[13px] font-semibold text-brand-ink font-sans">Premium Model</span>
                      <span className="text-[11px] text-brand-muted font-sans group-hover:text-brand-ink transition-colors">Claude Opus • Slower • Higher quality</span>
                    </div>
                  </label>

                  <button
                    onClick={handleRunSkill}
                    disabled={!inputText.trim() || running}
                    className="flex items-center gap-2 px-6 py-3 bg-brand-accent text-white text-[15px] font-sans font-semibold rounded-xl hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-sm"
                  >
                    {running ? (
                      <>
                        <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                        Analyzing...
                      </>
                    ) : (
                      <>
                        <Play size={16} className="fill-current" />
                        Run {selectedSkill.label}
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}

            {/* Error Message */}
            {runError && (
              <div className="bg-red-50 border border-red-200 rounded-xl p-5 flex items-start gap-3">
                <div className="mt-0.5 text-red-600">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-sm font-bold text-red-700 font-sans mb-1">Analysis Failed</h3>
                  <p className="text-sm text-red-600 font-sans">{runError}</p>
                </div>
              </div>
            )}

            {/* Output */}
            {output && (
              <div>
                <div className="flex items-center justify-between mb-4 px-1">
                  <h3 className="font-serif font-bold text-xl text-brand-ink">Analysis Results</h3>
                  <button
                    onClick={handleContinueInChat}
                    className="flex items-center gap-2 px-4 py-2 bg-brand-bg text-brand-ink text-sm font-sans font-medium rounded-lg border border-brand-line hover:border-brand-ink transition-colors shadow-sm"
                  >
                    <Bot size={16} />
                    Discuss in Chat
                  </button>
                </div>
                <SkillOutput result={output} />
              </div>
            )}

            {/* Empty state */}
            {!selectedSkill && (
              <div className="flex flex-col items-center justify-center py-20 text-center">
                <div className="w-16 h-16 rounded-2xl bg-brand-surface border border-brand-line flex items-center justify-center text-brand-muted mb-4">
                  <Icon size={32} strokeWidth={1.5} />
                </div>
                <h3 className="font-serif text-xl font-semibold text-brand-ink mb-2">{meta.name}</h3>
                <p className="text-brand-muted text-sm font-sans max-w-sm">
                  Select a workflow from the sidebar to get started.
                </p>
              </div>
            )}

          </div>
        </div>
      </div>
    </div>
  )
}
