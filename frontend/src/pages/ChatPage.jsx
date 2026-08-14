import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAppShell } from '../components/AppShell'
import ChatHeader from '../components/ChatHeader'
import ChatInput from '../components/ChatInput'
import Messages from '../components/Messages'
import ChatRail from '../components/chat/ChatRail'
import {
  getConversation,
  streamMessage,
  createConversation,
  updateConversation,
  uploadChatAttachment,
  getMattersV2,
  getLegalSourceHealth,
} from '../api'
import { AlertBanner } from '../components/ui'
import { Briefcase, ChevronDown, ExternalLink, Link2, Search, Unlink } from 'lucide-react'

function mergeRefreshedTranscript(serverMessages, optimisticUserMessage, fallbackAssistantMessage) {
  const next = Array.isArray(serverMessages) ? [...serverMessages] : []
  const submittedAt = Date.parse(optimisticUserMessage.created_at)
  const hasSubmittedQuestion = next.some(
    (msg) => msg.role === 'user' && msg.content === optimisticUserMessage.content
  )
  const hasFreshAssistant = next.some((msg) => {
    if (msg.role !== 'assistant') return false
    const createdAt = Date.parse(msg.created_at)
    return Number.isNaN(submittedAt) || Number.isNaN(createdAt) || createdAt >= submittedAt
  })

  if (!hasSubmittedQuestion) {
    const assistantIndex = hasFreshAssistant
      ? next.findIndex((msg) => {
          if (msg.role !== 'assistant') return false
          const createdAt = Date.parse(msg.created_at)
          return Number.isNaN(submittedAt) || Number.isNaN(createdAt) || createdAt >= submittedAt
        })
      : -1
    next.splice(assistantIndex >= 0 ? assistantIndex : next.length, 0, optimisticUserMessage)
  }

  if (!hasFreshAssistant && fallbackAssistantMessage?.content) {
    next.push(fallbackAssistantMessage)
  } else if (hasFreshAssistant && fallbackAssistantMessage?.progress) {
    const assistantIndex = next.findIndex((msg) => {
      if (msg.role !== 'assistant') return false
      const createdAt = Date.parse(msg.created_at)
      return Number.isNaN(submittedAt) || Number.isNaN(createdAt) || createdAt >= submittedAt
    })
    if (assistantIndex >= 0 && !next[assistantIndex].progress) {
      next[assistantIndex] = {
        ...next[assistantIndex],
        progress: fallbackAssistantMessage.progress,
      }
    }
  }

  return attachTurnReferences(next)
}

function deriveKeyphrases(text) {
  const stopWords = new Set([
    'about', 'after', 'before', 'case', 'cases', 'could', 'from', 'have',
    'legal', 'need', 'that', 'their', 'there', 'this', 'what', 'when',
    'where', 'which', 'with', 'would',
  ])
  return String(text || '')
    .split(/[^A-Za-z0-9]+/)
    .map((word) => word.trim())
    .filter((word) => word && (word.length > 2 || /^[A-Z]{2}$/.test(word)))
    .filter((word) => !stopWords.has(word.toLowerCase()))
    .slice(0, 5)
}

function initialStreamProgress(content, attachmentCount) {
  const uploads = Number.isFinite(attachmentCount) ? attachmentCount : 0
  return {
    type: 'progress',
    event: 'retrieving',
    status: 'Retrieving source material',
    keyphrases: deriveKeyphrases(content),
    counts: {
      matter: 0,
      uploads,
      firm: 0,
      courtlistener: 0,
      total: uploads,
    },
    activities: [],
  }
}

function mergeStreamProgress(current, event, content) {
  if (!event || event.type !== 'progress') return current
  const counts = {
    ...(current?.counts || {}),
    ...(event.counts || {}),
  }
  const activities = [...(current?.activities || [])]
  if (event.activity?.id) {
    const activityIndex = activities.findIndex((item) => item.id === event.activity.id)
    if (activityIndex >= 0) {
      activities[activityIndex] = {
        ...activities[activityIndex],
        ...event.activity,
        sources: event.activity.sources || activities[activityIndex].sources || [],
      }
    } else {
      activities.push(event.activity)
    }
  }
  return {
    ...(current || {}),
    ...event,
    counts,
    activities,
    keyphrases: event.keyphrases || current?.keyphrases || deriveKeyphrases(content),
  }
}

