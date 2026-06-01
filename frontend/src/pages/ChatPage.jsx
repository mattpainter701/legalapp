import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import Sidebar from '../components/Sidebar'
import ChatMessage from '../components/ChatMessage'
import { ShieldCheck, BadgeCheck, Scale, FileText, Send } from 'lucide-react'
import {
  getConversations,
  createConversation,
  getConversation,
  sendMessage,
  getDocuments,
  logout,
} from '../api'

function TypingIndicator() {
  return (
    <div className="flex justify-start mb-8">
      <div className="bg-brand-surface border border-brand-line p-8 max-w-3xl w-full shadow-sm relative">
        <div className="absolute top-0 left-0 w-full h-1 bg-brand-gold"></div>
        <div className="flex items-center gap-2 text-xs font-mono text-brand-muted uppercase tracking-wider">
          <Scale className="w-4 h-4 text-brand-gold" strokeWidth={2} />
          <span className="font-bold text-brand-ink">Clarity Legal Analysis</span>
          <span className="ml-auto flex gap-1.5 items-center">
            <span className="w-1.5 h-1.5 bg-brand-muted animate-bounce" style={{ animationDelay: '0ms' }} />
            <span className="w-1.5 h-1.5 bg-brand-muted animate-bounce" style={{ animationDelay: '150ms' }} />
            <span className="w-1.5 h-1.5 bg-brand-muted animate-bounce" style={{ animationDelay: '300ms' }} />
          </span>
        </div>
      </div>
    </div>
  )
}

