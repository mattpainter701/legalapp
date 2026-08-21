import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Check, Sparkles, X } from 'lucide-react'
import { useAuth } from '../App'
import { getAppVersion } from '../api'

const SEEN_RELEASE_PREFIX = 'lawhand.release.seen'

export function seenReleaseKey(userId, releaseId) {
  return `${SEEN_RELEASE_PREFIX}.${userId}.${releaseId}`
}

function wasSeen(userId, releaseId) {
  try {
    return window.localStorage.getItem(seenReleaseKey(userId, releaseId)) === '1'
  } catch {
    return false
  }
}

function rememberSeen(userId, releaseId) {
  try {
    window.localStorage.setItem(seenReleaseKey(userId, releaseId), '1')
  } catch {
    // Release announcements remain optional when device storage is unavailable.
  }
}

export default function ReleaseAnnouncement() {
  const { user } = useAuth()
  const [release, setRelease] = useState(null)
  const dialogRef = useRef(null)
  const closeRef = useRef(null)
  const previousFocusRef = useRef(null)

  useEffect(() => {
    let mounted = true
    setRelease(null)
    if (!user?.id || user?.role === 'client') return () => { mounted = false }

    getAppVersion()
      .then((data) => {
        const latest = data?.latest_release
        if (
          mounted
          && latest?.id
          && latest.is_recent
          && !wasSeen(user.id, latest.id)
        ) {
          setRelease(latest)
        }
      })
      .catch(() => {
        // A release notice must never interfere with sign-in or navigation.
      })

    return () => {
      mounted = false
    }
  }, [user?.id, user?.role])

  const dismiss = useCallback(() => {
    if (user?.id && release?.id) rememberSeen(user.id, release.id)
    setRelease(null)
  }, [release?.id, user?.id])

  useEffect(() => {
    if (!release) return undefined

    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        dismiss()
        return
      }
      if (event.key !== 'Tab') return

      const focusable = Array.from(dialogRef.current?.querySelectorAll(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
      ) || [])
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      previousFocusRef.current?.focus()
    }
  }, [dismiss, release])

  if (!release) return null

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-brand-ink/55 p-4 backdrop-blur-sm" onClick={dismiss}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="release-announcement-title"
        aria-describedby="release-announcement-summary"
        className="max-h-[calc(100dvh-2rem)] w-full max-w-lg overflow-y-auto rounded-3xl border border-brand-line bg-brand-surface shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="relative overflow-hidden border-b border-brand-line bg-brand-bg-soft px-6 py-6 sm:px-8">
          <div className="absolute -right-10 -top-12 h-36 w-36 rounded-full bg-brand-accent/10" aria-hidden="true" />
          <div className="relative flex items-start justify-between gap-4">
            <div>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-ink px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-white">
                <Sparkles size={12} aria-hidden="true" /> What’s new
              </span>
              <h2 id="release-announcement-title" className="mt-4 font-serif text-2xl font-bold text-brand-ink">
                {release.title}
              </h2>
              <p className="mt-1 text-xs font-semibold uppercase tracking-wider text-brand-muted">
                LawHand {release.version}
              </p>
            </div>
            <button
              ref={closeRef}
              type="button"
              onClick={dismiss}
              aria-label="Close release announcement"
              className="tap-target shrink-0 rounded-xl text-brand-muted hover:bg-brand-line/50 hover:text-brand-ink"
            >
              <X size={19} />
            </button>
          </div>
          <p id="release-announcement-summary" className="relative mt-4 text-sm leading-relaxed text-brand-ink-2">
            {release.summary}
          </p>
        </div>

        <div className="px-6 py-6 sm:px-8">
          <ul className="space-y-4">
            {(release.highlights || []).map((highlight) => (
              <li key={highlight.title} className="flex items-start gap-3">
                <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-green/10 text-brand-green">
                  <Check size={13} strokeWidth={3} aria-hidden="true" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-brand-ink">{highlight.title}</p>
                  <p className="mt-0.5 text-sm leading-relaxed text-brand-ink-2">{highlight.description}</p>
                </div>
              </li>
            ))}
          </ul>

          <div className="mt-6 flex flex-col-reverse gap-3 border-t border-brand-line pt-5 sm:flex-row sm:items-center sm:justify-between">
            <button type="button" onClick={dismiss} className="px-4 py-2 text-sm font-semibold text-brand-muted hover:text-brand-ink">
              Got it
            </button>
            <Link
              to="/profile#release-notes"
              onClick={dismiss}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand-ink px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-ink-2"
            >
              View release notes <ArrowRight size={15} aria-hidden="true" />
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