function countSourcesByType(sources) {
  const counts = {
    matter: 0,
    uploads: 0,
    firm: 0,
    courtlistener: 0,
    total: 0,
  }

  for (const src of Array.isArray(sources) ? sources : []) {
    const type = src?.source_type || ''
    if (type === 'public_authority') {
      counts.courtlistener += 1
    } else if (type === 'matter_context') {
      counts.matter += 1
    } else {
      counts.firm += 1
    }
  }
  counts.total = counts.matter + counts.uploads + counts.firm + counts.courtlistener
  return counts
}

function buildReferenceContext({ progress, sources, status } = {}) {
  const sourceList = Array.isArray(sources) ? sources : []
  const progressCounts = progress?.counts || null
  const derivedCounts = countSourcesByType(sourceList)
  const counts = {
    matter: Number(progressCounts?.matter ?? derivedCounts.matter ?? 0),
    uploads: Number(progressCounts?.uploads ?? derivedCounts.uploads ?? 0),
    firm: Number(progressCounts?.firm ?? derivedCounts.firm ?? 0),
    courtlistener: Number(progressCounts?.courtlistener ?? derivedCounts.courtlistener ?? 0),
  }
  counts.total = Number(progressCounts?.total ?? (counts.matter + counts.uploads + counts.firm + counts.courtlistener))
  const hasContext = counts.total > 0 || sourceList.length > 0 || progress?.status || status
  if (!hasContext) return null

  return {
    counts,
    source_count: sourceList.length,
    status: status || progress?.status || (sourceList.length ? 'Sources attached to answer' : ''),
    complete: Boolean(progress?.complete),
  }
}

