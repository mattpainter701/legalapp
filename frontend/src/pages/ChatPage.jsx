import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import Sidebar from '../components/Sidebar'
import ChatMessage from '../components/ChatMessage'
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
    <div className="flex justify-start mb-4">
      <div className="max-w-[80%]">
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
          <span className="text-xs text-gray-500 font-sans">Clarity Legal</span>
        </div>
        <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm inline-block">
          <div className="flex gap-1.5 items-center h-4">
            <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
            <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
            <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
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

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
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
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="font-serif font-semibold text-[#1e3a5f] truncate">
              {activeConvTitle || 'Select a conversation'}
            </h2>
          </div>
          <div className="flex items-center gap-4 flex-shrink-0">
            {/* Model selector */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500 font-sans">Model:</span>
              <button
                onClick={() => setUsePremium(false)}
                className={`px-2.5 py-1 text-xs rounded font-sans transition-colors ${
                  !usePremium
                    ? 'bg-[#1e3a5f] text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                DeepSeek
              </button>
              <button
                onClick={() => setUsePremium(true)}
                className={`px-2.5 py-1 text-xs rounded font-sans transition-colors ${
                  usePremium
                    ? 'bg-[#1e3a5f] text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                Claude (Premium)
              </button>
            </div>

            {/* Public case law toggle */}
            <label className="flex items-center gap-2 cursor-pointer">
              <span className="text-xs text-gray-500 font-sans">Public case law</span>
              <div
                className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer ${
                  includePublic ? 'bg-[#1e3a5f]' : 'bg-gray-300'
                }`}
                onClick={() => setIncludePublic((v) => !v)}
              >
                <div
                  className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                    includePublic ? 'translate-x-4' : 'translate-x-0.5'
                  }`}
                />
              </div>
            </label>

            {/* Admin link */}
            {user?.role === 'admin' && (
              <button
                onClick={() => navigate('/admin')}
                className="text-xs text-[#1e3a5f] hover:underline font-sans"
              >
                Admin
              </button>
            )}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {!activeConvId && messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="w-16 h-16 bg-[#1e3a5f] rounded-full flex items-center justify-center mb-4">
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
                  <path
                    d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z"
                    fill="white"
                    fillOpacity="0.9"
                  />
                  <path
                    d="M13 15l2 2 4-4"
                    stroke="#1e3a5f"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <h3 className="font-serif text-xl font-semibold text-[#1e3a5f] mb-2">
                Welcome to Clarity Legal
              </h3>
              <p className="text-gray-500 text-sm max-w-md leading-relaxed">
                Ask a legal research question, request a document draft, or analyze a case.
                Start a new conversation or select an existing one.
              </p>
              <div className="mt-6 grid grid-cols-2 gap-3 max-w-md w-full">
                {[
                  'Summarize the key holdings in Twombly and Iqbal regarding pleading standards',
                  'Draft a demand letter for breach of contract',
                  'What are the elements of promissory estoppel?',
                  'Compare negligence standards across jurisdictions',
                ].map((prompt) => (
                  <button
                    key={prompt}
                    onClick={() => {
                      setInputValue(prompt)
                      textareaRef.current?.focus()
                    }}
                    className="text-left p-3 bg-white border border-gray-200 rounded-lg text-xs text-gray-600 hover:border-[#1e3a5f] hover:bg-blue-50 transition-colors font-sans leading-relaxed"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}

          {isLoadingMessages ? (
            <div className="flex justify-center py-8">
              <div className="w-6 h-6 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
            </div>
          ) : (
            <>
              {messages.map((msg) => (
                <ChatMessage key={msg.id} message={msg} />
              ))}
              {isSending && <TypingIndicator />}
            </>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="bg-white border-t border-gray-200 px-6 py-4 flex-shrink-0">
          <div className="flex gap-3 items-end">
            <textarea
              ref={textareaRef}
              value={inputValue}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              placeholder="Ask a legal question... (Enter to send, Shift+Enter for new line)"
              className="flex-1 resize-none border border-gray-300 rounded-xl px-4 py-3 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400 max-h-48 min-h-[48px]"
              rows={1}
              style={{ height: 'auto' }}
              disabled={isSending}
            />
            <button
              onClick={handleSend}
              disabled={!inputValue.trim() || isSending}
              className="flex-shrink-0 w-10 h-10 rounded-xl bg-[#1e3a5f] text-white flex items-center justify-center hover:bg-[#2e4f7a] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              title="Send message"
            >
              {isSending ? (
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                  />
                </svg>
              )}
            </button>
          </div>
          <p className="mt-2 text-xs text-gray-400 font-sans">
            Clarity Legal may produce inaccurate information. Always verify citations independently.
          </p>
        </div>
      </div>
    </div>
  )
}
