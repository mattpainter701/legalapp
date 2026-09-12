import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { reportError } from '../utils/reportError'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAppShell } from '../components/AppShell'
import { useAuth } from '../App'
import ChatHeader from '../components/ChatHeader'
import ChatInput from '../components/ChatInput'
import { citedSourceCount } from '../components/ChatMessage'
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
  updateMe,
} from '../api'
import {
  GENERATION_ABORTED,
  GENERATION_COMPLETE,
  GENERATION_ERROR,
  GENERATION_STREAMING,
  abortChatGeneration,
  beginChatGeneration,
  countStreamingChatGenerations,
  getChatGeneration,
  patchChatGeneration,
  releaseChatGeneration,
  settleChatGeneration,
  subscribeToChatGenerations,
} from '../chatGenerations'
import { AlertBanner } from '../components/ui'
import { Briefcase, ChevronDown, ExternalLink, Link2, Search, Unlink } from 'lucide-react'

const MESSAGE_IDENTITY_KEYS = [
  'id',
  'client_message_id',
  'clientMessageId',
  'optimistic_id',
  'optimisticId',
]
const TURN_IDENTITY_KEYS = [
  'client_turn_id',
  'clientTurnId',
  'turn_id',
  'turnId',
  'client_request_id',
  'clientRequestId',
  'request_id',
  'requestId',
]
const REPLY_IDENTITY_KEYS = [
  'parent_message_id',
  'parentMessageId',
  'user_message_id',
  'userMessageId',
  'in_reply_to',
  'inReplyTo',
]

function identityValues(message, keys) {
  return new Set(
    keys
      .map((key) => message?.[key])
      .filter((value) => value !== undefined && value !== null && String(value).trim())
      .map(String),
  )
}

function sharesIdentity(left, right, keys) {
  const leftValues = identityValues(left, keys)
  if (leftValues.size === 0) return false
  return [...identityValues(right, keys)].some((value) => leftValues.has(value))
}

function assistantRepliesTo(assistant, user) {
  const userIds = identityValues(user, [...MESSAGE_IDENTITY_KEYS, ...TURN_IDENTITY_KEYS])
  return [...identityValues(assistant, REPLY_IDENTITY_KEYS)].some((value) => userIds.has(value))
}

export function mergeRefreshedTranscript(serverMessages, optimisticUserMessage, fallbackAssistantMessage) {
  const next = Array.isArray(serverMessages) ? [...serverMessages] : []
  const knownServerIds = new Set(
    (optimisticUserMessage?._known_server_message_ids || []).map(String),
  )

  let userIndex = next.findIndex((message) => (
    message.role === 'user'
    && (
      sharesIdentity(message, optimisticUserMessage, MESSAGE_IDENTITY_KEYS)
      || sharesIdentity(message, optimisticUserMessage, TURN_IDENTITY_KEYS)
    )
  ))

  if (userIndex < 0) {
    const contentMatches = next
      .map((message, index) => ({ message, index }))
      .filter(({ message }) => (
        message.role === 'user' && message.content === optimisticUserMessage.content
      ))
    const unseenMatch = [...contentMatches].reverse().find(({ message }) => (
      message.id && !knownServerIds.has(String(message.id))
    ))
    const fallbackMatch = knownServerIds.size === 0 ? contentMatches.at(-1) : null
    userIndex = unseenMatch?.index ?? fallbackMatch?.index ?? -1
  }

  if (userIndex < 0) {
    const stableAssistantIndex = next.findIndex((message) => (
      message.role === 'assistant'
      && (
        sharesIdentity(message, fallbackAssistantMessage, MESSAGE_IDENTITY_KEYS)
        || sharesIdentity(message, optimisticUserMessage, TURN_IDENTITY_KEYS)
        || sharesIdentity(message, fallbackAssistantMessage, TURN_IDENTITY_KEYS)
      )
    ))
    userIndex = stableAssistantIndex >= 0 ? stableAssistantIndex : next.length
    next.splice(userIndex, 0, optimisticUserMessage)
  }

  const matchedUser = next[userIndex]
  let assistantIndex = next.findIndex((message) => (
    message.role === 'assistant'
    && (
      sharesIdentity(message, fallbackAssistantMessage, MESSAGE_IDENTITY_KEYS)
      || sharesIdentity(message, optimisticUserMessage, TURN_IDENTITY_KEYS)
      || sharesIdentity(message, fallbackAssistantMessage, TURN_IDENTITY_KEYS)
      || assistantRepliesTo(message, matchedUser)
    )
  ))

  if (assistantIndex < 0) {
    for (let index = userIndex + 1; index < next.length; index += 1) {
      if (next[index].role === 'user') break
      if (next[index].role === 'assistant') {
        assistantIndex = index
        break
      }
    }
  }

  if (assistantIndex < 0 && fallbackAssistantMessage?.content) {
    const nextUserIndex = next.findIndex((message, index) => (
      index > userIndex && message.role === 'user'
    ))
    assistantIndex = nextUserIndex >= 0 ? nextUserIndex : next.length
    next.splice(assistantIndex, 0, fallbackAssistantMessage)
  } else if (assistantIndex >= 0 && fallbackAssistantMessage) {
    const serverAssistant = next[assistantIndex]
    next[assistantIndex] = {
      ...serverAssistant,
      ...(!serverAssistant.content && fallbackAssistantMessage.content
        ? { content: fallbackAssistantMessage.content }
        : {}),
      ...(!serverAssistant.progress && fallbackAssistantMessage.progress
        ? { progress: fallbackAssistantMessage.progress }
        : {}),
      ...((serverAssistant.proposed_actions || []).length === 0
        && (fallbackAssistantMessage.proposed_actions || []).length > 0
        ? { proposed_actions: fallbackAssistantMessage.proposed_actions }
        : {}),
      ...((serverAssistant.sources || []).length === 0
        && (fallbackAssistantMessage.sources || []).length > 0
        ? { sources: fallbackAssistantMessage.sources }
        : {}),
      ...((serverAssistant.citation_annotations || []).length === 0
        && (fallbackAssistantMessage.citation_annotations || []).length > 0
        ? { citation_annotations: fallbackAssistantMessage.citation_annotations }
        : {}),
    }
  }

  return attachTurnReferences(next)
}

