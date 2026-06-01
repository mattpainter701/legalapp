import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import Sidebar from '../components/Sidebar'
import ChatHeader from '../components/ChatHeader'
import ChatInput from '../components/ChatInput'
import Messages from '../components/Messages'
import {
  getConversations,
  createConversation,
  getConversation,
  sendMessage,
  getDocuments,
  logout,
} from '../api'

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

  // Load conversations and documents on mount
  useEffect(() => {
    Promise.all([getConversations(), getDocuments()])
      .then(([convs, docs]) => {
        setConversations(convs)
        setDocuments(Array.isArray(docs) ? docs : docs?.documents || [])
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

  const handleExportConversation = () => {
    // Export as Markdown for now
    const content = messages
      .map((msg) => `**${msg.role === 'user' ? 'You' : 'Clarity Legal'}:**\n\n${msg.content}`)
      .join('\n\n---\n\n')

    const element = document.createElement('a')
    element.setAttribute('href', 'data:text/markdown;charset=utf-8,' + encodeURIComponent(content))
    element.setAttribute('download', `conversation-${activeConvId}.md`)
    element.style.display = 'none'
    document.body.appendChild(element)
    element.click()
    document.body.removeChild(element)
  }

  const handleSearchMessages = () => {
    // TODO: Implement message search
    console.log('Search messages')
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
          <ChatHeader
            activeRef={activeRef}
            activeConvTitle={activeConvTitle}
            usePremium={usePremium}
            setUsePremium={setUsePremium}
            includePublic={includePublic}
            setIncludePublic={setIncludePublic}
            user={user}
            onExportConversation={handleExportConversation}
            onSearchMessages={handleSearchMessages}
          />

          <Messages
            messages={messages}
            isLoading={isLoadingMessages}
            isSending={isSending}
          />

          <ChatInput
            inputValue={inputValue}
            onInputChange={setInputValue}
            onSend={handleSend}
            isSending={isSending}
            disabled={false}
          />
        </div>
      </div>
    </div>
  )
}
