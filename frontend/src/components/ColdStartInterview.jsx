import React, { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { runColdStart, savePluginProfile } from '../api'
import { X, Check, Bot } from 'lucide-react'

const TOTAL_STEPS = 8

const PLUGIN_LABELS = {
  'commercial-legal': 'Commercial Legal',
  'privacy-legal': 'Privacy Legal',
  'litigation-legal': 'Litigation Legal',
  'corporate-legal': 'Corporate Legal',
  'employment-legal': 'Employment Legal',
  'product-legal': 'Product Legal',
  'ip-legal': 'IP Legal',
  'ai-governance-legal': 'AI Governance Legal',
  'regulatory-legal': 'Regulatory Legal',
}

export default function ColdStartInterview({ plugin, onClose, onProfileSaved }) {
  const [step, setStep] = useState(1)
  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [generatedProfile, setGeneratedProfile] = useState(null)
  const [savingProfile, setSavingProfile] = useState(false)
  const [error, setError] = useState(null)
  const textareaRef = useRef(null)
  const messagesEndRef = useRef(null)

  useEffect(() => {
    startStep1()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const startStep1 = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await runColdStart(plugin, '', 1)
      setMessages([{ role: 'assistant', content: res.message || res.assistant_message || '' }])
      if (res.step) setStep(res.step)
    } catch (err) {
      setError('Failed to start interview. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const handleContinue = async () => {
    const userMsg = inputValue.trim()
    if (!userMsg || loading) return

    setMessages((prev) => [...prev, { role: 'user', content: userMsg }])
    setInputValue('')
    setLoading(true)
    setError(null)

    try {
      const res = await runColdStart(plugin, userMsg, step)
      const nextStep = res.step || step + 1
      setStep(nextStep)

      const assistantMsg = res.message || res.assistant_message || ''
      setMessages((prev) => [...prev, { role: 'assistant', content: assistantMsg }])

      if (res.profile_complete || nextStep > TOTAL_STEPS) {
        setGeneratedProfile(res.profile || res.generated_profile || null)
      }
    } catch (err) {
      setError('Failed to process response. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const handleSaveProfile = async () => {
    if (!generatedProfile) return
    setSavingProfile(true)
    setError(null)
    try {
      await savePluginProfile(plugin, generatedProfile)
      if (onProfileSaved) onProfileSaved(generatedProfile)
      onClose()
    } catch (err) {
      setError('Failed to save profile. Please try again.')
    } finally {
      setSavingProfile(false)
    }
  }

  const handleSaveAndExit = async () => {
    const key = `coldstart_${plugin}`
    localStorage.setItem(
      key,
      JSON.stringify({ step, messages, inputValue })
    )
    onClose()
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleContinue()
    }
  }

  const isComplete = generatedProfile !== null || step > TOTAL_STEPS

  return (
    <div className="fixed inset-0 bg-brand-ink/40 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-in fade-in duration-200">
      <div className="bg-brand-bg rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[90vh] border border-brand-line overflow-hidden animate-in zoom-in-95 duration-200">

        {/* Header */}
        <div className="bg-brand-surface px-8 py-5 border-b border-brand-line flex-shrink-0">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-brand-ink font-serif font-bold text-xl">
                {PLUGIN_LABELS[plugin] || plugin} — Setup
              </h2>
              <p className="text-brand-ink-2 text-sm font-sans mt-1">
                Step {Math.min(step, TOTAL_STEPS)} of {TOTAL_STEPS}
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-brand-muted hover:text-brand-ink transition-colors p-2"
              title="Close"
            >
              <X size={20} />
            </button>
          </div>

          {/* Progress bar */}
          <div className="bg-brand-bg-soft rounded-full h-1.5 overflow-hidden">
            <div
              className="bg-brand-accent h-1.5 rounded-full transition-all duration-500 ease-out"
              style={{ width: `${(Math.min(step, TOTAL_STEPS) / TOTAL_STEPS) * 100}%` }}
            />
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              {msg.role === 'assistant' && (
                <div className="max-w-[85%]">
                  <div className="flex items-center gap-2.5 mb-2">
                    <div className="w-7 h-7 bg-brand-bg-soft border border-brand-line rounded-lg flex items-center justify-center flex-shrink-0 relative">
                       <Bot size={14} className="text-brand-ink" />
                       <div className="absolute -bottom-1 -right-1 w-2.5 h-2.5 bg-brand-accent border border-brand-surface rounded-full"></div>
                    </div>
                    <span className="text-[13px] text-brand-ink font-sans font-bold">Setup Assistant</span>
                  </div>
                  <div className="bg-brand-surface border border-brand-line rounded-2xl rounded-tl-sm px-5 py-4 shadow-sm">
                    <div className="text-[15px] prose-legal">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              )}
              {msg.role === 'user' && (
                <div className="max-w-[80%]">
                  <div className="bg-brand-ink text-brand-surface border border-brand-ink rounded-2xl rounded-tr-sm px-5 py-4 shadow-sm">
                    <p className="text-[15px] leading-relaxed whitespace-pre-wrap font-sans font-medium">{msg.content}</p>
                  </div>
                </div>
              )}
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="max-w-[85%]">
                 <div className="flex items-center gap-2.5 mb-2">
                    <div className="w-7 h-7 bg-brand-bg-soft border border-brand-line rounded-lg flex items-center justify-center flex-shrink-0 relative">
                       <Bot size={14} className="text-brand-ink" />
                    </div>
                 </div>
                 <div className="bg-brand-surface border border-brand-line rounded-2xl rounded-tl-sm px-5 py-4 shadow-sm inline-block">
                   <div className="flex gap-1.5 items-center h-4">
                     <span className="w-2 h-2 bg-brand-muted rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                     <span className="w-2 h-2 bg-brand-muted rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                     <span className="w-2 h-2 bg-brand-muted rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                   </div>
                 </div>
              </div>
            </div>
          )}

          {/* Generated profile preview */}
          {isComplete && generatedProfile && (
            <div className="bg-brand-green/10 border border-brand-green/20 rounded-2xl p-6">
              <div className="flex items-center gap-2.5 mb-3">
                <div className="w-6 h-6 rounded-full bg-brand-green flex items-center justify-center text-white">
                   <Check size={14} strokeWidth={3} />
                </div>
                <span className="text-brand-green font-bold text-[15px] font-sans">Profile Ready</span>
              </div>
              <p className="text-brand-green font-sans text-sm font-medium leading-relaxed">
                Your {PLUGIN_LABELS[plugin] || plugin} profile has been generated. Review and save it to activate the plugin.
              </p>
            </div>
          )}

          {error && (
            <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4">
              <p className="text-brand-rose text-[14px] font-sans font-medium">{error}</p>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="bg-brand-surface px-8 py-5 border-t border-brand-line flex-shrink-0">
          {!isComplete ? (
            <div className="flex gap-4 items-end">
              <textarea
                ref={textareaRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type your answer... (Enter to send)"
                className="flex-1 resize-none bg-brand-bg-soft border border-brand-line rounded-xl px-5 py-3.5 text-[15px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent placeholder-brand-muted min-h-[52px] max-h-32 transition-all shadow-sm"
                rows={1}
                disabled={loading}
              />
              <div className="flex flex-col gap-2 shrink-0">
                <button
                  onClick={handleContinue}
                  disabled={!inputValue.trim() || loading}
                  className="px-6 py-3.5 bg-brand-ink text-white text-[14px] font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted disabled:cursor-not-allowed transition-all shadow-sm"
                >
                  Send
                </button>
                <button
                  onClick={handleSaveAndExit}
                  className="px-6 py-2.5 bg-brand-surface text-brand-ink text-[13px] font-sans font-semibold rounded-xl border border-brand-line hover:bg-brand-bg-soft hover:border-brand-ink transition-all"
                >
                  Save & Exit
                </button>
              </div>
            </div>
          ) : (
            <div className="flex gap-4">
              {generatedProfile && (
                <button
                  onClick={handleSaveProfile}
                  disabled={savingProfile}
                  className="flex-1 px-6 py-3.5 bg-brand-ink text-white text-[15px] font-sans font-bold rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm flex items-center justify-center gap-2"
                >
                  {savingProfile ? (
                     <><div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" /> Saving...</>
                  ) : (
                     <><Check size={18} /> Save Profile</>
                  )}
                </button>
              )}
              <button
                onClick={onClose}
                className="px-6 py-3.5 bg-brand-surface text-brand-ink text-[15px] font-sans font-semibold rounded-xl border border-brand-line hover:bg-brand-bg-soft transition-all"
              >
                Close
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
