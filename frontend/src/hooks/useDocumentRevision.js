import { useCallback, useEffect, useRef, useState } from 'react'
import { getMatterDocumentRevision } from '../api'

const ACTIVE_STATUSES = new Set(['processing'])
const POLL_DELAY_MS = 1500

const errorMessage = (error) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (typeof detail?.message === 'string') return detail.message
  return error?.message || 'Could not load this document revision.'
}

export default function useDocumentRevision(matterId, revisionId) {
  const [revision, setRevision] = useState(null)
  const [loading, setLoading] = useState(Boolean(revisionId))
  const [error, setError] = useState('')
  const timerRef = useRef(null)
  const requestRef = useRef(0)

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const refresh = useCallback(async ({ quiet = false } = {}) => {
    if (!matterId || !revisionId) return null
    const requestId = requestRef.current + 1
    requestRef.current = requestId
    if (!quiet) setLoading(true)
    try {
      const next = await getMatterDocumentRevision(matterId, revisionId)
      if (requestRef.current !== requestId) return null
      setRevision(next)
      setError('')
      return next
    } catch (requestError) {
      if (requestRef.current !== requestId) return null
      setError(errorMessage(requestError))
      return null
    } finally {
      if (requestRef.current === requestId) setLoading(false)
    }
  }, [matterId, revisionId])

  useEffect(() => {
    setRevision(null)
    setError('')
    if (!revisionId) {
      setLoading(false)
      return undefined
    }

    let cancelled = false
    const schedule = (next) => {
      stopPolling()
      if (cancelled || !ACTIVE_STATUSES.has(next?.status)) return
      timerRef.current = window.setTimeout(async () => {
        if (document.visibilityState !== 'visible') {
          schedule(next)
          return
        }
        const refreshed = await refresh({ quiet: true })
        if (!cancelled) schedule(refreshed)
      }, POLL_DELAY_MS)
    }

    refresh().then((next) => {
      if (!cancelled) schedule(next)
    })

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refresh({ quiet: true }).then((next) => {
          if (!cancelled) schedule(next)
        })
      } else {
        stopPolling()
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      cancelled = true
      requestRef.current += 1
      stopPolling()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [refresh, revisionId, stopPolling])

  return {
    revision,
    setRevision,
    loading,
    error,
    refresh,
  }
}
