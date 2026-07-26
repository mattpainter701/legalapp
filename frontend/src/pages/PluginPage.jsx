import React, { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getPlugins, getPluginProfile, executeSkill, extractSkillInput, getPluginSetup, savePluginSetup, getMattersV2 } from '../api'
import ColdStartInterview from '../components/ColdStartInterview'
import SkillOutput from '../components/SkillOutput'
import {
  ArrowLeft, Bot, ClipboardList, FileUp, Settings2, Play
} from 'lucide-react'

// ── Extra fields per skill ────────────────────────────────────────────────────

function ExtraFields({ skillId, values, onChange }) {
  const inputClasses = "w-full border border-brand-line rounded-lg px-4 py-2.5 text-sm font-sans focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface text-brand-ink transition-all"
  const labelClasses = "block text-xs font-semibold text-brand-ink uppercase tracking-wide mb-2"

  if (skillId === 'claim-chart') {
    return (
      <div>
        <label htmlFor="pluginpage-chart-mode" className={labelClasses}>Chart Mode</label>
        <select id="pluginpage-chart-mode"
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
        <label htmlFor="pluginpage-conflicts-status" className={labelClasses}>Conflicts Status</label>
        <select id="pluginpage-conflicts-status"
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
        <label htmlFor="pluginpage-jurisdiction" className={labelClasses}>Jurisdiction</label>
        <select id="pluginpage-jurisdiction"
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
        <label htmlFor="pluginpage-jurisdiction-2" className={labelClasses}>Jurisdiction</label>
        <input id="pluginpage-jurisdiction-2"
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

function StructuredSetupModal({ pluginName, pluginLabel, setupData, onClose, onSaved }) {
  const setup = setupData?.setup || {}
  const health = setupData?.health || {}
  const [jurisdictions, setJurisdictions] = useState((setup.jurisdictions || []).join('\n'))
  const [escalationRules, setEscalationRules] = useState(JSON.stringify(setup.escalation_rules || {}, null, 2))
  const [approvalThresholds, setApprovalThresholds] = useState(JSON.stringify(setup.approval_thresholds || {}, null, 2))
  const [houseStyle, setHouseStyle] = useState(JSON.stringify(setup.house_style || {}, null, 2))
  const [cloudBindings, setCloudBindings] = useState(JSON.stringify(setup.cloud_bindings || {}, null, 2))
  const [calendarBindings, setCalendarBindings] = useState(JSON.stringify(setup.calendar_bindings || {}, null, 2))
  const [templatePreferences, setTemplatePreferences] = useState(JSON.stringify(setup.template_preferences || {}, null, 2))
  const [customConfig, setCustomConfig] = useState(JSON.stringify(setup.custom_config || {}, null, 2))
  const [isComplete, setIsComplete] = useState(Boolean(setup.is_complete))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const inputClasses = "w-full border border-brand-line rounded-lg px-3 py-2.5 text-[13px] font-mono focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface text-brand-ink transition-all"
  const labelClasses = "block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5"

  const parseJson = (label, value) => {
    try {
      return value.trim() ? JSON.parse(value) : {}
    } catch {
      throw new Error(`${label} must be valid JSON.`)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload = {
        jurisdictions: jurisdictions.split('\n').map(v => v.trim()).filter(Boolean),
        escalation_rules: parseJson('Escalation rules', escalationRules),
        approval_thresholds: parseJson('Approval thresholds', approvalThresholds),
        house_style: parseJson('House style', houseStyle),
        cloud_bindings: parseJson('Cloud bindings', cloudBindings),
        calendar_bindings: parseJson('Calendar bindings', calendarBindings),
        template_preferences: parseJson('Template preferences', templatePreferences),
        custom_config: parseJson('Custom config', customConfig),
        is_complete: isComplete,
      }
      const saved = await savePluginSetup(pluginName, payload)
      onSaved(saved)
      onClose()
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to save setup.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-brand-ink/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-brand-bg border border-brand-line rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
        <div className="bg-brand-surface px-7 py-5 border-b border-brand-line flex items-start justify-between">
          <div>
            <h2 className="font-serif font-bold text-xl text-brand-ink">{pluginLabel} Setup</h2>
            <p className="text-[13px] text-brand-muted font-sans mt-1">Structured workflow configuration, profile generation, and integration readiness.</p>
          </div>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink p-2">×</button>
        </div>

        <div className="flex-1 overflow-y-auto px-7 py-6 space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="bg-brand-surface border border-brand-line rounded-xl p-4">
              <p className="text-[11px] uppercase tracking-widest font-bold text-brand-muted mb-1">Status</p>
              <p className="text-brand-ink font-sans font-semibold capitalize">{health.setup_status || 'not-started'}</p>
            </div>
            <div className="bg-brand-surface border border-brand-line rounded-xl p-4">
              <p className="text-[11px] uppercase tracking-widest font-bold text-brand-muted mb-1">Available Integrations</p>
              <p className="text-brand-ink font-sans text-sm">{(health.available_integrations || []).join(', ') || 'None'}</p>
            </div>
            <div className="bg-brand-surface border border-brand-line rounded-xl p-4">
              <p className="text-[11px] uppercase tracking-widest font-bold text-brand-muted mb-1">Missing Required</p>
              <p className="text-brand-ink font-sans text-sm">{(health.missing_required_integrations || []).join(', ') || 'None'}</p>
            </div>
          </div>

          {health.warnings?.length > 0 && (
            <div className="bg-brand-amber/10 border border-brand-amber/20 rounded-xl px-4 py-3 text-brand-amber text-sm font-sans">
              {health.warnings.join(' ')}
            </div>
          )}

          <div>
            <label htmlFor="pluginpage-jurisdictions" className={labelClasses}>Jurisdictions</label>
            <textarea id="pluginpage-jurisdictions" value={jurisdictions} onChange={e => setJurisdictions(e.target.value)} rows={3} className={`${inputClasses} font-sans`} placeholder="One jurisdiction per line" />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div><label htmlFor="pluginpage-escalation-rules-json" className={labelClasses}>Escalation Rules JSON</label><textarea id="pluginpage-escalation-rules-json" value={escalationRules} onChange={e => setEscalationRules(e.target.value)} rows={7} className={inputClasses} /></div>
            <div><label htmlFor="pluginpage-approval-thresholds-json" className={labelClasses}>Approval Thresholds JSON</label><textarea id="pluginpage-approval-thresholds-json" value={approvalThresholds} onChange={e => setApprovalThresholds(e.target.value)} rows={7} className={inputClasses} /></div>
            <div><label htmlFor="pluginpage-house-style-json" className={labelClasses}>House Style JSON</label><textarea id="pluginpage-house-style-json" value={houseStyle} onChange={e => setHouseStyle(e.target.value)} rows={7} className={inputClasses} /></div>
            <div><label htmlFor="pluginpage-cloud-bindings-json" className={labelClasses}>Cloud Bindings JSON</label><textarea id="pluginpage-cloud-bindings-json" value={cloudBindings} onChange={e => setCloudBindings(e.target.value)} rows={7} className={inputClasses} /></div>
            <div><label htmlFor="pluginpage-calendar-bindings-json" className={labelClasses}>Calendar Bindings JSON</label><textarea id="pluginpage-calendar-bindings-json" value={calendarBindings} onChange={e => setCalendarBindings(e.target.value)} rows={7} className={inputClasses} /></div>
            <div><label htmlFor="pluginpage-template-preferences-json" className={labelClasses}>Template Preferences JSON</label><textarea id="pluginpage-template-preferences-json" value={templatePreferences} onChange={e => setTemplatePreferences(e.target.value)} rows={7} className={inputClasses} /></div>
          </div>

          <div>
            <label htmlFor="pluginpage-custom-config-json" className={labelClasses}>Custom Config JSON</label>
            <textarea id="pluginpage-custom-config-json" value={customConfig} onChange={e => setCustomConfig(e.target.value)} rows={5} className={inputClasses} />
          </div>

          <label className="flex items-center gap-3 text-sm font-sans text-brand-ink">
            <input type="checkbox" checked={isComplete} onChange={e => setIsComplete(e.target.checked)} className="w-4 h-4 accent-brand-accent" />
            Mark setup complete and activate generated profile
          </label>

          {error && <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-4 py-3 text-brand-rose text-sm font-sans">{error}</div>}
        </div>

        <div className="bg-brand-surface px-7 py-4 border-t border-brand-line flex justify-end gap-3">
          <button onClick={onClose} className="px-5 py-2.5 text-brand-ink text-sm font-sans font-semibold border border-brand-line rounded-xl hover:bg-brand-bg">Cancel</button>
          <button onClick={handleSave} disabled={saving} className="px-6 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:opacity-50">{saving ? 'Saving...' : 'Save Setup'}</button>
        </div>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

function skillLabel(skillId) {
  return skillId
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export default function PluginPage() {
  const { pluginName } = useParams()
  const navigate = useNavigate()

  const [catalogPlugin, setCatalogPlugin] = useState(null)

  // Derive all plugin metadata from the backend catalog — no duplicated frontend metadata.
  const displayName = catalogPlugin?.display_name || pluginName
  const skills = (catalogPlugin?.skills || [])
    .filter((s) => s !== 'cold-start-interview')
    .map((skillId) => ({
      id: skillId,
      label: skillLabel(skillId),
      description: `Run the ${skillLabel(skillId).toLowerCase()} workflow with optional matter and cloud context.`,
    }))
  const extraLinks = catalogPlugin?.primary_route
    ? [{ label: 'Plugin Workspace', path: catalogPlugin.primary_route }]
    : []
  const Icon = Settings2

  const [profile, setProfile] = useState(null)
  const [profileLoading, setProfileLoading] = useState(true)
  const [showColdStart, setShowColdStart] = useState(false)
  const [showStructuredSetup, setShowStructuredSetup] = useState(false)
  const [setupData, setSetupData] = useState(null)
  const [matters, setMatters] = useState([])
  const [selectedMatterId, setSelectedMatterId] = useState('')
  const [selectedSkill, setSelectedSkill] = useState(skills[0] || null)
  const [inputText, setInputText] = useState('')
  const [extraFields, setExtraFields] = useState({})
  const [showExtra, setShowExtra] = useState(false)
  const [usePremium, setUsePremium] = useState(false)
  const [output, setOutput] = useState(null)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadNotice, setUploadNotice] = useState(null)
  const fileInputRef = useRef(null)

  // Reset skill/output/error state when the plugin route param changes so
  // stale state from a previously-viewed plugin doesn't bleed through.
  useEffect(() => {
    setCatalogPlugin(null)
    setSelectedSkill(null)
    setOutput(null)
    setRunError(null)
    setInputText('')
    setExtraFields({})
    setShowExtra(false)
    setUploadNotice(null)
  }, [pluginName])

  useEffect(() => {
    if (skills.length) {
      setSelectedSkill(skills[0])
    }
  }, [catalogPlugin, pluginName])

  useEffect(() => {
    setProfileLoading(true)
    getPlugins()
      .then((data) => {
        const list = Array.isArray(data) ? data : data?.plugins || []
        setCatalogPlugin(list.find((p) => p.plugin_name === pluginName || p.id === pluginName) || null)
      })
      .catch(() => setCatalogPlugin(null))
    getPluginProfile(pluginName)
      .then((data) => setProfile(data))
      .catch(() => setProfile(null))
      .finally(() => setProfileLoading(false))
    getPluginSetup(pluginName)
      .then(setSetupData)
      .catch(() => setSetupData(null))
    getMattersV2({ page_size: 100 })
      .then(data => setMatters(data.items || []))
      .catch(() => setMatters([]))
  }, [pluginName])

  // PDF and DOCX are binary: reading them in the browser produced garbage
  // input. The backend extracts text (with OCR fallback for scanned PDFs).
  const handleFileUpload = async (e) => {
    const file = e.target.files[0]
    e.target.value = ''
    if (!file) return
    setUploading(true)
    setUploadNotice(null)
    setRunError(null)
    try {
      const result = await extractSkillInput(file)
      setInputText(result.text || '')
      const notes = []
      if (result.ocr_used) {
        notes.push(
          `Scanned document — text recovered by OCR${
            result.pages_analyzed ? ` from ${result.pages_analyzed} page(s)` : ''
          }. Check it against the original before relying on it.`
        )
      }
      if (result.truncated) {
        notes.push('The document was long and has been truncated.')
      }
      setUploadNotice({
        tone: notes.length ? 'warn' : 'info',
        text: [
          `${result.filename}: ${result.characters.toLocaleString()} characters extracted.`,
          ...notes,
        ].join(' '),
      })
    } catch (err) {
      setUploadNotice({
        tone: 'error',
        text:
          err?.response?.data?.detail ||
          err?.message ||
          'That file could not be read. Paste the text directly instead.',
      })
    } finally {
      setUploading(false)
    }
  }

  const handleRunSkill = async () => {
    if (!selectedSkill || !inputText.trim() || running) return
    setRunning(true)
    setRunError(null)
    setOutput(null)
    try {
      const payload = {
        text: inputText,
        matter_id: selectedMatterId || undefined,
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
      `[From ${displayName} — ${selectedSkill?.label}]\n\n${output.memo}`
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

      {showStructuredSetup && (
        <StructuredSetupModal
          pluginName={pluginName}
          pluginLabel={displayName}
          setupData={setupData}
          onClose={() => setShowStructuredSetup(false)}
          onSaved={(saved) => {
            setSetupData(saved)
            setProfile(saved?.setup?.generated_profile ? { profile_content: saved.setup.generated_profile, is_complete: saved.setup.is_complete } : profile)
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
              {displayName}
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
            onClick={() => setShowStructuredSetup(true)}
            className="w-full px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-[13px] font-sans font-medium rounded-lg hover:bg-brand-bg hover:border-brand-ink transition-all"
          >
            {setupData?.setup ? 'Edit Setup' : 'Setup Workflow'}
          </button>
          <button
            onClick={() => setShowColdStart(true)}
            className="w-full mt-2 px-4 py-2 bg-brand-bg text-brand-muted border border-brand-line text-[12px] font-sans font-medium rounded-lg hover:text-brand-ink transition-all"
          >
            Legacy Interview
          </button>
        </div>

        {/* Skills */}
        <div className="px-4 py-6 flex-1 overflow-y-auto">
          <p className="px-2 text-xs font-semibold text-brand-muted uppercase tracking-widest mb-3 font-sans">
            Workflows
          </p>
          <nav className="space-y-1">
            {skills.map((skill) => (
              <button
                key={skill.id}
                onClick={() => {
                  setSelectedSkill(skill)
                  setOutput(null)
                  setRunError(null)
                  setExtraFields({})
                  setShowExtra(false)
                  setUploadNotice(null)
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
          {extraLinks.length > 0 && (
            <div className="mt-8">
              <p className="px-2 text-xs font-semibold text-brand-muted uppercase tracking-widest mb-3 font-sans">
                Resources
              </p>
              <nav className="space-y-1">
                {extraLinks.map((link) => (
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
            {selectedSkill ? selectedSkill.label : displayName}
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
                  <label htmlFor="pluginpage-input-materials" className="text-[15px] font-semibold text-brand-ink font-sans">
                    Input Materials
                  </label>
                  <div className="flex items-center gap-3">
                    <input id="pluginpage-input-materials"
                      ref={fileInputRef}
                      type="file"
                      accept=".pdf,.docx,.doc,.txt,.md"
                      className="hidden"
                      onChange={handleFileUpload}
                    />
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploading || running}
                      className="flex items-center gap-2 px-3 py-1.5 text-xs font-sans font-medium text-brand-muted border border-brand-line rounded-lg hover:border-brand-ink hover:text-brand-ink transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <FileUp size={14} />
                      {uploading ? 'Reading file…' : 'Upload File'}
                    </button>
                  </div>
                </div>

                {uploadNotice && (
                  <div
                    role="status"
                    className={`mb-5 rounded-xl border px-4 py-3 text-[13px] font-sans ${
                      uploadNotice.tone === 'error'
                        ? 'bg-red-50 border-red-200 text-red-700'
                        : uploadNotice.tone === 'warn'
                          ? 'bg-brand-amber/10 border-brand-amber/20 text-brand-amber'
                          : 'bg-brand-bg border-brand-line text-brand-muted'
                    }`}
                  >
                    {uploadNotice.text}
                  </div>
                )}

                <div className="mb-5">
                  <label htmlFor="pluginpage-matter-context" className="block text-xs font-semibold text-brand-ink uppercase tracking-wide mb-2">
                    Matter Context
                  </label>
                  <select id="pluginpage-matter-context"
                    value={selectedMatterId}
                    onChange={(e) => setSelectedMatterId(e.target.value)}
                    className="w-full border border-brand-line rounded-lg px-4 py-2.5 text-sm font-sans focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface text-brand-ink transition-all"
                  >
                    <option value="">No matter context</option>
                    {matters.map((matter) => (
                      <option key={matter.id} value={matter.id}>
                        {matter.matter_name}
                        {matter.primary_plugin ? ` — ${matter.primary_plugin}` : ''}
                      </option>
                    ))}
                  </select>
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
                    <input
                      type="checkbox"
                      checked={usePremium}
                      onChange={(event) => setUsePremium(event.target.checked)}
                      className="peer sr-only"
                    />
                    <div
                      className={`relative w-11 h-6 rounded-full transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-brand-accent peer-focus-visible:ring-offset-2 ${
                        usePremium ? 'bg-brand-ink' : 'bg-brand-line'
                      }`}
                      aria-hidden="true"
                    >
                      <div
                        className={`absolute top-0.5 w-5 h-5 bg-white rounded-full shadow-sm transition-transform ${
                          usePremium ? 'translate-x-[22px]' : 'translate-x-0.5'
                        }`}
                      />
                    </div>
                    <div className="flex flex-col">
                      <span className="text-[13px] font-semibold text-brand-ink font-sans">Premium Model</span>
                      <span className="text-[11px] text-brand-muted font-sans group-hover:text-brand-ink transition-colors">Premium • Slower • Higher quality</span>
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
                <h3 className="font-serif text-xl font-semibold text-brand-ink mb-2">{displayName}</h3>
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