/**
 * Render a registry generation into the transcript on screen.
 *
 * The first call for a turn places the streaming pair against whatever the
 * server already holds — the page may have mounted after the turn began, in
 * which case the persisted user message is already there and must not be
 * duplicated. Later calls replace the streamed turn in place.
 */
export function upsertGenerationTurn(messages, generation) {
  const next = Array.isArray(messages) ? [...messages] : []
  const { userMessage, assistantMessage } = generation
  const assistantIndex = next.findIndex((message) => message.id === assistantMessage.id)
  if (assistantIndex >= 0) {
    next[assistantIndex] = { ...next[assistantIndex], ...assistantMessage }
    const userIndex = assistantIndex - 1
    if (userIndex >= 0 && next[userIndex].role === 'user' && userMessage.referenceContext) {
      next[userIndex] = { ...next[userIndex], referenceContext: userMessage.referenceContext }
    }
    return next
  }

  const merged = mergeRefreshedTranscript(next, userMessage, assistantMessage)
  if (merged.some((message) => message.id === assistantMessage.id)) return merged

  // mergeRefreshedTranscript reconciles finished turns, so it only splices an
  // assistant that already carries text. A turn that has only just started has
  // none yet and still needs its placeholder mounted: that placeholder is what
  // shows retrieval progress, and what a failure before the first token is
  // reported in.
  let userIndex = merged.findIndex((message) => message.id === userMessage.id)
  if (userIndex < 0) {
    for (let index = merged.length - 1; index >= 0; index -= 1) {
      if (merged[index].role === 'user' && merged[index].content === userMessage.content) {
        userIndex = index
        break
      }
    }
  }
  if (userIndex < 0) return [...merged, assistantMessage]

  // Never add a second answer to a turn the server has already answered.
  let insertAt = userIndex + 1
  while (insertAt < merged.length && merged[insertAt].role !== 'user') {
    if (merged[insertAt].role === 'assistant') return merged
    insertAt += 1
  }
  merged.splice(insertAt, 0, assistantMessage)
  return merged
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

function completedStreamStatus(publicRetrieval) {
  if (publicRetrieval?.state === 'service_unavailable') {
    return 'Response complete — public authority unavailable'
  }
  return 'Response complete'
}

function persistedPublicRetrievalStatus(retrievalMetadata) {
  const publicRetrieval = retrievalMetadata?.public_retrieval
  return publicRetrieval?.state === 'service_unavailable'
    ? completedStreamStatus(publicRetrieval)
    : ''
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
    } else if (type === 'tenant_document' && src?.source_label === 'Attached document') {
      counts.uploads += 1
    } else {
      counts.firm += 1
    }
  }
  counts.total = counts.matter + counts.uploads + counts.firm + counts.courtlistener
  return counts
}

