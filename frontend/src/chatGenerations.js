/**
 * In-flight chat generations, held outside the React tree.
 *
 * The server persists a streamed answer only once the SSE read reaches
 * `[STREAM_COMPLETE]`; cancelling that fetch makes it record an interrupted turn
 * instead. ChatPage unmounts whenever the user opens another menu, so keeping a
 * generation in component state meant leaving Chat threw the answer away. They
 * live here instead: navigation no longer cancels one, and whichever ChatPage is
 * mounted when tokens arrive renders them, including one that mounted after the
 * user came back.
 *
 * Keyed by conversation id, matching the server's per-conversation generation
 * lease: one answer per conversation can be in flight at a time.
 */

export const GENERATION_STREAMING = 'streaming'
export const GENERATION_COMPLETE = 'complete'
export const GENERATION_ERROR = 'error'
export const GENERATION_ABORTED = 'aborted'

// A settled generation waits for the page that will reconcile it against the
// persisted transcript. Nothing collects one whose conversation the user never
// reopens, so cap what a single tab can accumulate.
const SETTLED_TTL_MS = 15 * 60 * 1000
const MAX_GENERATIONS = 12

const generations = new Map()
const listeners = new Set()

function notify() {
  for (const listener of [...listeners]) {
    try {
      listener()
    } catch {
      // A subscriber that throws must not strand the remaining subscribers or
      // the detached drain loop that published the update.
    }
  }
}

function prune() {
  const now = Date.now()
  for (const [conversationId, record] of generations) {
    if (record.status === GENERATION_STREAMING) continue
    if (now - record.settledAt > SETTLED_TTL_MS) generations.delete(conversationId)
  }
  if (generations.size <= MAX_GENERATIONS) return
  const oldestSettledFirst = [...generations.entries()]
    .filter(([, record]) => record.status !== GENERATION_STREAMING)
    .sort((left, right) => left[1].settledAt - right[1].settledAt)
  for (const [conversationId] of oldestSettledFirst) {
    if (generations.size <= MAX_GENERATIONS) break
    generations.delete(conversationId)
  }
}

/** Subscribe to every registry mutation. Returns the unsubscribe function. */
export function subscribeToChatGenerations(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The generation for a conversation, streaming or settled-but-unreconciled. */
export function getChatGeneration(conversationId) {
  if (!conversationId) return null
  return generations.get(conversationId) || null
}

/** How many answers are still being read, across every conversation. */
export function countStreamingChatGenerations() {
  let count = 0
  for (const record of generations.values()) {
    if (record.status === GENERATION_STREAMING) count += 1
  }
  return count
}

export function beginChatGeneration({
  conversationId,
  clientTurnId,
  controller = null,
  userMessage,
  assistantMessage,
}) {
  if (!conversationId || !clientTurnId) return null
  prune()
  const record = {
    conversationId,
    clientTurnId,
    controller,
    userMessage,
    assistantMessage,
    status: GENERATION_STREAMING,
    error: null,
    errorSource: null,
    attached: true,
    startedAt: Date.now(),
    settledAt: null,
  }
  generations.set(conversationId, record)
  notify()
  return record
}

/**
 * Merge streamed fields into the stored turn. Records are replaced rather than
 * mutated so subscribers can compare identity to detect a change.
 */
export function patchChatGeneration(conversationId, clientTurnId, { assistant = null, user = null } = {}) {
  const record = generations.get(conversationId)
  if (!record || record.clientTurnId !== clientTurnId) return null
  const next = {
    ...record,
    ...(assistant ? { assistantMessage: { ...record.assistantMessage, ...assistant } } : {}),
    ...(user ? { userMessage: { ...record.userMessage, ...user } } : {}),
  }
  generations.set(conversationId, next)
  notify()
  return next
}

/**
 * Mark the read finished. `attached` records whether a page was still bound to
 * this turn when it ended, which decides whether a failure is shown in place or
 * re-read from the server's persisted interruption.
 */
export function settleChatGeneration(conversationId, clientTurnId, {
  status = GENERATION_COMPLETE,
  error = null,
  errorSource = null,
  attached = false,
  assistant = null,
} = {}) {
  const record = generations.get(conversationId)
  if (!record || record.clientTurnId !== clientTurnId) return null
  const next = {
    ...record,
    ...(assistant ? { assistantMessage: { ...record.assistantMessage, ...assistant } } : {}),
    status,
    error,
    errorSource,
    attached,
    controller: null,
    settledAt: Date.now(),
  }
  generations.set(conversationId, next)
  notify()
  return next
}

/** Drop a generation once its turn is reconciled against the saved transcript. */
export function releaseChatGeneration(conversationId, clientTurnId = null) {
  const record = generations.get(conversationId)
  if (!record) return false
  if (clientTurnId && record.clientTurnId !== clientTurnId) return false
  generations.delete(conversationId)
  notify()
  return true
}

/**
 * Stop a generation outright. Only a conversation that no longer exists warrants
 * this — an abandoned read still persists its answer, a cancelled one cannot.
 */
export function abortChatGeneration(conversationId) {
  const record = generations.get(conversationId)
  if (!record) return false
  record.controller?.abort()
  generations.set(conversationId, {
    ...record,
    status: GENERATION_ABORTED,
    controller: null,
    settledAt: Date.now(),
  })
  notify()
  return true
}

/** Test helper: drop every record without aborting, leaving no listeners armed. */
export function resetChatGenerations() {
  generations.clear()
  listeners.clear()
}
