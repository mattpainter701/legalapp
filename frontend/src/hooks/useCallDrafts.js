import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  assignNextPartner,
  createIntakeDashboardCall,
  deleteIntakeDraft,
  getIntakeDrafts,
  normalizeApiError,
  upsertIntakeDraft,
} from '../api'

const STORAGE_DRAFT_PREFIX = 'intake.drafts.'
const BACKEND_WRITE_DEBOUNCE_MS = 5000
const MAX_DRAFTS = 30
const MAX_RECEIPTS = 30
const RATE_LIMIT_STATUS = 429
const LOCAL_ORPHAN_KEEP_MS = 12 * 60 * 60 * 1000

const DRAFT_FORM_DEFAULTS = {
  caller_name: '',
  phone: '',
  practice_area: 'divorce',
  purpose: '',
  notes: '',
  qualified: true,
  outcome: 'create_lead',
  task_mode: 'partner_rotation',
  task_title: 'Call back caller',
  custom_task_title: '',
  auto_assign: true,
  source_communication_id: null,
  selected_staff_id: null,
  selected_staff_name: '',
  selected_staff_email: '',
  linked_history_contact_id: null,
  linked_history_lead_id: null,
  linked_history_result_id: null,
  linked_history_result_type: null,
  linked_history_title: '',
  linked_history_phone: '',
}

const nowISO = () => new Date().toISOString()