function buildReferenceContext({ progress, sources, status, citedCount } = {}) {
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

  const referenceContext = {
    counts,
    source_count: sourceList.length,
    status: status || progress?.status || (sourceList.length ? 'Materials retrieved for source audit' : ''),
    complete: Boolean(progress?.complete),
  }
  if (Number.isFinite(citedCount)) {
    referenceContext.cited_count = citedCount
  }
  return referenceContext
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
    const citedCount = citedSourceCount(
      assistant.content,
      assistant.sources,
      assistant.citation_annotations,
    )
    const context = buildReferenceContext({
      progress: assistant.progress,
      sources: assistant.sources,
      status: persistedPublicRetrievalStatus(assistant.retrieval_metadata),
      citedCount,
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
  const routeConvId = searchParams.get('conv')
  const { conversations, setConversations, activeConvId, setActiveConvId, onConversationDeleted } = useAppShell()
  const { user, refreshUser } = useAuth()

  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [generationVersion, setGenerationVersion] = useState(0)
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
  const [metadataRefreshRetrying, setMetadataRefreshRetrying] = useState(false)
  const [privacySaving, setPrivacySaving] = useState(false)
  const fileInputRef = useRef(null)
  const matterPickerRef = useRef(null)
  const messagesRef = useRef(messages)
  const activeConvIdRef = useRef(activeConvId)
  const loadedConversationIdRef = useRef(null)
  const loadingConversationIdRef = useRef(null)
  const conversationLoadRequestRef = useRef(0)
  const streamRequestRef = useRef(0)
  const metadataRefreshContextRef = useRef(null)
  const metadataRefreshRequestRef = useRef(0)
  const metadataRefreshRetryingRef = useRef(false)
  const settledGenerationsRef = useRef(new Set())

  // Generations live in module scope so that they survive this page. Re-render
  // on every registry mutation; the derived values below are the only readers.
  useEffect(() => subscribeToChatGenerations(
    () => setGenerationVersion((version) => version + 1),
  ), [])

  // The registry is the real dependency of both; the version counter is how it
  // reports a change.
  const liveGeneration = useMemo(
    () => getChatGeneration(activeConvId),
    [activeConvId, generationVersion],
  )
  const generationCount = useMemo(() => countStreamingChatGenerations(), [generationVersion])
  const isSending = isSubmitting || liveGeneration?.status === GENERATION_STREAMING

  useEffect(() => {
    activeConvIdRef.current = activeConvId
  }, [activeConvId])

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  useEffect(() => {
    if (generationCount !== 0) return
    setNotice((current) => current?.kind === 'background-generation' ? null : current)
  }, [generationCount])

  const showErrorNotice = useCallback((title, fallback, err) => {
    setNotice({
      type: 'error',
      title,
      message: err?.response?.data?.detail || err?.message || fallback,
    })
  }, [])

  const togglePrivacyMode = useCallback(async () => {
    if (privacySaving || user?.demo) return
    const nextValue = !user?.privacy_mode
    if (nextValue && !window.confirm('Turn on Privacy Mode? This immediately revokes any connected Claude, ChatGPT, Codex, or other external MCP assistant. Native LawHand features remain available with Privacy Mode safeguards.')) return
    setPrivacySaving(true)
    try {
      await updateMe({ privacy_mode: nextValue })
      await refreshUser?.()
    } catch (err) {
      showErrorNotice('Privacy preference could not be saved', 'Please try again.', err)
    } finally {
      setPrivacySaving(false)
    }
  }, [privacySaving, refreshUser, showErrorNotice, user?.demo, user?.privacy_mode])

  // Unbind this page from the turn it started without touching the read itself:
  // a detached generation keeps draining in the registry so the server still
  // commits the answer.
  const detachActiveStream = useCallback(() => {
    streamRequestRef.current += 1
  }, [])

  // Leaving Chat must not cancel an answer. The server persists a streamed turn
  // only once the read reaches [STREAM_COMPLETE]; aborting here made it record an
  // interruption instead, so opening another menu lost the response outright.
  // The read and its partial answer live in the generation registry, which
  // outlives this page — detach UI state and let the read finish.
  useEffect(() => () => {
    conversationLoadRequestRef.current += 1
    streamRequestRef.current += 1
    metadataRefreshRequestRef.current += 1
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
    let convId = activeConvIdRef.current

    if (!convId) {
      try {
        const conv = await createConversation()
        setConversations((prev) => [conv, ...prev])
        activeConvIdRef.current = conv.id
        loadedConversationIdRef.current = conv.id
        setActiveConvId(conv.id)
        setActiveConvTitle(conv.title || 'New Conversation')
        navigate(`/chat?conv=${conv.id}`)
        convId = conv.id
      } catch (err) {
        reportError('Failed to create conversation', err)
        showErrorNotice('Conversation could not be created', 'Start a new conversation and try again.', err)
        return
      }
    }

    for (const file of files) {
      try {
        const doc = await uploadChatAttachment(convId, file)
        setPendingAttachments((prev) => [...prev, { id: doc.id, filename: doc.filename }])
      } catch (err) {
        reportError('Upload failed:', err)
        showErrorNotice('Attachment upload failed', `${file.name} could not be uploaded.`, err)
      }
    }
  }

  const loadConversation = useCallback(async (id) => {
    if (!id) return
    const conversationChanged = Boolean(
      activeConvIdRef.current && activeConvIdRef.current !== id
    )
    detachActiveStream()
    if (conversationChanged) {
      setPendingAttachments([])
    }

    const loadRequestId = conversationLoadRequestRef.current + 1
    conversationLoadRequestRef.current = loadRequestId
    activeConvIdRef.current = id
    loadingConversationIdRef.current = id
    metadataRefreshContextRef.current = null
    metadataRefreshRequestRef.current += 1
    metadataRefreshRetryingRef.current = false
    setMetadataRefreshRetrying(false)
    setNotice((current) => current?.kind === 'metadata' ? null : current)
    setIsLoadingMessages(true)
    setActiveConvId(id)
    try {
      const data = await getConversation(id)
      if (
        conversationLoadRequestRef.current !== loadRequestId
        || activeConvIdRef.current !== id
      ) return

      loadedConversationIdRef.current = id
      // Reattach on arrival rather than waiting for the next token: a generation
      // this page left behind may still be running, and its turn belongs back in
      // view as soon as the saved transcript is.
      const loaded = attachTurnReferences(data.messages || [])
      const live = getChatGeneration(id)
      setMessages(
        live && live.status !== GENERATION_ABORTED
          ? upsertGenerationTurn(loaded, live)
          : loaded
      )
      setActiveConvTitle(data.conversation?.title || 'Untitled')
      if (data.conversation) {
        setConversations((prev) =>
          prev.some((conv) => conv.id === data.conversation.id)
            ? prev.map((conv) => (conv.id === data.conversation.id ? { ...conv, ...data.conversation } : conv))
            : [data.conversation, ...prev]
        )
      }
    } catch (err) {
      if (
        conversationLoadRequestRef.current !== loadRequestId
        || activeConvIdRef.current !== id
      ) return

      reportError('Failed to load conversation', err)
      const status = err?.response?.status
      // Never leave a previous conversation visible beneath a newly selected
      // conversation ID. A stale legal transcript is worse than an empty error
      // state because its action cards still remain operable.
      loadedConversationIdRef.current = id
      setMessages([])
      setActiveConvTitle('')
      if (status === 403 || status === 404) {
        setConversations((prev) => prev.filter((conv) => conv.id !== id))
        activeConvIdRef.current = null
        loadedConversationIdRef.current = null
        setActiveConvId(null)
        navigate('/chat', { replace: true })
      }
      showErrorNotice('Conversation could not be loaded', 'Select another conversation or retry.', err)
    } finally {
      if (conversationLoadRequestRef.current === loadRequestId) {
        loadingConversationIdRef.current = null
        setIsLoadingMessages(false)
      }
    }
  }, [detachActiveStream, navigate, setActiveConvId, setConversations, showErrorNotice])

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

  // A prompt handed off from another page is a one-time draft, independent of
  // which conversation ultimately loads.
  useEffect(() => {
    const pending = sessionStorage.getItem('pending_chat_message')
    if (pending) {
      setInputValue(pending)
      sessionStorage.removeItem('pending_chat_message')
    }
  }, [])

  // Treat the URL as the durable conversation selection and react to browser
  // back/forward and AppShell Ctrl+N changes. Refs prevent the state updates
  // inside loadConversation from starting the same request twice.
  useEffect(() => {
    const targetId = routeConvId || activeConvId || conversations[0]?.id
    if (!targetId) return
    if (
      loadedConversationIdRef.current === targetId
      || loadingConversationIdRef.current === targetId
    ) return
    loadConversation(targetId)
  }, [conversations, activeConvId, routeConvId, loadConversation])

  const handleNewConversation = useCallback(async () => {
    try {
      detachActiveStream()
      conversationLoadRequestRef.current += 1
      const conv = await createConversation()
      setConversations((prev) => [conv, ...prev])
      activeConvIdRef.current = conv.id
      loadedConversationIdRef.current = conv.id
      loadingConversationIdRef.current = null
      metadataRefreshContextRef.current = null
      metadataRefreshRequestRef.current += 1
      metadataRefreshRetryingRef.current = false
      setActiveConvId(conv.id)
      setMessages([])
      setActiveConvTitle(conv.title || 'New Conversation')
      setPendingAttachments([])
      setNotice(null)
      navigate(`/chat?conv=${conv.id}`)
    } catch (err) {
      reportError('Failed to create conversation', err)
      showErrorNotice('Conversation could not be created', 'Please try again.', err)
    }
  }, [detachActiveStream, navigate, setConversations, setActiveConvId, showErrorNotice])

  const handleConversationDeleted = useCallback(
    (id) => {
      // AppShell context performs the API delete + list mutation;
      // here we additionally clear local thread state if it was active.
      // A detached response for a conversation that no longer exists must not
      // keep consuming model time or attempt a late persistence write.
      abortChatGeneration(id)
      if (activeConvIdRef.current === id) {
        detachActiveStream()
        conversationLoadRequestRef.current += 1
        activeConvIdRef.current = null
        loadedConversationIdRef.current = null
        loadingConversationIdRef.current = null
        metadataRefreshContextRef.current = null
        metadataRefreshRequestRef.current += 1
        metadataRefreshRetryingRef.current = false
        setMetadataRefreshRetrying(false)
        setActiveConvId(null)
        setMessages([])
        setActiveConvTitle('')
        setPendingAttachments([])
        navigate('/chat', { replace: true })
      }
    },
    [detachActiveStream, navigate, setActiveConvId]
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
      navigate(`/chat?conv=${id}`)
      loadConversation(id)
      setRailOpen(false)
    },
    [loadConversation, navigate]
  )

  const applyRefreshedConversation = useCallback((conversationId, refreshed, nextMessages) => {
    if (activeConvIdRef.current !== conversationId) return false
    loadedConversationIdRef.current = conversationId
    setMessages(nextMessages)
    setActiveConvTitle((current) => refreshed.conversation?.title || current)
    if (refreshed.conversation) {
      setConversations((prev) =>
        prev.some((conv) => conv.id === refreshed.conversation.id)
          ? prev.map((conv) => (conv.id === refreshed.conversation.id ? { ...conv, ...refreshed.conversation } : conv))
          : [refreshed.conversation, ...prev]
      )
    }
    return true
  }, [setConversations])

  const handleRetryMetadataRefresh = useCallback(async () => {
    const retryContext = metadataRefreshContextRef.current
    if (!retryContext || activeConvIdRef.current !== retryContext.conversationId) {
      setNotice({
        type: 'info',
        title: 'Select the affected conversation',
        message: 'Source metadata can only be retried while that conversation is open.',
      })
      return
    }
    if (metadataRefreshRetryingRef.current) return

    const retryRequestId = metadataRefreshRequestRef.current + 1
    metadataRefreshRequestRef.current = retryRequestId
    metadataRefreshRetryingRef.current = true
    setMetadataRefreshRetrying(true)
    setNotice({
      type: 'info',
      kind: 'metadata',
      title: 'Refreshing source metadata',
      message: 'Retrieving the persisted citations and proposed actions for this answer.',
    })
    try {
      const refreshed = await getConversation(retryContext.conversationId)
      if (
        metadataRefreshRequestRef.current !== retryRequestId
        || activeConvIdRef.current !== retryContext.conversationId
      ) return
      const nextMessages = mergeRefreshedTranscript(
        refreshed.messages,
        retryContext.userMessage,
        retryContext.fallbackAssistantMessage,
      )
      applyRefreshedConversation(retryContext.conversationId, refreshed, nextMessages)
      metadataRefreshContextRef.current = null
      setNotice({
        type: 'success',
        title: 'Sources refreshed',
        message: 'Citation links and proposed actions now reflect the persisted response.',
      })
    } catch (err) {
      if (
        metadataRefreshRequestRef.current !== retryRequestId
        || activeConvIdRef.current !== retryContext.conversationId
      ) return
      reportError('Failed to retry streamed message metadata', err)
      setNotice({
        type: 'warning',
        kind: 'metadata',
        title: 'Source metadata is still unavailable',
        message: err?.response?.data?.detail || err?.message || 'Retry before relying on this answer or approving any proposed work.',
      })
    } finally {
      if (metadataRefreshRequestRef.current === retryRequestId) {
        metadataRefreshRetryingRef.current = false
        setMetadataRefreshRetrying(false)
      }
    }
  }, [applyRefreshedConversation])

  const handleSend = useCallback(async () => {
    const content = inputValue.trim()
    if (!content || isSending) return
    const hasLinkedMatter = conversations.some(
      (conversation) => conversation.id === activeConvIdRef.current && conversation.matter_id,
    )
    if (!usePremium && hasLinkedMatter) {
      setNotice({
        type: 'info',
        title: 'Matter context requires a private route',
        message: 'Standard AI cannot use matter context. Switch to Premium or unlink the matter and start a general conversation.',
      })
      return
    }
    if (!usePremium && pendingAttachments.length) {
      setNotice({
        type: 'info',
        title: 'Attachments require a private route',
        message: 'Standard AI cannot process attachments. Switch to Premium or remove the attachment before sending.',
      })
      return
    }
    if (countStreamingChatGenerations() > 0) {
      setNotice({
        type: 'info',
        kind: 'background-generation',
        title: 'Another response is still finishing',
        message: 'You can browse and draft in this conversation. Sending is enabled when the earlier response finishes.',
      })
      return
    }

    setIsSubmitting(true)
    let generation = null
    let convId = activeConvIdRef.current
    let clientTurnId = null
    let assistantMsgId = null
    let attachmentIds = []

    try {
      if (!convId) {
        try {
          const conv = await createConversation(content.slice(0, 60))
          setConversations((prev) => [conv, ...prev])
          activeConvIdRef.current = conv.id
          loadedConversationIdRef.current = conv.id
          setActiveConvId(conv.id)
          setActiveConvTitle(conv.title || 'New Conversation')
          navigate(`/chat?conv=${conv.id}`)
          convId = conv.id
          // Brief pause to let DB commit settle before the streaming read
          await new Promise(r => setTimeout(r, 150))
          // Creating a conversation is the only send path with an await before
          // the optimistic turn is mounted. If the user selected another thread
          // during that window, do not append this turn to (or send it from) the
          // newly selected conversation.
          if (activeConvIdRef.current !== convId) return
        } catch (err) {
          reportError('Failed to create conversation', err)
          showErrorNotice('Conversation could not be created', 'Your message was not sent. Try again after starting a new conversation.', err)
          return
        }
      }

      attachmentIds = pendingAttachments.map((a) => a.id)
      const initialProgress = initialStreamProgress(content, attachmentIds.length)
      const initialReferenceContext = buildReferenceContext({ progress: initialProgress })
      clientTurnId = globalThis.crypto?.randomUUID?.()
        || `turn-${Date.now()}-${Math.random().toString(36).slice(2)}`
      const knownServerMessageIds = messagesRef.current
        .map((message) => String(message?.id || ''))
        .filter((id) => id && !/^(temp|stream|err)-/.test(id))
      assistantMsgId = `stream-${clientTurnId}`

      metadataRefreshContextRef.current = null
      metadataRefreshRequestRef.current += 1
      metadataRefreshRetryingRef.current = false
      setMetadataRefreshRetrying(false)
      setNotice((current) => current?.kind === 'metadata' ? null : current)
      setInputValue('')
      setPendingAttachments([])

      // The registry — not this component — owns the turn from here on, so the
      // optimistic pair is registered rather than pushed into `messages`. An
      // effect renders whichever generation belongs to the conversation on
      // screen, which is what lets a page that mounts later pick this one up.
      streamRequestRef.current += 1
      generation = beginChatGeneration({
        conversationId: convId,
        clientTurnId,
        controller: new AbortController(),
        userMessage: {
          id: `temp-${clientTurnId}`,
          role: 'user',
          content,
          sources: [],
          referenceContext: initialReferenceContext,
          client_turn_id: clientTurnId,
          _known_server_message_ids: knownServerMessageIds,
          created_at: new Date().toISOString(),
        },
        assistantMessage: {
          id: assistantMsgId,
          role: 'assistant',
          content: '',
          sources: [],
          progress: initialProgress,
          referenceContext: initialReferenceContext,
          client_turn_id: clientTurnId,
          created_at: new Date().toISOString(),
        },
      })
    } finally {
      setIsSubmitting(false)
    }

    if (!generation) return

    const streamRequestId = streamRequestRef.current
    // Whether a page is still bound to this turn. A failure seen while bound is
    // shown in place; once detached, the server's persisted interruption is the
    // truth and gets re-read instead.
    const isAttached = () => (
      streamRequestRef.current === streamRequestId
      && activeConvIdRef.current === convId
    )
    const publish = (assistant, user = null) => {
      patchChatGeneration(convId, clientTurnId, { assistant, user })
    }

    let streamProgress = generation.assistantMessage.progress
    let accumulatedText = ''
    let streamError = null
    let sawStreamComplete = false
    let streamedSources = []
    let streamedCitationAnnotations = []

    try {
      for await (const token of streamMessage(
        convId,
        content,
        includePublic,
        usePremium,
        attachmentIds,
        { signal: generation.controller.signal },
      )) {
        if (token?.type === 'progress' && token.event === 'citation_metadata') {
          streamedSources = token.sources || []
          streamedCitationAnnotations = token.citation_annotations || []
          if (token.public_retrieval && typeof token.public_retrieval === 'object') {
            streamProgress = {
              ...streamProgress,
              public_retrieval: token.public_retrieval,
            }
          }
          const referenceContext = buildReferenceContext({
            progress: streamProgress,
            sources: streamedSources,
            citedCount: streamedSources.length,
          })
          publish({
            sources: streamedSources,
            citation_annotations: streamedCitationAnnotations,
            progress: streamProgress,
            referenceContext,
          })
          continue
        }
        if (token?.type === 'progress' && token.event === 'action_proposal') {
          // Reviewable work the assistant proposed. Attached to the message
          // rather than merged into progress, since it outlives the stream.
          publish({ proposed_actions: token.proposed_actions || [] })
          continue
        }
        if (token?.type === 'progress') {
          streamProgress = mergeStreamProgress(streamProgress, token, content)
          const referenceContext = buildReferenceContext({
            progress: streamProgress,
            sources: streamedSources,
            citedCount: streamedSources.length,
          })
          publish({ progress: streamProgress, referenceContext }, { referenceContext })
          continue
        }
        if (token?.type === 'artifacts') {
          const streamArtifacts = Array.isArray(token.artifacts) ? token.artifacts : []
          if (streamArtifacts.length > 0) publish({ artifacts: streamArtifacts })
          continue
        }
        if (token === '[STREAM_COMPLETE]') {
          sawStreamComplete = true
          streamProgress = {
            ...streamProgress,
            complete: true,
            status: completedStreamStatus(streamProgress.public_retrieval),
          }
          const referenceContext = buildReferenceContext({
            progress: streamProgress,
            sources: streamedSources,
            citedCount: streamedSources.length,
          })
          publish({ progress: streamProgress, referenceContext }, { referenceContext })
          break
        } else if (typeof token === 'string' && token.startsWith('[ERROR]')) {
          streamError = token.slice(7)
          break
        } else if (typeof token === 'string') {
          accumulatedText += token
          publish({ content: accumulatedText })
        }
      }

      if (!streamError && !sawStreamComplete) {
        streamError = 'The assistant stream ended before completion. Please retry.'
      }
      if (!streamError && !accumulatedText.trim()) {
        streamError = 'The assistant completed without a visible answer. Please retry.'
      }

      settleChatGeneration(convId, clientTurnId, {
        status: streamError ? GENERATION_ERROR : GENERATION_COMPLETE,
        error: streamError,
        errorSource: streamError ? 'stream' : null,
        attached: isAttached(),
        assistant: {
          content: accumulatedText,
          sources: streamedSources,
          citation_annotations: streamedCitationAnnotations,
          progress: { ...streamProgress, complete: true },
        },
      })
    } catch (err) {
      if (err?.name === 'AbortError') {
        // Only a deleted conversation aborts a read, and its transcript is gone.
        releaseChatGeneration(convId, clientTurnId)
        return
      }
      reportError('Failed to send message', err)
      settleChatGeneration(convId, clientTurnId, {
        status: GENERATION_ERROR,
        error: err?.response?.data?.detail || err?.message || 'Please try again.',
        errorSource: 'request',
        attached: isAttached(),
        assistant: { content: accumulatedText, progress: streamProgress },
      })
    }
  }, [inputValue, isSending, includePublic, usePremium, conversations, pendingAttachments, setConversations, setActiveConvId, showErrorNotice, navigate])

  // A generation outlives this page, so the transcript renders from the registry
  // rather than from the send handler: whichever ChatPage is mounted when tokens
  // arrive shows them, including one that mounted after the user came back from
  // another menu. Keying on the conversation on screen is also what keeps a
  // detached generation's tokens out of the thread the user moved to.
  useEffect(() => {
    if (!liveGeneration || !activeConvId) return
    if (liveGeneration.conversationId !== activeConvId) return
    if (liveGeneration.status === GENERATION_ABORTED) return
    // `isLoadingMessages` is a dependency, not just a guard: the transcript this
    // splices into arrives asynchronously, and a ref would not re-run the effect
    // once it did.
    if (isLoadingMessages || loadedConversationIdRef.current !== activeConvId) return
    setMessages((prev) => upsertGenerationTurn(prev, liveGeneration))
  }, [liveGeneration, activeConvId, isLoadingMessages])

  const settleGeneration = useCallback(async (generation) => {
    const {
      conversationId,
      clientTurnId,
      userMessage,
      assistantMessage,
      status,
      error,
      errorSource,
      attached,
    } = generation
    try {
      if (!attached) {
        // The page let go of this turn before it ended — another thread was
        // opened, or Chat was left and reopened. Either way the server finished
        // the read and persisted the outcome, so re-read rather than reconcile
        // against an optimistic turn this page may no longer be showing.
        await loadConversation(conversationId)
        return
      }

      if (status === GENERATION_ERROR) {
        // This page watched the turn fail, so fail its placeholder in place.
        // Re-reading the conversation would only swap one terminal message for
        // the interruption marker the server just wrote.
        const failedProgress = { ...assistantMessage.progress, complete: true, status: 'Response failed' }
        const failedReferenceContext = buildReferenceContext({ progress: failedProgress })
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMessage.id
              ? {
                  ...msg,
                  content: `An error occurred: ${error}`,
                  progress: failedProgress,
                  referenceContext: failedReferenceContext,
                }
              : msg.id === userMessage.id
                ? { ...msg, referenceContext: failedReferenceContext }
                : msg
          )
        )
        if (errorSource === 'request') {
          showErrorNotice('Message could not be sent', 'Please try again.', { message: error })
        } else {
          setNotice({
            type: 'error',
            title: 'Response could not be completed',
            message: error || 'The assistant stopped before finishing the response.',
          })
        }
        return
      }

      const fallbackAssistantMessage = {
        ...assistantMessage,
        referenceContext: buildReferenceContext({
          progress: assistantMessage.progress,
          sources: assistantMessage.sources,
          citedCount: (assistantMessage.sources || []).length,
        }),
      }
      try {
        const refreshed = await getConversation(conversationId)
        if (activeConvIdRef.current !== conversationId) return
        applyRefreshedConversation(
          conversationId,
          refreshed,
          mergeRefreshedTranscript(refreshed.messages, userMessage, fallbackAssistantMessage),
        )
        metadataRefreshContextRef.current = null
      } catch (refreshErr) {
        if (activeConvIdRef.current !== conversationId) return
        reportError('Failed to refresh streamed message metadata', refreshErr)
        metadataRefreshContextRef.current = {
          conversationId,
          userMessage,
          fallbackAssistantMessage,
        }
        setNotice({
          type: 'warning',
          kind: 'metadata',
          title: 'Source metadata could not be verified',
          message: 'The answer text is shown, but its persisted citation links and proposed actions could not be refreshed. Retry before relying on it.',
        })
      }
      setConversations((prev) =>
        prev.map((conv) =>
          conv.id === conversationId ? { ...conv, updated_at: new Date().toISOString() } : conv
        )
      )
    } finally {
      releaseChatGeneration(conversationId, clientTurnId)
    }
  }, [applyRefreshedConversation, loadConversation, setConversations, showErrorNotice])

  // Reconcile a finished turn against the saved transcript here rather than in
  // the send handler, so the page that is mounted — not the one that started the
  // answer — is the one that settles it.
  useEffect(() => {
    if (!liveGeneration || !activeConvId) return
    if (liveGeneration.conversationId !== activeConvId) return
    if (liveGeneration.status === GENERATION_STREAMING) return
    if (liveGeneration.status === GENERATION_ABORTED) {
      releaseChatGeneration(liveGeneration.conversationId, liveGeneration.clientTurnId)
      return
    }
    if (isLoadingMessages || loadedConversationIdRef.current !== activeConvId) return
    const turnKey = `${liveGeneration.conversationId}:${liveGeneration.clientTurnId}`
    if (settledGenerationsRef.current.has(turnKey)) return
    settledGenerationsRef.current.add(turnKey)
    settleGeneration(liveGeneration)
  }, [liveGeneration, activeConvId, isLoadingMessages, settleGeneration])

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
  const hasBackgroundGeneration = generationCount > 0 && !isSending
  const conversationContextLocked = messages.length > 0
    || Number(activeConversation?.message_count || 0) > 0
    || Number(activeConversation?.attachment_count || 0) > 0
    || pendingAttachments.length > 0
  const matterLinkBlocked = isLoadingMessages
    || isSending
    || generationCount > 0
    || conversationContextLocked
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
    if (matterLinkBlocked) {
      setMatterPickerOpen(false)
      setNotice({
        type: 'info',
        title: 'Matter context is in use',
        message: conversationContextLocked
          ? 'Matter context is locked after a conversation has messages or attachments. Start a new conversation for a different matter.'
          : 'Wait for the conversation to load and the active response to finish before changing its matter context.',
      })
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
  }, [activeConvId, conversationContextLocked, matterLinkBlocked, setConversations, showErrorNotice])

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
            demoMode={Boolean(user?.demo)}
            standardMatterContextAllowed={Boolean(user?.standard_matter_context_allowed)}
            includePublic={includePublic}
            setIncludePublic={setIncludePublic}
            privacyMode={Boolean(user?.privacy_mode)}
            privacySaving={privacySaving}
            onTogglePrivacy={togglePrivacyMode}
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
                <p className="hidden text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted sm:block">AI context</p>
                <p className={`truncate text-xs font-semibold sm:mt-0.5 sm:text-sm ${linkedMatterId ? 'text-brand-ink' : 'text-brand-muted'}`}>
                  {linkedMatterId
                    ? `Using your profile + ${linkedMatterName}`
                    : activeConvId
                      ? 'Using your profile'
                      : 'Using your profile — start a conversation to add a matter'}
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
                disabled={!activeConvId || matterLinking || matterLinkBlocked}
                title={conversationContextLocked
                  ? 'Matter context is locked after messages or attachments are added'
                  : matterLinkBlocked
                    ? 'Wait for the conversation to load or finish responding'
                    : linkedMatterId ? 'Change matter context' : 'Link this conversation to a matter'}
                aria-expanded={matterPickerOpen}
                aria-haspopup="dialog"
                className="inline-flex min-h-8 items-center gap-1 rounded-lg border border-brand-line bg-brand-surface px-2 text-xs font-semibold text-brand-ink hover:bg-brand-bg-soft disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-10 sm:gap-2 sm:rounded-xl sm:px-3"
              >
                <Link2 size={14} className="hidden sm:block" />
                <span className="hidden sm:inline">{linkedMatterId ? 'Change' : 'Link matter'}</span>
                <ChevronDown size={13} />
              </button>
            </div>

            {matterPickerOpen && !matterLinkBlocked && (
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
                actionLabel={notice.kind === 'metadata'
                  ? (metadataRefreshRetrying ? 'Retrying…' : 'Retry source metadata')
                  : notice.actionLabel}
                onAction={notice.kind === 'metadata' ? handleRetryMetadataRefresh : notice.onAction}
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

          {hasBackgroundGeneration && (
            <div role="status" className="border-t border-brand-line bg-amber-50 px-4 py-2 text-center text-xs font-semibold text-amber-900">
              A response is finishing in the background. You can browse and draft here; sending unlocks when it finishes.
            </div>
          )}

          <ChatInput
            inputValue={inputValue}
            onInputChange={setInputValue}
            onSend={handleSend}
            onUploadClick={handleUploadClick}
            onDropFiles={handleDropFiles}
            isSending={isSending}
            disabled={false}
            sendDisabled={hasBackgroundGeneration}
            sendDisabledLabel="Another conversation response is finishing"
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
