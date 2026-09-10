import { useCallback, useEffect, useState } from 'react'
import { Info, X } from 'lucide-react'
import { useAuth } from '../App'
import { getReleaseWindow } from '../api'

const DISMISS_PREFIX = 'lawhand.release-window.dismissed'
const POLL_MS = 30_000

export function dismissedReleaseWindowKey(userId, windowId) {
  return `${DISMISS_PREFIX}.${userId}.${windowId}`
}

function wasDismissed(userId, windowId) {
  try {
    return window.localStorage.getItem(dismissedReleaseWindowKey(userId, windowId)) === '1'
  } catch {
    return false
  }
}

function rememberDismissed(userId, windowId) {
  try {
    window.localStorage.setItem(dismissedReleaseWindowKey(userId, windowId), '1')
  } catch {
    // The advisory banner stays optional when device storage is unavailable.
  }
}

// Advisory deploy heads-up, shown to EVERY signed-in user — including portal
// clients — while a release is in flight. Unlike ReleaseAnnouncement this is
// never modal: it must not interrupt whatever the user is doing.
export default function ReleaseWindowBanner() {
  const { user } = useAuth()
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    let mounted = true
    setNotice(null)
    if (!user?.id) return () => { mounted = false }

    const poll = () => {
      getReleaseWindow()
        .then((data) => {
          if (!mounted) return
          if (data?.active && data.window_id && !wasDismissed(user.id, data.window_id)) {
            setNotice({ windowId: data.window_id, message: data.message })
          } else {
            setNotice(null)
          }
        })
        .catch(() => {
          // A maintenance notice must never interfere with sign-in or work.
          if (mounted) setNotice(null)
        })
    }

    poll()
    const interval = window.setInterval(poll, POLL_MS)
    return () => {
      mounted = false
      window.clearInterval(interval)
    }
  }, [user?.id])

  const dismiss = useCallback(() => {
    if (user?.id && notice?.windowId) rememberDismissed(user.id, notice.windowId)
    setNotice(null)
  }, [notice?.windowId, user?.id])

  if (!notice) return null

  return (
    <div
      role="status"
      className="fixed inset-x-0 top-0 z-[75] flex items-start justify-center gap-3 border-b border-brand-accent/30 bg-brand-bg-soft/95 px-4 py-2.5 backdrop-blur-sm"
    >
      <Info size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-ink" />
      <p className="max-w-3xl text-sm leading-snug text-brand-ink">{notice.message}</p>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss maintenance notice"
        className="tap-target shrink-0 rounded-lg text-brand-muted hover:bg-brand-line/50 hover:text-brand-ink"
      >
        <X size={16} />
      </button>
    </div>
  )
}
