import React, { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { runColdStart, savePluginProfile } from '../api'

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

  // Kick off step 1 on mount
  useEffect(() => {
    startStep1()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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
    // Save partial progress in localStorage for resume
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
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="bg-[#1e3a5f] px-6 py-4 rounded-t-2xl flex-shrink-0">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-white font-serif font-semibold text-lg">
                {PLUGIN_LABELS[plugin] || plugin} — Setup Interview
              </h2>
              <p className="text-blue-200 text-xs mt-0.5 font-sans">
                Step {Math.min(step, TOTAL_STEPS)} of {TOTAL_STEPS}
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-blue-200 hover:text-white transition-colors"
              title="Close"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Progress bar */}
          <div className="mt-3 bg-blue-800 rounded-full h-1.5">
            <div
              className="bg-blue-300 h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${(Math.min(step, TOTAL_STEPS) / TOTAL_STEPS) * 100}%` }}
            />
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              {msg.role === 'assistant' && (
                <div className="max-w-[85%]">
                  <div className="flex items-center gap-2 mb-1.5">
                    <div className="w-6 h-6 bg-[#1e3a5f] rounded-full flex items-center justify-center flex-shrink-0">
                      <svg width="12" height="12" viewBox="0 0 32 32" fill="none">
                        <path
                          d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z"
                          fill="white"
                          fillOpacity="0.9"
                        />
                      </svg>
                    </div>
                    <span className="text-xs text-gray-500 font-sans">Setup Assistant</span>
                  </div>
                  <div className="bg-gray-50 border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3">
                    <div className="text-sm prose-legal">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              )}
              {msg.role === 'user' && (
                <div className="max-w-[75%]">
                  <div className="bg-[#1e3a5f] text-white rounded-2xl rounded-tr-sm px-4 py-3">
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                  </div>
                </div>
              )}
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="bg-gray-50 border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3">
                <div className="flex gap-1.5 items-center h-4">
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            </div>
          )}

          {/* Generated profile preview */}
          {isComplete && generatedProfile && (
            <div className="bg-green-50 border border-green-200 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <svg className="w-4 h-4 text-green-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span className="text-green-800 font-semibold text-sm font-sans">Profile Ready</span>
              </div>
              <p className="text-green-700 text-xs font-sans">
                Your {PLUGIN_LABELS[plugin] || plugin} profile has been generated. Review and save it to activate the plugin.
              </p>
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3">
              <p className="text-red-700 text-sm font-sans">{error}</p>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="px-6 py-4 border-t border-gray-200 flex-shrink-0">
          {!isComplete ? (
            <div className="flex gap-3 items-end">
              <textarea
                ref={textareaRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type your answer..."
                className="flex-1 resize-none border border-gray-300 rounded-xl px-4 py-3 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400 min-h-[48px] max-h-32"
                rows={2}
                disabled={loading}
              />
              <div className="flex flex-col gap-2">
                <button
                  onClick={handleContinue}
                  disabled={!inputValue.trim() || loading}
                  className="px-4 py-2 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-xl hover:bg-[#2e4f7a] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Continue
                </button>
                <button
                  onClick={handleSaveAndExit}
                  className="px-4 py-2 bg-gray-100 text-gray-700 text-sm font-sans font-medium rounded-xl hover:bg-gray-200 transition-colors"
                >
                  Save & Exit
                </button>
              </div>
            </div>
          ) : (
            <div className="flex gap-3">
              {generatedProfile && (
                <button
                  onClick={handleSaveProfile}
                  disabled={savingProfile}
                  className="flex-1 px-4 py-2.5 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-xl hover:bg-[#2e4f7a] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {savingProfile ? 'Saving...' : 'Save Profile'}
                </button>
              )}
              <button
                onClick={onClose}
                className="px-4 py-2.5 bg-gray-100 text-gray-700 text-sm font-sans font-medium rounded-xl hover:bg-gray-200 transition-colors"
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