export default function ChatPage() {
  const { user, logout: authLogout } = useAuth()
  const navigate = useNavigate()

  // State
  const [conversations, setConversations] = useState([])
  const [activeConvId, setActiveConvId] = useState(null)
  const [messages, setMessages] = useState([])
  const [documents, setDocuments] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [includePublic, setIncludePublic] = useState(true)
  const [usePremium, setUsePremium] = useState(false)
  const [activeConvTitle, setActiveConvTitle] = useState('')

  const messagesEndRef = useRef(null)
  const textareaRef = useRef(null)

  // Auto-scroll
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, isSending, scrollToBottom])

  // Load conversations and documents on mount
  useEffect(() => {
    Promise.all([getConversations(), getDocuments()])
      .then(([convs, docs]) => {
        setConversations(convs)
        setDocuments(docs)
        if (convs.length > 0) {
          loadConversation(convs[0].id)
        }
      })
      .catch(console.error)

    // Check for pending message from plugin skill output
    const pending = sessionStorage.getItem('pending_chat_message')
    if (pending) {
      setInputValue(pending)
      sessionStorage.removeItem('pending_chat_message')
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const loadConversation = useCallback(async (id) => {
    setIsLoadingMessages(true)
    setActiveConvId(id)
    try {
      const data = await getConversation(id)
      setMessages(data.messages || [])
      setActiveConvTitle(data.conversation?.title || 'Untitled')
    } catch (err) {
      console.error('Failed to load conversation', err)
    } finally {
      setIsLoadingMessages(false)
    }
  }, [])

  const handleNewConversation = useCallback(async () => {
    try {
      const conv = await createConversation()
      setConversations((prev) => [conv, ...prev])
      setActiveConvId(conv.id)
      setMessages([])
      setActiveConvTitle(conv.title || 'New Conversation')
    } catch (err) {
      console.error('Failed to create conversation', err)
    }
  }, [])

  const handleConversationDeleted = useCallback(
    (id) => {
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (activeConvId === id) {
        setActiveConvId(null)
        setMessages([])
        setActiveConvTitle('')
      }
    },
    [activeConvId]
  )

  const handleSend = useCallback(async () => {
    const content = inputValue.trim()
    if (!content || isSending) return

    let convId = activeConvId

    // Create a conversation if none is active
    if (!convId) {
      try {
        const conv = await createConversation(content.slice(0, 60))
        setConversations((prev) => [conv, ...prev])
        setActiveConvId(conv.id)
        setActiveConvTitle(conv.title || 'New Conversation')
        convId = conv.id
      } catch (err) {
        console.error('Failed to create conversation', err)
        return
      }
    }

    const userMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      sources: [],
      created_at: new Date().toISOString(),
    }

    setMessages((prev) => [...prev, userMessage])
    setInputValue('')
    setIsSending(true)

    // Auto-resize textarea
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }

    try {
      const response = await sendMessage(convId, content, includePublic, usePremium)
      setMessages((prev) => [...prev, response])

      // Update conversation title if it was auto-generated
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId ? { ...c, updated_at: new Date().toISOString() } : c
        )
      )
    } catch (err) {
      console.error('Failed to send message', err)
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          role: 'assistant',
          content: 'An error occurred while processing your request. Please try again.',
          sources: [],
          created_at: new Date().toISOString(),
        },
      ])
    } finally {
      setIsSending(false)
    }
  }, [inputValue, isSending, activeConvId, includePublic, usePremium])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleTextareaChange = (e) => {
    setInputValue(e.target.value)
    // Auto-resize
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px'
  }

  const handleLogout = async () => {
    try {
      await logout()
    } catch {
      // ignore
    }
    authLogout()
    navigate('/login')
  }

  const handleDocumentUploaded = (doc) => {
    setDocuments((prev) => {
      const exists = prev.find((d) => d.id === doc.id)
      if (exists) return prev
      return [doc, ...prev]
    })
  }

  const handleDocumentDeleted = (id) => {
    setDocuments((prev) => prev.filter((d) => d.id !== id))
  }

  const activeRef = (() => {
    const idx = conversations.findIndex((c) => c.id === activeConvId)
    return idx >= 0 ? String(idx + 1).padStart(2, '0') : '—'
  })()

  return (
    <div className="flex h-screen bg-brand-bg overflow-hidden relative">
      {/* Background ledger ruling */}
      <div
        className="absolute inset-0 pointer-events-none z-0"
        style={{
          backgroundImage: 'linear-gradient(#CFC4AE 1px, transparent 1px)',
          backgroundSize: '100% 24px',
          opacity: 0.15,
        }}
      ></div>

      <div className="relative z-10 flex h-full w-full">
        <Sidebar
          conversations={conversations}
          activeConvId={activeConvId}
          onSelectConversation={loadConversation}
          onNewConversation={handleNewConversation}
          onConversationDeleted={handleConversationDeleted}
          documents={documents}
          onDocumentUploaded={handleDocumentUploaded}
          onDocumentDeleted={handleDocumentDeleted}
          user={user}
          onLogout={handleLogout}
        />

        {/* Main area */}
        <div className="flex-1 flex flex-col min-w-0 bg-brand-bg">
          {/* Header */}
          <div className="h-16 bg-brand-surface border-b border-brand-line px-6 flex items-center justify-between flex-shrink-0 z-20">
            <div className="flex flex-col min-w-0">
              <div className="text-xs font-mono text-brand-muted uppercase tracking-widest mb-0.5 flex items-center gap-2">
                <span>Case Ledger</span>
                <span className="text-brand-line-2">/</span>
                <span>Ref: {activeRef}</span>
              </div>
              <h1 className="font-serif text-xl text-brand-ink font-semibold truncate">
                {activeConvTitle || 'Select a conversation'}
              </h1>
            </div>
            <div className="flex items-center gap-6 flex-shrink-0">
              {/* Legal-safe badge */}
              <div
                className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 bg-brand-accent/10 text-brand-accent border border-brand-accent/20 text-xs font-semibold"
                title="Responses are grounded in your sources, cited by confidence, and gated for attorney review"
              >
                <ShieldCheck size={14} strokeWidth={2} />
                <span>LEGAL-SAFE</span>
              </div>

              {/* Model selector */}
              <div className="flex items-center bg-brand-surface-2 border border-brand-line p-0.5">
                <button
                  onClick={() => setUsePremium(false)}
                  className={`px-3 py-1 text-xs font-medium transition-all ${
                    !usePremium
                      ? 'bg-brand-surface text-brand-ink shadow-sm border border-brand-line'
                      : 'text-brand-muted hover:text-brand-ink'
                  }`}
                >
                  Standard
                </button>
                <button
                  onClick={() => setUsePremium(true)}
                  className={`px-3 py-1 text-xs font-medium transition-all ${
                    usePremium
                      ? 'bg-brand-surface text-brand-ink shadow-sm border border-brand-line'
                      : 'text-brand-muted hover:text-brand-ink'
                  }`}
                >
                  Premium
                </button>
              </div>

              {/* Public case law toggle */}
              <button
                type="button"
                role="switch"
                aria-checked={includePublic}
                onClick={() => setIncludePublic((v) => !v)}
                className="flex items-center gap-2 cursor-pointer group focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-ink"
              >
                <span
                  className={`relative inline-block w-8 h-4 rounded-full transition-colors ${
                    includePublic ? 'bg-brand-accent' : 'bg-brand-line-2'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-transform ${
                      includePublic ? 'left-[18px]' : 'left-0.5'
                    }`}
                  />
                </span>
                <span className="text-xs font-medium text-brand-ink">Public case law</span>
              </button>

              {/* Admin link */}
              {user?.role === 'admin' && (
                <>
                  <div className="w-px h-6 bg-brand-line"></div>
                  <button
                    onClick={() => navigate('/admin')}
                    className="text-xs font-semibold text-brand-muted hover:text-brand-ink uppercase tracking-wider transition-colors"
                  >
                    Admin
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-8 py-6">
            {!activeConvId && messages.length === 0 && (
              <div className="flex flex-col items-center justify-center min-h-full text-center max-w-2xl mx-auto py-10">
                <div className="w-16 h-16 bg-brand-ink flex items-center justify-center mb-6 relative shadow-sm">
                  <Scale className="w-8 h-8 text-brand-bg" strokeWidth={1.5} />
                  <div className="absolute top-0 left-0 w-full h-1 bg-brand-gold"></div>
                </div>
                <div className="text-xs font-mono text-brand-muted uppercase tracking-widest mb-3">
                  Clarity Legal · Case Ledger
                </div>
                <h3 className="font-serif text-3xl font-semibold text-brand-ink mb-4 tracking-tight">
                  Your legal-safe AI coworker
                </h3>
                <p className="text-brand-ink-2 text-base max-w-lg leading-relaxed font-sans mb-8">
                  Clarity Legal researches, drafts, and analyzes alongside you — grounded in your firm's documents and public case law, with every answer cited and ready for attorney review.
                </p>

                {/* Trust signals */}
                <div className="grid sm:grid-cols-3 gap-3 w-full mb-8">
                  {[
                    { icon: ShieldCheck, title: 'Grounded answers', text: 'Drawn from your documents + public case law' },
                    { icon: BadgeCheck, title: 'Cited & verifiable', text: 'Every claim tagged by confidence level' },
                    { icon: Scale, title: 'Attorney-reviewed', text: 'Work product gated for sign-off' },
                  ].map(({ icon: Icon, title, text }) => (
                    <div
                      key={title}
                      className="flex flex-col items-center text-center gap-2 p-4 bg-brand-surface border border-brand-line relative"
                    >
                      <div className="absolute top-0 left-0 w-full h-px bg-brand-gold/60"></div>
                      <div className="w-9 h-9 bg-brand-surface-2 border border-brand-line flex items-center justify-center text-brand-ink">
                        <Icon size={18} strokeWidth={1.75} />
                      </div>
                      <p className="text-[13px] font-sans font-bold text-brand-ink">{title}</p>
                      <p className="text-[12px] font-sans text-brand-ink-2 leading-snug">{text}</p>
                    </div>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-3 w-full">
                  {[
                    'Summarize the key holdings in Twombly and Iqbal regarding pleading standards',
                    'Draft a demand letter for breach of contract',
                    'What are the elements of promissory estoppel?',
                    'Compare negligence standards across jurisdictions',
                  ].map((prompt, i) => (
                    <button
                      key={prompt}
                      onClick={() => {
                        setInputValue(prompt)
                        textareaRef.current?.focus()
                      }}
                      className="text-left p-4 bg-brand-surface border border-brand-line text-sm hover:border-brand-ink hover:shadow-sm transition-all font-sans leading-relaxed group flex items-start gap-3"
                    >
                      <span className="font-mono text-[10px] text-brand-muted pt-1 shrink-0">
                        {String(i + 1).padStart(2, '0')}
                      </span>
                      <span className="text-brand-ink-2 group-hover:text-brand-ink">{prompt}</span>
                    </button>
                  ))}
                </div>

                {/* Citation legend */}
                <div className="mt-8 w-full">
                  <p className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-3 font-mono">
                    How answers are tagged
                  </p>
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    {[
                      { label: 'settled', text: 'Well-established law', classes: 'bg-brand-green/10 text-brand-green border-brand-green/20' },
                      { label: 'verify', text: 'Confirm before relying', classes: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20' },
                      { label: 'model knowledge', text: 'General reasoning, not a source', classes: 'bg-brand-gold/10 text-brand-gold border-brand-gold/20' },
                    ].map(({ label, text, classes }) => (
                      <div
                        key={label}
                        className="flex items-center gap-2 px-3 py-1.5 bg-brand-surface border border-brand-line"
                      >
                        <span className={`text-[9px] font-bold uppercase tracking-widest font-mono px-1.5 py-0.5 border ${classes}`}>
                          {label}
                        </span>
                        <span className="text-[12px] font-sans text-brand-ink-2">{text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {isLoadingMessages ? (
              <div className="flex justify-center py-12">
                <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
              </div>
            ) : (
              <div className="max-w-4xl mx-auto">
                {messages.map((msg) => (
                  <ChatMessage key={msg.id} message={msg} />
                ))}
                {isSending && <TypingIndicator />}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>

          {/* Input area */}
          <div className="bg-brand-surface border-t border-brand-line px-8 py-4 flex-shrink-0 z-20">
            <div className="max-w-4xl mx-auto flex flex-col gap-2">
              {/* Tag legend */}
              <div className="flex justify-center">
                <div className="bg-brand-surface-2 border border-brand-line px-4 py-1.5 flex items-center gap-6 shadow-sm">
                  <span className="text-[10px] font-mono text-brand-muted uppercase tracking-wider mr-1">Tag Legend:</span>
                  {[
                    { color: 'bg-brand-accent', label: 'SETTLED (Well-established)' },
                    { color: 'bg-brand-amber', label: 'VERIFY (Confirm before relying)' },
                    { color: 'bg-brand-gold', label: 'MODEL (General reasoning)' },
                  ].map(({ color, label }) => (
                    <div key={label} className="flex items-center gap-1.5">
                      <span className={`w-2 h-2 ${color}`}></span>
                      <span className="text-[10px] text-brand-ink-2 font-medium">{label}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="relative flex items-end shadow-sm">
                <div className="absolute left-4 top-4 text-brand-muted pointer-events-none">
                  <FileText className="w-5 h-5" strokeWidth={1.5} />
                </div>
                <textarea
                  ref={textareaRef}
                  value={inputValue}
                  onChange={handleTextareaChange}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask a legal question or drop a document here..."
                  className="w-full resize-none bg-brand-bg border border-brand-ink text-brand-ink px-12 py-4 pr-16 min-h-[56px] max-h-[200px] text-[15px] font-sans focus:outline-none focus:ring-1 focus:ring-brand-ink placeholder-brand-muted leading-relaxed"
                  rows={1}
                  style={{ height: 'auto' }}
                  disabled={isSending}
                />
                <button
                  onClick={handleSend}
                  disabled={!inputValue.trim() || isSending}
                  className="absolute right-3 top-3 p-2 bg-brand-ink text-brand-surface hover:bg-brand-accent disabled:bg-brand-line disabled:text-brand-muted disabled:cursor-not-allowed transition-colors"
                  title="Send message"
                >
                  {isSending ? (
                    <div className="w-4 h-4 border-2 border-brand-surface border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <Send className="w-4 h-4" />
                  )}
                </button>
              </div>
              <p className="text-center text-[10px] text-brand-muted font-mono uppercase tracking-widest">
                Clarity Legal may produce inaccurate information. Always verify citations independently.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