function attachTurnReferences(messages) {
  const next = (Array.isArray(messages) ? messages : []).map((msg) => ({ ...msg }))

  for (let i = 0; i < next.length; i += 1) {
    if (next[i].role !== 'user') continue
    let assistantIndex = -1
    for (let idx = i + 1; idx < next.length; idx += 1) {
      if (next[idx].role === 'user') break
      if (next[idx].role === 'assistant') {
        assistantIndex = idx
        break
      }
    }
    if (assistantIndex < 0) continue

    const assistant = next[assistantIndex]
    const context = buildReferenceContext({
      progress: assistant.progress,
      sources: assistant.sources,
    })
    if (!context) continue

    next[i] = {
      ...next[i],
      referenceContext: next[i].referenceContext || context,
    }
    next[assistantIndex] = {
      ...assistant,
      referenceContext: assistant.referenceContext || context,
    }
  }

  return next
}

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
  const [matters, setMatters] = useState([])
  const [matterQuery, setMatterQuery] = useState('')
  const [matterPickerOpen, setMatterPickerOpen] = useState(false)
  const [matterLinking, setMatterLinking] = useState(false)
  const [pendingAttachments, setPendingAttachments] = useState([])
  const [railOpen, setRailOpen] = useState(false)
  const [notice, setNotice] = useState(null)
  const [sourceHealth, setSourceHealth] = useState(null)
  const fileInputRef = useRef(null)
  const matterPickerRef = useRef(null)

  const showErrorNotice = useCallback((title, fallback, err) => {
    setNotice({
      type: 'error',
      title,
      message: err?.response?.data?.detail || err?.message || fallback,
    })
  }, [])

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
        showErrorNotice('Conversation could not be created', 'Start a new conversation and try again.', err)
        return
      }
    }

    for (const file of files) {
      try {
        const doc = await uploadChatAttachment(convId, file)
        setPendingAttachments((prev) => [...prev, { id: doc.id, filename: doc.filename }])
      } catch (err) {
        console.error('Upload failed:', err)
        showErrorNotice('Attachment upload failed', `${file.name} could not be uploaded.`, err)
      }
    }
  }

  const loadConversation = useCallback(async (id) => {
    setIsLoadingMessages(true)
    setActiveConvId(id)
    try {
      const data = await getConversation(id)
      setMessages(attachTurnReferences(data.messages || []))
      setActiveConvTitle(data.conversation?.title || 'Untitled')
      if (data.conversation) {
        setConversations((prev) =>
          prev.some((conv) => conv.id === data.conversation.id)
            ? prev.map((conv) => (conv.id === data.conversation.id ? { ...conv, ...data.conversation } : conv))
            : [data.conversation, ...prev]
        )
      }
    } catch (err) {
      console.error('Failed to load conversation', err)
      const status = err?.response?.status
      if (status === 403 || status === 404) {
        setConversations((prev) => prev.filter((conv) => conv.id !== id))
        setActiveConvId(null)
        setMessages([])
        setActiveConvTitle('')
        navigate('/chat', { replace: true })
      }
      showErrorNotice('Conversation could not be loaded', 'Select another conversation or retry.', err)
    } finally {
      setIsLoadingMessages(false)
    }
  }, [navigate, setActiveConvId, setConversations, showErrorNotice])

  useEffect(() => {
    getMattersV2({ page_size: 200, sort_by: 'updated_at', sort_dir: 'desc' })
      .then((data) => setMatters(Array.isArray(data) ? data : (data.items || [])))
      .catch(() => setMatters([]))
    getLegalSourceHealth()
      .then(setSourceHealth)
      .catch(() => setSourceHealth({ available: false, status: 'unavailable', sources: [], partitions: [] }))
  }, [])

  useEffect(() => {
    if (!matterPickerOpen) return undefined

    const handlePointerDown = (event) => {
      if (!matterPickerRef.current?.contains(event.target)) setMatterPickerOpen(false)
    }
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setMatterPickerOpen(false)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [matterPickerOpen])

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
  }, [])

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
      setNotice(null)
    } catch (err) {
      console.error('Failed to create conversation', err)
      showErrorNotice('Conversation could not be created', 'Please try again.', err)
    }
  }, [setConversations, setActiveConvId, showErrorNotice])

  const handleConversationDeleted = useCallback(
    (id) => {
      // AppShell context performs the API delete + list mutation;
      // here we additionally clear local thread state if it was active.
      if (activeConvId === id) {
        setActiveConvId(null)
        setMessages([])
        setActiveConvTitle('')
        navigate('/chat', { replace: true })
      }
    },
    [activeConvId, navigate, setActiveConvId]
  )

  const handleRailDeleteConversation = useCallback(
    async (id) => {
      try {
        const deleted = await onConversationDeleted(id)
        if (deleted === false) return
      } catch (err) {
        const status = err?.response?.status
        if (status !== 403 && status !== 404) {
          showErrorNotice('Conversation could not be deleted', 'Please try again.', err)
          return
        }
        setConversations((prev) => prev.filter((conv) => conv.id !== id))
      }
      handleConversationDeleted(id)
    },
    [onConversationDeleted, handleConversationDeleted, setConversations, showErrorNotice]
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
        showErrorNotice('Conversation could not be created', 'Your message was not sent. Try again after starting a new conversation.', err)
        return
      }
    }

    const attachmentIds = pendingAttachments.map((a) => a.id)
    let streamProgress = initialStreamProgress(content, attachmentIds.length)
    const initialReferenceContext = buildReferenceContext({ progress: streamProgress })

    const userMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      sources: [],
      referenceContext: initialReferenceContext,
      created_at: new Date().toISOString(),
    }

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
        progress: streamProgress,
        referenceContext: initialReferenceContext,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, assistantMsg])

      let accumulatedText = ''
      let streamError = null

      for await (const token of streamMessage(convId, content, includePublic, usePremium, attachmentIds)) {
        if (token?.type === 'progress' && token.event === 'action_proposal') {
          // Reviewable work the assistant proposed. Attached to the message
          // rather than merged into progress, since it outlives the stream.
          const proposals = token.proposed_actions || []
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? { ...msg, proposed_actions: proposals }
                : msg
            )
          )
          continue
        }
        if (token?.type === 'progress') {
          streamProgress = mergeStreamProgress(streamProgress, token, content)
          const referenceContext = buildReferenceContext({ progress: streamProgress })
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? { ...msg, progress: streamProgress, referenceContext }
                : msg.id === userMessage.id
                  ? { ...msg, referenceContext }
                : msg
            )
          )
          continue
        }
        if (token === '[STREAM_COMPLETE]') {
          streamProgress = { ...streamProgress, complete: true, status: 'Response complete' }
          const referenceContext = buildReferenceContext({ progress: streamProgress })
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? { ...msg, progress: streamProgress, referenceContext }
                : msg.id === userMessage.id
                  ? { ...msg, referenceContext }
                : msg
            )
          )
          break
        } else if (typeof token === 'string' && token.startsWith('[ERROR]')) {
          streamError = token.slice(7)
          break
        } else if (typeof token === 'string') {
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
              ? {
                  ...msg,
                  content: `An error occurred: ${streamError}`,
                  progress: { ...streamProgress, complete: true },
                  referenceContext: buildReferenceContext({ progress: { ...streamProgress, complete: true } }),
                }
              : msg.id === userMessage.id
                ? { ...msg, referenceContext: buildReferenceContext({ progress: { ...streamProgress, complete: true } }) }
              : msg
          )
        )
        setNotice({
          type: 'error',
          title: 'Response could not be completed',
          message: streamError || 'The assistant stopped before finishing the response.',
        })
      } else {
        try {
          const refreshed = await getConversation(convId)
          setMessages(
            mergeRefreshedTranscript(refreshed.messages, userMessage, {
              ...assistantMsg,
              content: accumulatedText,
              progress: { ...streamProgress, complete: true },
              referenceContext: buildReferenceContext({ progress: { ...streamProgress, complete: true } }),
            })
          )
          setActiveConvTitle(refreshed.conversation?.title || activeConvTitle)
          if (refreshed.conversation) {
            setConversations((prev) =>
              prev.some((conv) => conv.id === refreshed.conversation.id)
                ? prev.map((conv) => (conv.id === refreshed.conversation.id ? { ...conv, ...refreshed.conversation } : conv))
                : [refreshed.conversation, ...prev]
            )
          }
        } catch (refreshErr) {
          console.error('Failed to refresh streamed message metadata', refreshErr)
        }
      }

      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId ? { ...c, updated_at: new Date().toISOString() } : c
        )
      )
    } catch (err) {
      console.error('Failed to send message', err)
      showErrorNotice('Message could not be sent', 'Please try again.', err)
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
  }, [inputValue, isSending, activeConvId, includePublic, usePremium, pendingAttachments, setConversations, setActiveConvId, activeConvTitle, showErrorNotice])

  const handleExportConversation = () => {
    if (messages.length === 0) {
      setNotice({
        type: 'info',
        title: 'Nothing to export',
        message: 'Start or select a conversation before exporting.',
      })
      return
    }

    const content = messages
      .map((msg) => `**${msg.role === 'user' ? 'You' : 'LawHand'}:**\n\n${msg.content}`)
      .join('\n\n---\n\n')

    const element = document.createElement('a')
    element.setAttribute('href', 'data:text/markdown;charset=utf-8,' + encodeURIComponent(content))
    element.setAttribute('download', `conversation-${activeConvId}.md`)
    element.style.display = 'none'
    document.body.appendChild(element)
    element.click()
    document.body.removeChild(element)
  }

  const handleRenameConversation = useCallback(async (title) => {
    if (!activeConvId) {
      throw new Error('Select a conversation before renaming it.')
    }
    const updated = await updateConversation(activeConvId, { title })
    setActiveConvTitle(updated.title || title)
    setConversations((prev) =>
      prev.map((conv) => (conv.id === updated.id ? { ...conv, ...updated } : conv))
    )
    return updated
  }, [activeConvId, setConversations])

  const activeConversation = conversations.find((conv) => conv.id === activeConvId) || null
  const linkedMatterId = activeConversation?.matter_id || null
  const linkedMatter = matters.find((matter) => matter.id === linkedMatterId) || null
  const linkedMatterName = linkedMatter?.matter_name || linkedMatter?.name || (linkedMatterId ? 'Linked matter' : '')
  const filteredMatters = matters
    .filter((matter) => {
      const q = matterQuery.trim().toLowerCase()
      if (!q) return true
      return [matter.matter_name, matter.name, matter.case_number, matter.client_name]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q))
    })
    .slice(0, 20)

  const applyMatterLink = useCallback(async (matterId) => {
    if (!activeConvId) {
      setNotice({ type: 'info', title: 'No conversation selected', message: 'Start or select a conversation before linking it to a matter.' })
      return
    }
    setMatterLinking(true)
    try {
      const updated = await updateConversation(activeConvId, { matter_id: matterId || '' })
      setConversations((prev) =>
        prev.map((conv) => (conv.id === updated.id ? { ...conv, ...updated } : conv))
      )
      setMatterPickerOpen(false)
      setMatterQuery('')
      setNotice({
        type: 'success',
        title: matterId ? 'Matter linked' : 'Matter unlinked',
        message: matterId ? 'Future messages in this conversation will use that matter context.' : 'This conversation is no longer tied to a matter.',
      })
    } catch (err) {
      showErrorNotice('Matter link failed', 'The conversation could not be updated.', err)
    } finally {
      setMatterLinking(false)
    }
  }, [activeConvId, setConversations, showErrorNotice])

  const activeRef = (() => {
    const idx = conversations.findIndex((c) => c.id === activeConvId)
    return idx >= 0 ? String(idx + 1).padStart(2, '0') : '—'
  })()

  return (
    <div className="flex h-full bg-brand-bg">
      {/* Desktop rail */}
      <ChatRail
        className="hidden lg:flex w-[300px] flex-shrink-0 border-r border-brand-line h-full"
        isOpen
        onNewConversation={handleNewConversation}
        onSelectConversation={handleRailSelectConversation}
        onDeleteConversation={handleRailDeleteConversation}
        sourceHealth={sourceHealth}
      />

      {/* Mobile rail drawer */}
      <div
        className={`fixed inset-0 z-30 bg-brand-ink/45 backdrop-blur-[2px] transition-opacity duration-300 lg:hidden ${railOpen ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0'}`}
        onClick={() => setRailOpen(false)}
        aria-hidden="true"
      />
      <ChatRail
        className={`fixed inset-y-0 left-0 z-40 w-[min(340px,calc(100vw-1rem))] rounded-r-2xl border-r border-brand-line shadow-2xl transition-transform duration-300 ease-in-out lg:hidden ${railOpen ? 'sidebar-visible' : 'sidebar-hidden'}`}
        isOpen={railOpen}
        onNewConversation={() => { handleNewConversation(); setRailOpen(false) }}
        onSelectConversation={handleRailSelectConversation}
        onDeleteConversation={handleRailDeleteConversation}
        sourceHealth={sourceHealth}
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
            onExportConversation={handleExportConversation}
            onRenameConversation={handleRenameConversation}
            onRenameError={(message) => setNotice({ type: 'error', title: 'Rename failed', message })}
            onOpenSidebar={() => setRailOpen(true)}
          />

          <div className="relative px-2 pt-2 sm:px-4 sm:pt-3 md:px-6" ref={matterPickerRef}>
            <div className="flex min-h-10 items-center gap-2 rounded-xl border border-brand-line bg-brand-surface/95 px-2 py-1.5 shadow-sm sm:gap-3 sm:rounded-2xl sm:px-3 sm:py-2.5">
              <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg sm:h-9 sm:w-9 sm:rounded-xl ${
                linkedMatterId ? 'bg-brand-accent/10 text-brand-accent-2' : 'bg-brand-bg-soft text-brand-muted'
              }`}>
                <Briefcase size={17} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="hidden text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted sm:block">Working context</p>
                <p className={`truncate text-xs font-semibold sm:mt-0.5 sm:text-sm ${linkedMatterId ? 'text-brand-ink' : 'text-brand-muted'}`}>
                  {linkedMatterId
                    ? linkedMatterName
                    : activeConvId
                      ? 'No matter linked'
                      : 'Start a conversation to link a matter'}
                  {linkedMatter?.case_number ? (
                    <span className="ml-2 font-normal text-brand-muted">{linkedMatter.case_number}</span>
                  ) : null}
                </p>
              </div>
              {linkedMatterId && (
                <button
                  type="button"
                  onClick={() => navigate(`/matters/${linkedMatterId}`)}
                  className="hidden tap-target rounded-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink sm:inline-flex"
                  aria-label={`Open ${linkedMatterName}`}
                  title="Open linked matter"
                >
                  <ExternalLink size={16} />
                </button>
              )}
              <button
                type="button"
                onClick={() => setMatterPickerOpen((open) => !open)}
                disabled={!activeConvId || matterLinking}
                aria-expanded={matterPickerOpen}
                aria-haspopup="dialog"
                className="inline-flex min-h-8 items-center gap-1 rounded-lg border border-brand-line bg-brand-surface px-2 text-xs font-semibold text-brand-ink hover:bg-brand-bg-soft disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-10 sm:gap-2 sm:rounded-xl sm:px-3"
              >
                <Link2 size={14} className="hidden sm:block" />
                <span className="hidden sm:inline">{linkedMatterId ? 'Change' : 'Link matter'}</span>
                <ChevronDown size={13} />
              </button>
            </div>

            {matterPickerOpen && (
              <div
                role="dialog"
                aria-label="Choose matter context"
                className="absolute right-3 top-[calc(100%+0.5rem)] z-20 w-[min(430px,calc(100vw-1.5rem))] overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-xl sm:right-4 md:right-6"
              >
                <div className="border-b border-brand-line p-3">
                  <p className="mb-2 text-xs font-semibold text-brand-ink">Choose matter context</p>
                  <div className="relative">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted" />
                    <input
                      value={matterQuery}
                      onChange={(event) => setMatterQuery(event.target.value)}
                      aria-label="Search matters"
                      placeholder="Search by matter, client, or case number"
                      className="w-full rounded-xl border border-brand-line bg-brand-bg py-2.5 pl-9 pr-3 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent"
                      autoFocus
                    />
                  </div>
                </div>
                <div className="max-h-72 overflow-y-auto">
                  {filteredMatters.length === 0 ? (
                    <p className="px-4 py-6 text-center text-sm text-brand-muted">No matters found.</p>
                  ) : filteredMatters.map((matter) => (
                    <button
                      key={matter.id}
                      type="button"
                      onClick={() => applyMatterLink(matter.id)}
                      disabled={matterLinking}
                      className="block w-full border-b border-brand-line px-4 py-3 text-left last:border-0 hover:bg-brand-bg-soft disabled:opacity-50"
                    >
                      <span className="block truncate text-sm font-semibold text-brand-ink">{matter.matter_name || matter.name || 'Untitled matter'}</span>
                      <span className="mt-0.5 block truncate text-xs text-brand-muted">
                        {[matter.case_number, matter.client_name, matter.status].filter(Boolean).join(' · ') || 'Matter'}
                      </span>
                    </button>
                  ))}
                </div>
                {linkedMatterId && (
                  <div className="border-t border-brand-line p-2">
                    <button
                      type="button"
                      onClick={() => applyMatterLink('')}
                      disabled={matterLinking}
                      className="flex min-h-10 w-full items-center gap-2 rounded-xl px-3 text-sm font-medium text-brand-muted hover:bg-brand-rose/10 hover:text-brand-rose disabled:opacity-50"
                    >
                      <Unlink size={15} /> Remove matter context
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {notice && (
            <div className="px-3 pt-3 sm:px-4 md:px-6">
              <AlertBanner
                type={notice.type}
                title={notice.title}
                onDismiss={() => setNotice(null)}
              >
                {notice.message}
              </AlertBanner>
            </div>
          )}

          <Messages
            messages={messages}
            isLoading={isLoadingMessages}
            isSending={isSending}
            onPromptSelect={(prompt) => setInputValue(prompt)}
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
