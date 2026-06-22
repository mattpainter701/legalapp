import { useCallback, useEffect, useRef, useState } from 'react'

// In-page call alerts: a toast queue + a WebAudio chime, with a mute toggle
// persisted per tenant. No browser-notification permission needed. Audio only
// plays after the first user gesture (browser autoplay policy); `soundReady`
// reflects whether audio is unlocked so the UI can show a hint until then.
export function useCallAlerts(tenantId) {
  const muteKey = `intake.mute.${tenantId || 'unknown'}`
  const [muted, setMuted] = useState(() => {
    try {
      return localStorage.getItem(muteKey) === '1'
    } catch {
      return false
    }
  })
  const [toasts, setToasts] = useState([])
  const [soundReady, setSoundReady] = useState(false)
  const ctxRef = useRef(null)
  const nextId = useRef(1)

  // Unlock audio on the first user gesture.
  useEffect(() => {
    const unlock = () => {
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext
        if (Ctx && !ctxRef.current) ctxRef.current = new Ctx()
        if (ctxRef.current?.state === 'suspended') ctxRef.current.resume()
        setSoundReady(true)
      } catch {
        /* ignore — toasts still work */
      }
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
    window.addEventListener('pointerdown', unlock)
    window.addEventListener('keydown', unlock)
    return () => {
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
  }, [])

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      const next = !m
      try {
        localStorage.setItem(muteKey, next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }, [muteKey])

  const playChime = useCallback(() => {
    const ctx = ctxRef.current
    if (!ctx) return
    try {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = 880
      gain.gain.setValueAtTime(0.0001, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start()
      osc.stop(ctx.currentTime + 0.36)
    } catch {
      /* ignore */
    }
  }, [])

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  // Call with an array of new caller objects ({id, caller_name, result, ...}).
  const notify = useCallback(
    (newCallers) => {
      if (!newCallers || newCallers.length === 0) return
      const added = newCallers.map((c) => ({
        id: nextId.current++,
        callId: c.id,
        title: c.caller_name || 'Unknown caller',
        status: c.result || c.lead_status || 'logged',
      }))
      setToasts((list) => [...added, ...list].slice(0, 4))
      added.forEach((t) => setTimeout(() => dismiss(t.id), 6000))
      if (!muted) playChime()
    },
    [muted, playChime, dismiss]
  )

  return { toasts, notify, dismiss, muted, toggleMute, soundReady }
}
