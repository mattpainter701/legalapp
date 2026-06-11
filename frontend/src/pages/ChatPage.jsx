import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAppShell } from '../components/AppShell'
import ChatHeader from '../components/ChatHeader'
import ChatInput from '../components/ChatInput'
import Messages from '../components/Messages'
import {
  getConversation,
  sendMessage,
  streamMessage,
  createConversation,
  uploadDocument,
} from '../api'

export default function ChatPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { conversations, setConversations, documents, setDocuments, activeConvId, setActiveConvId } = useAppShell()

  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [includePublic, setIncludePublic] = useState(true)
  const [usePremium, setUsePremium] = useState(false)
  const [activeConvTitle, setActiveConvTitle] = useState('')
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
    for (const file of files) {
      try {
        const doc = await uploadDocument(file)
        setDocuments((prev) => {
          const exists = prev.find((d) => d.id === doc.id)
          return exists ? prev : [doc, ...prev]
        })
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
      // AppShell handles this now via context, but we still clear local state
      if (activeConvId === id) {
        setActiveConvId(null)
        setMessages([])
        setActiveConvTitle('')
      }
    },
    [activeConvId, setActiveConvId]
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

    setMessages((prev) => [...prev, userMessage])
    setInputValue('')
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

      for await (const token of streamMessage(convId, content, includePublic, usePremium)) {
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
  }, [inputValue, isSending, activeConvId, includePublic, usePremium, setConversations, setActiveConvId])

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
    <div className="flex flex-col h-full bg-brand-bg relative">
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
          onOpenSidebar={() => {}}
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
  )
}
