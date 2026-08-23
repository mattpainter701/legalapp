const SESSION_KEY = 'lawhand.marketing.session'
const EVENT_NAMES = new Set(['demo_cta_clicked', 'demo_form_started', 'demo_form_submitted'])

function sessionId() {
  try {
    const existing = window.sessionStorage.getItem(SESSION_KEY)
    if (existing) return existing
    const created = window.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
      const random = Math.floor(Math.random() * 16)
      return (token === 'x' ? random : ((random & 0x3) | 0x8)).toString(16)
    })
    window.sessionStorage.setItem(SESSION_KEY, created)
    return created
  } catch {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
      const random = Math.floor(Math.random() * 16)
      return (token === 'x' ? random : ((random & 0x3) | 0x8)).toString(16)
    })
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
  const payload = {
    name,
    session_id: sessionId(),
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