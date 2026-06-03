import React, { useState, useEffect, useCallback } from 'react'
import {
  getPromptList,
  getPromptDetail,
  savePromptOverride,
  resetPromptOverride,
  testPrompt,
} from '../api'

const VARIABLE_REF = [
  { var: '{work_product_header}', desc: 'Attorney work product disclaimer badge' },
  { var: '{universal_guardrails}', desc: 'Universal citation & ethics rules' },
  { var: '{practice_profile}', desc: "Tenant's practice profile content" },
  { var: '{matter_context}', desc: 'Current matter context (litigation)' },
  { var: '{dsar_context}', desc: 'DSAR request details' },
  { var: '{jurisdiction}', desc: 'Jurisdiction string' },
  { var: '{chart_mode}', desc: 'infringement / invalidity / civil-elements' },
]

function Spinner() {
  return (
    <div className="flex justify-center py-16">
      <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
    </div>
  )
}

function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer ${
        checked ? 'bg-brand-green' : 'bg-brand-line-2'
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
          checked ? 'translate-x-[18px]' : 'translate-x-1'
        }`}
      />
    </button>
  )
}

// ── Skill Tree Sidebar ──────────────────────────────────────────────────────

function SkillTree({ plugins, selectedKey, onSelect }) {
  return (
    <div className="space-y-1">
      {plugins.map((p) => (
        <div key={p.plugin_name}>
          <div className="text-[11px] font-bold text-brand-muted uppercase tracking-wider px-3 py-2">
            {p.display_name}
          </div>
          {p.skills.map((s) => {
            const key = `${p.plugin_name}/${s.skill_name}`
            const isSelected = key === selectedKey
            return (
              <button
                key={key}
                onClick={() => onSelect(p.plugin_name, s.skill_name)}
                className={`w-full text-left px-3 py-1.5 rounded-md text-sm font-sans transition-colors flex items-center gap-2 ${
                  isSelected
                    ? 'bg-brand-accent/10 text-brand-accent font-medium'
                    : 'text-brand-ink-2 hover:bg-brand-bg-soft hover:text-brand-ink'
                }`}
              >
                {s.has_override && (
                  <span className="w-2 h-2 rounded-full bg-brand-amber flex-shrink-0" title="Has custom override" />
                )}
                {!s.has_override && (
                  <span className="w-2 h-2 rounded-full bg-brand-line-2 flex-shrink-0" />
                )}
                <span className="truncate">{s.skill_name}</span>
                {s.is_active === false && (
                  <span className="text-[10px] text-brand-muted uppercase ml-auto">inactive</span>
                )}
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}

// ── Variable Reference ──────────────────────────────────────────────────────

function VariableRef({ onInsert }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="border border-brand-line rounded-lg">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 text-sm font-sans font-medium text-brand-ink hover:bg-brand-bg-soft transition-colors"
      >
        Template Variables
        <svg
          className={`w-3.5 h-3.5 text-brand-muted transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-1.5 border-t border-brand-line pt-2">
          {VARIABLE_REF.map((v) => (
            <button
              key={v.var}
              onClick={() => onInsert(v.var)}
              className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded hover:bg-brand-bg-soft transition-colors group"
            >
              <code className="text-xs font-mono text-brand-accent bg-brand-accent/5 px-1.5 py-0.5 rounded group-hover:bg-brand-accent/10">
                {v.var}
              </code>
              <span className="text-xs text-brand-muted truncate">{v.desc}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Edit Prompt Panel ────────────────────────────────────────────────────────

function EditPanel({ detail, plugin, skill, onSaved, onReset }) {
  const [overrideContent, setOverrideContent] = useState('')
  const [isActive, setIsActive] = useState(true)
  const [saving, setSaving] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [message, setMessage] = useState(null)
  const textareaRef = React.useRef(null)

  useEffect(() => {
    setOverrideContent(detail?.override_content ?? '')
    setIsActive(detail?.is_active ?? true)
    setMessage(null)
  }, [detail])

  const handleInsertVar = (v) => {
    const ta = textareaRef.current
    if (!ta) return
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const before = overrideContent.substring(0, start)
    const after = overrideContent.substring(end)
    setOverrideContent(before + v + after)
    setTimeout(() => {
      ta.focus()
      ta.selectionStart = ta.selectionEnd = start + v.length
    }, 0)
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await savePromptOverride(plugin, skill, {
        prompt_content: overrideContent,
        is_active: isActive,
      })
      setMessage({ type: 'success', text: 'Override saved. Skills will use the custom prompt immediately.' })
      onSaved()
    } catch (e) {
      setMessage({ type: 'error', text: e?.response?.data?.detail || 'Failed to save override' })
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    setSaving(true)
    setMessage(null)
    try {
      await resetPromptOverride(plugin, skill)
      setMessage({ type: 'success', text: 'Override removed. Code default restored.' })
      setOverrideContent('')
      onReset()
    } catch (e) {
      setMessage({ type: 'error', text: e?.response?.data?.detail || 'Failed to reset' })
    } finally {
      setSaving(false)
      setResetOpen(false)
    }
  }

  if (!detail) return null

  const hasUnsavedChanges = overrideContent !== (detail.override_content ?? '')

  return (
    <div className="space-y-6">
      {/* Message */}
      {message && (
        <div
          className={`px-4 py-3 rounded-lg text-sm font-sans ${
            message.type === 'success'
              ? 'bg-brand-green/10 text-brand-green border border-brand-green/20'
              : 'bg-brand-rose/10 text-brand-rose border border-brand-rose/20'
          }`}
        >
          {message.text}
        </div>
      )}

      {/* Default prompt (readonly) */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="text-[11px] font-bold text-brand-muted uppercase tracking-wider">
            Default Prompt (read-only)
          </label>
          <span className="text-[10px] text-brand-muted font-mono">{plugin}/{skill}</span>
        </div>
        <pre className="bg-brand-bg-soft border border-brand-line rounded-lg p-4 text-xs font-mono text-brand-ink-2 overflow-auto max-h-60 whitespace-pre-wrap">
          {detail.default_content || 'No default prompt configured for this skill.'}
        </pre>
      </div>

      {/* Override textarea */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="text-[11px] font-bold text-brand-muted uppercase tracking-wider">
            Custom Override
          </label>
          <div className="flex items-center gap-3">
            <span className="text-xs text-brand-muted font-sans">Active</span>
            <Toggle checked={isActive} onChange={setIsActive} label="Override active" />
          </div>
        </div>
        <textarea
          ref={textareaRef}
          value={overrideContent}
          onChange={(e) => setOverrideContent(e.target.value)}
          className="w-full min-h-[300px] px-4 py-3 border border-brand-line rounded-lg text-sm font-mono text-brand-ink placeholder-brand-muted bg-brand-surface focus:outline-none focus:ring-2 focus:ring-brand-accent/30 focus:border-brand-accent resize-y"
          placeholder="Enter custom prompt content. Leave empty to use code default."
          spellCheck={false}
        />
      </div>

      {/* Variable reference */}
      <VariableRef onInsert={handleInsertVar} />

      {/* Actions */}
      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={handleSave}
          disabled={saving || (!hasUnsavedChanges && !message?.type?.startsWith('error'))}
          className="px-5 py-2.5 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {saving ? 'Saving...' : detail.override_content ? 'Update Override' : 'Save Override'}
        </button>

        {detail.override_content && (
          <>
            <button
              onClick={() => setResetOpen(true)}
              disabled={saving}
              className="px-5 py-2.5 border border-brand-line text-brand-ink-2 text-sm font-sans font-medium rounded-lg hover:bg-brand-rose/10 hover:text-brand-rose hover:border-brand-rose/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Reset to Default
            </button>
            {resetOpen && (
              <div className="flex items-center gap-2 ml-2">
                <span className="text-xs text-brand-rose font-sans">Are you sure?</span>
                <button
                  onClick={handleReset}
                  className="text-xs px-3 py-1.5 bg-brand-rose text-white rounded font-sans font-medium"
                >
                  Yes, Reset
                </button>
                <button
                  onClick={() => setResetOpen(false)}
                  className="text-xs px-3 py-1.5 border border-brand-line rounded font-sans text-brand-ink-2"
                >
                  Cancel
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Test Prompt Panel ────────────────────────────────────────────────────────

function TestPanel({ plugin, skill, currentContent }) {
  const [input, setInput] = useState('')
  const [result, setResult] = useState(null)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState(null)

  const handleTest = async () => {
    if (!input.trim()) return
    setTesting(true)
    setError(null)
    setResult(null)
    try {
      const res = await testPrompt(plugin, skill, {
        prompt_content: currentContent || '',
        sample_input: input,
      })
      setResult(res)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Test failed')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-2 block">
          Sample Input
        </label>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="w-full h-32 px-4 py-3 border border-brand-line rounded-lg text-sm font-sans text-brand-ink placeholder-brand-muted bg-brand-surface focus:outline-none focus:ring-2 focus:ring-brand-accent/30 focus:border-brand-accent resize-y"
          placeholder="Enter sample input to test the prompt..."
        />
      </div>

      <button
        onClick={handleTest}
        disabled={testing || !input.trim()}
        className="px-5 py-2.5 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        {testing ? 'Running...' : 'Run Test'}
      </button>

      {error && (
        <div className="px-4 py-3 bg-brand-rose/10 text-brand-rose border border-brand-rose/20 rounded-lg text-sm font-sans">
          {error}
        </div>
      )}

      {result && (
        <div className="space-y-3">
          <div className="flex items-center gap-4 text-xs text-brand-muted font-sans">
            <span>Model: <span className="font-mono text-brand-ink-2">{result.model_used}</span></span>
            <span>Tokens: <span className="font-mono text-brand-ink-2">{result.tokens_used?.toLocaleString()}</span></span>
          </div>

          {result.gates_triggered?.length > 0 && (
            <div className="px-4 py-3 bg-brand-rose/10 text-brand-rose border border-brand-rose/20 rounded-lg text-sm font-sans">
              {result.gates_triggered.join('\n')}
            </div>
          )}

          <div className="bg-brand-surface border border-brand-line rounded-lg">
            <div className="px-4 py-3 border-b border-brand-line bg-brand-bg-soft/50">
              <span className="text-[11px] font-bold text-brand-muted uppercase tracking-wider">Response</span>
            </div>
            <pre className="p-4 text-sm font-sans text-brand-ink whitespace-pre-wrap overflow-auto max-h-96 leading-relaxed">
              {result.response_text}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function PromptAdminPage() {
  const [plugins, setPlugins] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [selectedPlugin, setSelectedPlugin] = useState(null)
  const [selectedSkill, setSelectedSkill] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const [tab, setTab] = useState('edit')

  // Load plugin tree
  useEffect(() => {
    getPromptList()
      .then((data) => setPlugins(data.plugins))
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load prompts'))
      .finally(() => setLoading(false))
  }, [])

  // Load detail when selection changes
  const loadDetail = useCallback(async (plugin, skill) => {
    setDetailLoading(true)
    setDetail(null)
    try {
      const data = await getPromptDetail(plugin, skill)
      setDetail(data)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load prompt detail')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleSelect = (plugin, skill) => {
    setSelectedPlugin(plugin)
    setSelectedSkill(skill)
    setTab('edit')
    loadDetail(plugin, skill)
  }

  const handleRefreshTree = async () => {
    try {
      const data = await getPromptList()
      setPlugins(data.plugins)
    } catch (e) {
      // ignore refresh errors
    }
  }

  if (loading) return <Spinner />

  return (
    <div className="flex h-full min-h-[calc(100vh-12rem)] gap-0">
      {/* Left: Skill Tree */}
      <div className="w-72 flex-shrink-0 border-r border-brand-line overflow-y-auto bg-brand-surface-2">
        <div className="p-4">
          <h3 className="text-sm font-serif font-bold text-brand-ink mb-1">Prompts</h3>
          <p className="text-xs text-brand-muted font-sans mb-4">
            Select a skill to view and edit its system prompt.
          </p>
          {error && (
            <p className="text-xs text-brand-rose font-sans mb-4 bg-brand-rose/10 px-3 py-2 rounded-lg">
              {error}
            </p>
          )}
          {plugins.length === 0 ? (
            <p className="text-sm text-brand-muted font-sans py-8 text-center">No plugins found.</p>
          ) : (
            <SkillTree plugins={plugins} selectedKey={`${selectedPlugin}/${selectedSkill}`} onSelect={handleSelect} />
          )}
        </div>
      </div>

      {/* Right: Detail Panel */}
      <div className="flex-1 overflow-y-auto">
        {!selectedPlugin ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-brand-muted text-sm font-sans">Select a skill from the tree to edit its prompt.</p>
          </div>
        ) : detailLoading ? (
          <div className="p-8">
            <Spinner />
          </div>
        ) : (
          <div className="p-8 max-w-4xl">
            {/* Tab bar */}
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-xl font-serif font-bold text-brand-ink">{selectedSkill}</h2>
                <p className="text-xs text-brand-muted font-mono mt-0.5">{selectedPlugin}</p>
              </div>
              <div className="flex gap-1 border border-brand-line rounded-lg p-0.5">
                <button
                  onClick={() => setTab('edit')}
                  className={`px-4 py-1.5 text-sm font-sans font-medium rounded-md transition-colors ${
                    tab === 'edit'
                      ? 'bg-brand-accent text-white'
                      : 'text-brand-muted hover:text-brand-ink'
                  }`}
                >
                  Edit
                </button>
                <button
                  onClick={() => setTab('test')}
                  className={`px-4 py-1.5 text-sm font-sans font-medium rounded-md transition-colors ${
                    tab === 'test'
                      ? 'bg-brand-accent text-white'
                      : 'text-brand-muted hover:text-brand-ink'
                  }`}
                >
                  Test
                </button>
              </div>
            </div>

            {/* Panel content */}
            {tab === 'edit' && detail && (
              <EditPanel
                detail={detail}
                plugin={selectedPlugin}
                skill={selectedSkill}
                onSaved={handleRefreshTree}
                onReset={() => {
                  handleRefreshTree()
                  loadDetail(selectedPlugin, selectedSkill)
                }}
              />
            )}

            {tab === 'test' && (
              <TestPanel
                plugin={selectedPlugin}
                skill={selectedSkill}
                currentContent={detail?.override_content ?? detail?.default_content ?? ''}
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}
