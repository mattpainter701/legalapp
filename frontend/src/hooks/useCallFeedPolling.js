import { useCallback, useEffect, useRef, useState } from 'react'
import { getRecentIntakeDashboardCallers } from '../api'

const POLL_MS = 30000

// Visibility-aware poll of the recent-callers feed. Returns the current callers,
// the ids that are new since the last successful fetch (empty on first load),
// loading state, and a manual refresh(). Never throws on a failed poll — keeps
// the last good feed so the desk never blanks on a transient error.
export function useCallFeedPolling(limit = 20) {
  const [callers, setCallers] = useState([])
  const [loading, setLoading] = useState(true)
  const [newCallIds, setNewCallIds] = useState([])
  const seenRef = useRef(null) // null until first successful load (so no alert on mount)
  const timerRef = useRef(null)

  const fetchOnce = useCallback(async () => {
    try {
      const data = await getRecentIntakeDashboardCallers({ limit })
      const next = data.callers || []
      const ids = next.map((c) => c.id)
      if (seenRef.current === null) {
        setNewCallIds([])
      } else {
        const fresh = ids.filter((id) => !seenRef.current.has(id))
        setNewCallIds(fresh)
      }
      seenRef.current = new Set(ids)
      setCallers(next)
    } catch {
      // keep last good feed; no alert
      setNewCallIds([])
    } finally {
      setLoading(false)
    }
  }, [limit])

  const start = useCallback(() => {
    if (timerRef.current) return
    timerRef.current = setInterval(() => {
      if (document.visibilityState === 'visible') fetchOnce()
    }, POLL_MS)
  }, [fetchOnce])

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    fetchOnce()
    start()
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        fetchOnce()
        start()
      } else {
        stop()
      }
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      stop()
    }
  }, [fetchOnce, start, stop])

  return { callers, loading, newCallIds, refresh: fetchOnce }
}
