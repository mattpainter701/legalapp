import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAppShell } from '../components/AppShell'
import ChatHeader from '../components/ChatHeader'
import ChatInput from '../components/ChatInput'
import Messages from '../components/Messages'
import ChatRail from '../components/chat/ChatRail'
import {
  getConversation,
  sendMessage,
  streamMessage,
  createConversation,
  uploadChatAttachment,
} from '../api'

export default function ChatPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { conversations, setConversations, activeConvId, setActiveConvId, onConversationDeleted } = useAppShell()

  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [includePublic, setIncludePublic] = useState(true)
  const [usePremium, setUsePremium] = useState(false)
  const [activeConvTitle, setActiveConvTitle] = useState('')
  const [pendingAttachments, setPendingAttachments] = useState([])
  const [railOpen, setRailOpen] = useState(false)
  const fileInputRef = useRef(null)

  const handleUploadClick = () => {
    fileInputRef.current?.click()
  }

  const handleFilesSelected = async (e) => {
    const files = e.target.files
    if (!files?.length) return
    await uploadFiles(Array.from(files))
    e.target.value = ''
  }

  const handleDropFiles = async (files) => {
    if (!files?.length) return
    await uploadFiles(files)
  }

  const uploadFiles = async (files) => {
    let convId = activeConvId

    if (!convId) {
      try {
        const conv = await createConversation()
        setConversations((prev) => [conv, ...prev])
        setActiveConvId(conv.id)
        setActiveConvTitle(conv.title || 'New Conversation')
        convId = conv.id
      } catch (err) {
        console.error('Failed to create conversation', err)
        return
      }
    }

    for (const file of files) {
      try {
        const doc = await uploadChatAttachment(convId, file)
        setPendingAttachments((prev) => [...prev, { id: doc.id, filename: doc.filename }])
      } catch (err) {
        console.error('Upload failed:', err)
      }
    }
  }

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
  }, [setActiveConvId])

  // Load first conversation on mount, or from URL param
  useEffect(() => {
    const convId = searchParams.get('conv')
    if (convId) {
      loadConversation(convId)
    } else if (conversations.length > 0 && !activeConvId) {
      loadConversation(conversations[0].id)
    }

    const pending = sessionStorage.getItem('pending_chat_message')
    if (pending) {
      setInputValue(pending)
      sessionStorage.removeItem('pending_chat_message')
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // If conversations loaded after mount and no active conv, pick first
  useEffect(() => {
    if (conversations.length > 0 && !activeConvId && !searchParams.get('conv')) {
      loadConversation(conversations[0].id)
    }
  }, [conversations, activeConvId, searchParams, loadConversation])

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
  }, [setConversations, setActiveConvId])

  const handleConversationDeleted = useCallback(
    (id) => {
      // AppShell context performs the API delete + list mutation;
      // here we additionally clear local thread state if it was active.
      if (activeConvId === id) {
        setActiveConvId(null)
        setMessages([])
        setActiveConvTitle('')
      }
    },
    [activeConvId, setActiveConvId]
  )

  const handleRailDeleteConversation = useCallback(
    (id) => {
      onConversationDeleted(id)
      handleConversationDeleted(id)
    },
    [onConversationDeleted, handleConversationDeleted]
  )

  const handleRailSelectConversation = useCallback(
    (id) => {
      loadConversation(id)
      setRailOpen(false)
    },
    [loadConversation]
  )

  const handleSend = useCallback(async () => {
    const content = inputValue.trim()
    if (!content || isSending) return

    let convId = activeConvId

    if (!convId) {
      try {
        const conv = await createConversation(content.slice(0, 60))
        setConversations((prev) => [conv, ...prev])
        setActiveConvId(conv.id)
        setActiveConvTitle(conv.title || 'New Conversation')
        convId = conv.id
        // Brief pause to let DB commit settle before the streaming read
        await new Promise(r => setTimeout(r, 150))
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

    const attachmentIds = pendingAttachments.map((a) => a.id)

    setMessages((prev) => [...prev, userMessage])
    setInputValue('')
    setPendingAttachments([])
    setIsSending(true)

    try {
      const assistantMsgId = `stream-${Date.now()}`
      const assistantMsg = {
        id: assistantMsgId,
        role: 'assistant',
        content: '',
        sources: [],
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, assistantMsg])

      let accumulatedText = ''
      let streamError = null

      for await (const token of streamMessage(convId, content, includePublic, usePremium, attachmentIds)) {
        if (token === '[STREAM_COMPLETE]') {
          break
        } else if (token.startsWith('[ERROR]')) {
          streamError = token.slice(7)
          break
        } else {
          accumulatedText += token
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? { ...msg, content: accumulatedText }
                : msg
            )
          )
        }
      }

      if (streamError) {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMsgId
              ? { ...msg, content: `An error occurred: ${streamError}` }
              : msg
          )
        )
      }

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
  }, [inputValue, isSending, activeConvId, includePublic, usePremium, pendingAttachments, setConversations, setActiveConvId])

  const handleExportConversation = () => {
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
    console.log('Search messages')
  }

  const activeRef = (() => {
    const idx = conversations.findIndex((c) => c.id === activeConvId)
    return idx >= 0 ? String(idx + 1).padStart(2, '0') : '—'
  })()

  return (
    <div className="flex h-full bg-brand-bg">
      {/* Desktop rail */}
      <ChatRail
        className="hidden lg:flex w-[300px] flex-shrink-0 border-r border-brand-line h-full"
        onNewConversation={handleNewConversation}
        onSelectConversation={handleRailSelectConversation}
        onDeleteConversation={handleRailDeleteConversation}
      />

      {/* Mobile rail drawer */}
      <div
        className={`fixed inset-0 bg-black/40 z-30 lg:hidden transition-opacity duration-300 ${railOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
        onClick={() => setRailOpen(false)}
        aria-hidden="true"
      />
      <ChatRail
        className={`fixed inset-y-0 left-0 z-40 w-[300px] border-r border-brand-line lg:hidden transition-transform duration-300 ease-in-out ${railOpen ? 'sidebar-visible' : 'sidebar-hidden'}`}
        onNewConversation={() => { handleNewConversation(); setRailOpen(false) }}
        onSelectConversation={handleRailSelectConversation}
        onDeleteConversation={handleRailDeleteConversation}
        onClose={() => setRailOpen(false)}
      />

      {/* Thread column */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* Background ledger ruling */}
        <div
          className="absolute inset-0 pointer-events-none z-0"
          style={{
            backgroundImage: 'linear-gradient(#CFC4AE 1px, transparent 1px)',
            backgroundSize: '100% 24px',
            opacity: 0.15,
          }}
        />

        <div className="relative z-10 flex flex-col h-full">
          <ChatHeader
            activeRef={activeRef}
            activeConvTitle={activeConvTitle}
            usePremium={usePremium}
            setUsePremium={setUsePremium}
            includePublic={includePublic}
            setIncludePublic={setIncludePublic}
            user={null}
            onExportConversation={handleExportConversation}
            onSearchMessages={handleSearchMessages}
            onOpenSidebar={() => setRailOpen(true)}
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
            onUploadClick={handleUploadClick}
            onDropFiles={handleDropFiles}
            isSending={isSending}
            disabled={false}
            pendingAttachments={pendingAttachments}
            onRemoveAttachment={(id) =>
              setPendingAttachments((prev) => prev.filter((a) => a.id !== id))
            }
          />
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.txt"
            multiple
            className="hidden"
            onChange={handleFilesSelected}
          />
        </div>
      </div>
    </div>
  )
}
