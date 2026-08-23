const SESSION_KEY = 'lawhand.marketing.session'
const EVENT_NAMES = new Set(['demo_cta_clicked', 'demo_form_started', 'demo_form_submitted'])

function createSessionId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  if (!window.crypto?.getRandomValues) return null

  const bytes = window.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function sessionId() {
  try {
    const existing = window.sessionStorage.getItem(SESSION_KEY)
    if (existing) return existing
    const created = createSessionId()
    if (!created) return null
    window.sessionStorage.setItem(SESSION_KEY, created)
    return created
  } catch {
    return createSessionId()
  }
}

export function campaignProperties(search = window.location.search) {
  const params = new URLSearchParams(search)
  return Object.fromEntries(
    ['utm_source', 'utm_medium', 'utm_campaign']
      .map((key) => [key, params.get(key)])
      .filter(([, value]) => value),
  )
}

export function trackMarketingEvent(name, properties = {}) {
  if (!EVENT_NAMES.has(name) || typeof window === 'undefined') return
  const currentSessionId = sessionId()
  if (!currentSessionId) return
  const payload = {
    name,
    session_id: currentSessionId,
    page: `${window.location.pathname}${window.location.search}`.slice(0, 500),
    properties: { ...campaignProperties(), ...properties },
  }

  window.dataLayer?.push?.({ event: name, ...properties })
  window.dispatchEvent(new CustomEvent('lawhand:marketing', { detail: payload }))

  const body = JSON.stringify(payload)
  if (navigator.sendBeacon) {
    navigator.sendBeacon('/api/marketing/events', new Blob([body], { type: 'application/json' }))
    return
  }
  fetch('/api/marketing/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    credentials: 'same-origin',
    keepalive: true,
  }).catch(() => {})
}