const newDraftId = () => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const newReceiptId = () => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `receipt-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const parseJson = (raw, fallback) => {
  if (!raw) return fallback
  try {
    const parsed = JSON.parse(raw)
    return parsed === undefined ? fallback : parsed
  } catch {
    return fallback
  }
}

const isTransientError = (error) => {
  const status = error?.status || error?.response?.status
  if (!status) return true
  return status >= 500 || status === 408 || status === 429
}

const MEANINGFUL_DRAFT_FIELDS = [
  'caller_name',
  'phone',
  'purpose',
  'notes',
  'custom_task_title',
  'source_communication_id',
  'selected_staff_id',
  'linked_history_contact_id',
  'linked_history_lead_id',
  'linked_history_result_id',
  'linked_history_title',
  'linked_history_phone',
]

function hasMeaningfulDraftContent(draft) {
  if (!draft || typeof draft !== 'object') return false
  if (MEANINGFUL_DRAFT_FIELDS.some((key) => {
    const value = draft[key]
    return typeof value === 'string' ? value.trim().length > 0 : Boolean(value)
  })) return true
  return ['practice_area', 'outcome', 'task_mode', 'task_title', 'qualified']
    .some((key) => draft[key] !== undefined && draft[key] !== DRAFT_FORM_DEFAULTS[key])
}

function isRecentLocalOrphan(draft) {
  const timestamp = draft?._localUpdatedAt || draft?.updated_at || draft?.created_at
  const age = Date.now() - new Date(timestamp || 0).getTime()
  return Number.isFinite(age) && age >= 0 && age <= LOCAL_ORPHAN_KEEP_MS
}

// Defends against a proxy/gateway HTML error page (nginx 429/502/504) ever
// getting stored verbatim as a receipt's error text — whether from a stale
// entry saved before this guard existed, or a future regression.
function sanitizeReceiptError(value) {
  const text = typeof value === 'string' ? value : ''
  if (/^\s*<(!doctype|html)/i.test(text)) return 'Request failed. Please try again.'
  return text.slice(0, 300)
}

function normalizeReceipts(list) {
  if (!Array.isArray(list)) return []
  return list
    .filter((entry) => entry && typeof entry === 'object')
    .map((entry) => ({
      id: entry.id || newReceiptId(),
      status: entry.status || 'pending',
      label: entry.label || 'Action',
      at: entry.at || nowISO(),
      error: sanitizeReceiptError(entry.error),
      retry: entry.retry || null,
    }))
    .filter((entry) => ['ok', 'failed', 'pending'].includes(entry.status))
}

function normalizeDraftFromStorage(raw) {
  if (!raw || typeof raw !== 'object') return null
  return {
    ...DRAFT_FORM_DEFAULTS,
    ...raw,
    ...raw.payload,
    draft_id: raw.draft_id || newDraftId(),
    created_at: raw.created_at || nowISO(),
    updated_at: raw.updated_at || nowISO(),
    receipts: normalizeReceipts(raw.receipts || raw.payload?.receipts),
    _dirty: Boolean(raw._dirty),
    _localOnly: Boolean(raw._localOnly),
    _syncing: false,
    _syncError: raw._syncError || null,
    _backendUpdatedAt: raw._backendUpdatedAt || null,
    _localUpdatedAt: raw._localUpdatedAt || nowISO(),
    _syncRetryCount: Number(raw._syncRetryCount || 0),
  }
}

function normalizeDraftFromBackend(row) {
  const payload = (row && row.payload) || {}
  return {
    ...DRAFT_FORM_DEFAULTS,
    ...payload,
    draft_id: row?.id || newDraftId(),
    created_at: row?.created_at || nowISO(),
    updated_at: row?.updated_at || nowISO(),
    receipts: normalizeReceipts(payload.receipts),
    _dirty: false,
    _localOnly: false,
    _syncing: false,
    _syncError: null,
    _backendUpdatedAt: row?.updated_at || null,
    _localUpdatedAt: row?.updated_at || nowISO(),
    _syncRetryCount: 0,
  }
}

function mergeTime(a, b) {
  return new Date(a || 0).getTime() - new Date(b || 0).getTime()
}

function isBackendFresher(localDraft, backendDraft) {
  const localTs = localDraft?._backendUpdatedAt || localDraft?.updated_at
  const backendTs = backendDraft?._backendUpdatedAt || backendDraft?.updated_at
  if (!backendTs) return false
  if (!localTs) return true
  return new Date(backendTs).getTime() >= new Date(localTs).getTime()
}

function sortDrafts(list) {
  return [...(Array.isArray(list) ? list : [])]
    .sort((left, right) => mergeTime(right.updated_at, left.updated_at))
    .slice(0, MAX_DRAFTS)
}

function parseReceiptTargetId(url) {
  const normalized = String(url || '').replace(/^\/+/, '')
  if (!normalized) return null
  const parts = normalized.split('/')
  return parts[parts.length - 1] || null
}

function parseAssignLeadId(url) {
  const match = String(url || '').match(/\/intake\/dashboard\/leads\/([^/]+)\/assign-next$/)
  return match ? match[1] : null
}

export default function useCallDrafts({ onToast } = {}) {
  const [drafts, setDraftsState] = useState([])
  const [activeDraftId, setActiveDraftId] = useState(null)
  const [loading, setLoading] = useState(true)
  const storageHealthy = true

  const draftsRef = useRef([])
  const mountedRef = useRef(true)
  const activeDraftIdRef = useRef(null)
  const backendTimersRef = useRef(new Map())
  const deleteQueueRef = useRef([])
  const syncingDraftIdsRef = useRef(new Set())
  const onToastRef = useRef(onToast)

  useEffect(() => {
    onToastRef.current = onToast
  }, [onToast])

  const emitToast = useCallback((type, title, message) => {
    if (typeof onToastRef.current !== 'function') return
    onToastRef.current(type, title, message)
  }, [])

  const setDrafts = useCallback((next) => {
    setDraftsState((current) => {
      const nextList = typeof next === 'function' ? next(current) : next
      const sorted = sortDrafts(Array.isArray(nextList) ? nextList : current)
      draftsRef.current = sorted
      return sorted
    })
  }, [])

  // Draft contents can contain client PII. They intentionally live only in
  // React memory between backend writes; browser storage is never a fallback.
  // Remove records created by older releases as soon as this hook mounts.
  const purgeLegacyDraftStorage = useCallback(() => {
    if (typeof window === 'undefined') return
    try {
      Object.keys(window.localStorage)
        .filter((key) => key.startsWith(STORAGE_DRAFT_PREFIX))
        .forEach((key) => window.localStorage.removeItem(key))
    } catch {}
  }, [])

  const scheduleLocalPersist = useCallback(() => {}, [])

  const addReceipt = useCallback((draftId, receipt) => {
    const payload = {
      id: newReceiptId(),
      status: receipt?.status || 'pending',
      at: nowISO(),
      label: receipt?.label || 'Action',
      error: receipt?.error || '',
      retry: receipt?.retry || null,
    }
    setDrafts((current) => current.map((draft) => {
      if (draft.draft_id !== draftId) return draft
      const updatedAt = nowISO()
      return {
        ...draft,
        receipts: [payload, ...(draft.receipts || [])].slice(0, MAX_RECEIPTS),
        updated_at: updatedAt,
        _dirty: true,
        _localOnly: true,
        _localUpdatedAt: updatedAt,
      }
    }))
    scheduleLocalPersist(draftId)
    return payload.id
  }, [scheduleLocalPersist, setDrafts])

  const updateReceipt = useCallback((draftId, receiptId, patch, options = {}) => {
    const markDirty = options.markDirty !== false
    setDrafts((current) => current.map((draft) => {
      if (draft.draft_id !== draftId) return draft
      const updatedAt = nowISO()
      return {
        ...draft,
        receipts: (draft.receipts || []).map((receipt) => (
          receipt.id === receiptId
            ? { ...receipt, ...patch, ...(patch.error !== undefined ? { error: sanitizeReceiptError(patch.error) } : {}) }
            : receipt
        )),
        ...(markDirty
          ? {
              updated_at: updatedAt,
              _dirty: true,
              _localOnly: true,
              _localUpdatedAt: updatedAt,
            }
          : {}),
      }
    }))
    scheduleLocalPersist(draftId)
  }, [scheduleLocalPersist, setDrafts])

  const markDraftDirty = useCallback((draftId, patch) => {
    const now = nowISO()
    let shouldSyncBackend = false
    let didChange = false
    setDrafts((current) => current.map((draft) => {
      if (draft.draft_id !== draftId) return draft
      const nextDraft = { ...draft, ...patch }
      const changed = Object.keys(patch || {}).some((key) => draft[key] !== nextDraft[key])
      if (!changed) return draft
      didChange = true
      shouldSyncBackend = hasMeaningfulDraftContent(nextDraft)
      return {
        ...nextDraft,
        updated_at: now,
        _dirty: shouldSyncBackend,
        _localOnly: true,
        _localUpdatedAt: now,
      }
    }))
    if (!didChange) return
    scheduleLocalPersist(draftId)
    if (draftId && shouldSyncBackend) {
      if (backendTimersRef.current.has(draftId)) {
        clearTimeout(backendTimersRef.current.get(draftId))
      }
      backendTimersRef.current.set(draftId, setTimeout(() => {
        backendTimersRef.current.delete(draftId)
        flushBackendDraft(draftId)
      }, BACKEND_WRITE_DEBOUNCE_MS))
    }
  }, [scheduleLocalPersist])

  const flushBackendDraft = useCallback(async (draftId, { force = false } = {}) => {
    if (!mountedRef.current) return
    if (!draftId) return
    const draft = draftsRef.current.find((entry) => entry.draft_id === draftId)
    if (!draft || (!draft._dirty && !force)) return
    if (!hasMeaningfulDraftContent(draft)) return
    if (draft._syncing || syncingDraftIdsRef.current.has(draftId)) return
    syncingDraftIdsRef.current.add(draftId)

    const backendPayload = { ...draft }
    delete backendPayload._dirty
    delete backendPayload._localOnly
    delete backendPayload._syncing
    delete backendPayload._syncError
    delete backendPayload._backendUpdatedAt
    delete backendPayload._localUpdatedAt
    delete backendPayload._syncRetryCount

    setDrafts((current) => current.map((entry) => (
      entry.draft_id === draftId ? { ...entry, _syncing: true, _syncError: null } : entry
    )))

    try {
      const saved = await upsertIntakeDraft(draftId, { payload: backendPayload })
      if (!mountedRef.current) return
      const normalized = normalizeDraftFromBackend(saved)
      setDrafts((current) => current.map((entry) => {
        if (entry.draft_id !== draftId) return entry
        return {
          ...entry,
          ...normalized,
          _dirty: false,
          _localOnly: false,
          _syncing: false,
          _syncError: null,
          _syncRetryCount: 0,
          _backendUpdatedAt: normalized.updated_at,
          _localUpdatedAt: nowISO(),
          updated_at: normalized.updated_at,
          receipts: entry.receipts || normalized.receipts,
        }
      }))
    } catch (error) {
      if (!mountedRef.current) return
      const normalizedError = normalizeApiError(error)
      const retryCount = Number(draft._syncRetryCount || 0)
      setDrafts((current) => current.map((entry) => {
        if (entry.draft_id !== draftId) return entry
        return {
          ...entry,
          _syncing: false,
          _syncError: normalizedError.message || 'Failed to save draft',
          _syncRetryCount: retryCount + 1,
        }
      }))
      if (
        isTransientError(normalizedError)
        && normalizedError.status !== RATE_LIMIT_STATUS
        && retryCount < 1
      ) {
        setTimeout(() => {
          if (!mountedRef.current) return
          flushBackendDraft(draftId)
        }, 10000)
      } else if (force && normalizedError.status !== RATE_LIMIT_STATUS) {
        emitToast('error', 'Draft save failed', normalizedError.message || 'Failed to save draft')
      }
    } finally {
      syncingDraftIdsRef.current.delete(draftId)
    }
  }, [emitToast])

  const updateDraftField = useCallback((draftId, patch) => {
    markDraftDirty(draftId, patch)
  }, [markDraftDirty])

  const createDraft = useCallback((seed = {}) => {
    const now = nowISO()
    const seededDraft = {
      ...DRAFT_FORM_DEFAULTS,
      ...seed,
    }
    const hasSeedContent = hasMeaningfulDraftContent(seededDraft)
    const draft = {
      ...seededDraft,
      draft_id: newDraftId(),
      created_at: now,
      updated_at: now,
      receipts: [],
      _dirty: hasSeedContent,
      _localOnly: true,
      _syncing: false,
      _syncError: null,
      _backendUpdatedAt: null,
      _localUpdatedAt: now,
      _syncRetryCount: 0,
    }
    setDrafts((current) => {
      const next = [draft, ...current].slice(0, MAX_DRAFTS)
      return next
    })
    setActiveDraftId(draft.draft_id)
    activeDraftIdRef.current = draft.draft_id
    return draft
  }, [setDrafts])

  const setActiveDraft = useCallback((draftId) => {
    if (!draftId || draftId === activeDraftIdRef.current) return
    const previousId = activeDraftIdRef.current
    if (previousId) flushBackendDraft(previousId, { force: true })
    activeDraftIdRef.current = draftId
    setActiveDraftId(draftId)
  }, [flushBackendDraft])

  const removeDraft = useCallback(async (draftId, skipBackendDelete = false) => {
    if (!draftId) return
    setDrafts((current) => {
      const next = current.filter((entry) => entry.draft_id !== draftId)
      return next
    })
    if (!skipBackendDelete) {
      try {
        await deleteIntakeDraft(draftId)
      } catch (error) {
        const normalizedError = normalizeApiError(error)
        if (normalizedError.status !== 404) {
          deleteQueueRef.current = [...new Set([...deleteQueueRef.current, draftId])]
        }
      }
    }
    if (activeDraftIdRef.current === draftId) {
      const fallback = draftsRef.current.find((entry) => entry.draft_id !== draftId)?.draft_id || null
      activeDraftIdRef.current = fallback
      setActiveDraftId(fallback)
      if (!fallback) createDraft()
    }
  }, [createDraft, setDrafts])

  const retryReceipt = useCallback(async (draftId, receiptId) => {
    const draft = draftsRef.current.find((entry) => entry.draft_id === draftId)
    const receipt = draft?.receipts?.find((entry) => entry.id === receiptId)
    if (!draft || !receipt?.retry) return
    const method = String(receipt.retry.method || '').toUpperCase()
    const targetId = parseReceiptTargetId(receipt.retry.url)
    const leadAssignId = parseAssignLeadId(receipt.retry.url)
    const url = String(receipt.retry.url || '')
    const payload = receipt.retry.payload || {}

    updateReceipt(draftId, receiptId, { status: 'pending', error: '' })

    try {
      if (method === 'PUT' && url.startsWith('/intake/drafts/') && targetId) {
        await upsertIntakeDraft(targetId, payload)
      } else if (method === 'DELETE' && url.startsWith('/intake/drafts/') && targetId) {
        await deleteIntakeDraft(targetId)
      } else if (method === 'POST' && url === '/intake/dashboard/calls') {
        await createIntakeDashboardCall(payload)
      } else if (method === 'POST' && leadAssignId) {
        await assignNextPartner(leadAssignId)
      } else {
        throw new Error('Unsupported retry action')
      }
      updateReceipt(draftId, receiptId, { status: 'ok', error: '' })
    } catch (error) {
      const normalizedError = normalizeApiError(error)
      updateReceipt(draftId, receiptId, { status: 'failed', error: normalizedError.message || 'Retry failed' })
    }
  }, [updateReceipt])

  const flushDeleteQueue = useCallback(async () => {
    if (!deleteQueueRef.current.length) return
    const queue = [...deleteQueueRef.current]
    const nextQueue = []
    for (const draftId of queue) {
      try {
        await deleteIntakeDraft(draftId)
      } catch (error) {
        const normalizedError = normalizeApiError(error)
        if (normalizedError.status !== 404) {
          nextQueue.push(draftId)
        }
      }
    }
    deleteQueueRef.current = nextQueue
  }, [])

  const executeOnBlur = useCallback((draftId) => {
    flushBackendDraft(draftId, { force: true })
  }, [flushBackendDraft])

  const hydrate = useCallback(async () => {
    let backendDrafts = []
    try {
      const rows = await getIntakeDrafts()
      backendDrafts = Array.isArray(rows) ? rows.map(normalizeDraftFromBackend) : []
    } catch {
      backendDrafts = []
    }

    const next = sortDrafts(backendDrafts)
    if (next.length === 0) {
      createDraft()
      setLoading(false)
      return
    }

    const activeId = activeDraftIdRef.current || next[0]?.draft_id || null
    setDrafts(next)
    setActiveDraftId(activeId)
    activeDraftIdRef.current = activeId
    setLoading(false)
  }, [
    createDraft,
    flushBackendDraft,
    flushDeleteQueue,
    setDrafts,
  ])

  useEffect(() => {
    mountedRef.current = true
    purgeLegacyDraftStorage()
    hydrate()
    return () => {
      mountedRef.current = false
      backendTimersRef.current.forEach((timer) => clearTimeout(timer))
      backendTimersRef.current.clear()
      syncingDraftIdsRef.current.clear()
    }
  }, [hydrate, purgeLegacyDraftStorage])

  const activeDraft = useMemo(() => (
    drafts.find((draft) => draft.draft_id === activeDraftId) || null
  ), [activeDraftId, drafts])

  return {
    drafts,
    loading,
    activeDraft,
    activeDraftId,
    storageHealthy,
    setActiveDraft,
    createDraft,
    updateDraftField,
    removeDraft,
    addReceipt,
    updateReceipt,
    retryReceipt,
    executeOnBlur,
    flushBackendDraft,
  }
}
