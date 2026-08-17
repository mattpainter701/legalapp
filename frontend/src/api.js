import axios from 'axios'

const BASE_URL = (import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '')
export const API_BASE_URL = BASE_URL

const getHeaderValue = (headers, name) => {
  if (!headers) return undefined
  if (typeof headers.get === 'function') {
    return headers.get(name) || headers.get(name.toLowerCase()) || headers.get(name.toUpperCase())
  }
  const lower = String(name).toLowerCase()
  const upper = String(name).toUpperCase()
  if (Object.prototype.hasOwnProperty.call(headers, name)) return headers[name]
  if (Object.prototype.hasOwnProperty.call(headers, lower)) return headers[lower]
  if (Object.prototype.hasOwnProperty.call(headers, upper)) return headers[upper]
  return undefined
}

const buildLegacyAuthHeaders = (headers = {}) => headers

const readValidationDetails = (detail) => {
  if (!Array.isArray(detail)) return []
  return detail
    .map((entry) => {
      const message = entry?.msg || entry?.message
      const field = Array.isArray(entry?.loc) ? entry.loc.filter(Boolean).join('.') : ''
      if (!message) return ''
      return field ? `${field}: ${message}` : String(message)
    })
    .filter(Boolean)
}

// A proxy/gateway that rejects a request before it reaches the app (nginx 429,
// 502, 504, etc.) returns its own HTML error page, not JSON. Axios still hands
// that page to us as a string in `data`. Never surface that markup as a user
// message — detect it and fall back to a short status-based message instead.
const isMarkupBody = (value) => (
  typeof value === 'string' && /^\s*<(!doctype|html)/i.test(value)
)

const STATUS_FALLBACK_MESSAGES = {
  429: 'Too many requests right now. Please wait a moment and try again.',
  502: 'The server is temporarily unavailable. Please try again shortly.',
  503: 'The server is temporarily unavailable. Please try again shortly.',
  504: 'The request timed out. Please try again.',
}

export const normalizeApiError = (errorOrResponse) => {
  const response = errorOrResponse?.response || errorOrResponse
  const data = response?.data
  const status = response?.status
  const headers = response?.headers

  const hasStructuredData = data && typeof data === 'object' && !Array.isArray(data)
  const rawDetail = hasStructuredData ? (data.detail ?? data.error ?? data.message ?? '') : data
  const dataDetail = isMarkupBody(rawDetail) ? '' : rawDetail
  const validationErrors = readValidationDetails(dataDetail)
  const detail = validationErrors.length
    ? validationErrors.join('; ')
    : (typeof dataDetail === 'string' ? dataDetail.slice(0, 500) : '')
  const statusMessage = STATUS_FALLBACK_MESSAGES[status] || response?.statusText || `HTTP ${status || 'error'}`
  const message = validationErrors.length
    ? validationErrors.join('; ')
    : detail || statusMessage || 'Request failed'

  const requestId = getHeaderValue(headers, 'x-request-id') || data?.request_id || data?.requestId
  const errorId = getHeaderValue(headers, 'x-error-id') || data?.error_id || data?.errorId

  const normalized = errorOrResponse instanceof Error ? errorOrResponse : new Error(message)
  normalized.message = String(message)
  normalized.name = 'ApiError'
  normalized.status = status
  normalized.request_id = requestId
  normalized.error_id = errorId
  normalized.detail = detail
  normalized.validationErrors = validationErrors

  if (!normalized.response) {
    normalized.response = {
      status,
      data: data ?? { detail },
      headers,
      statusText: response?.statusText,
    }
  } else if (normalized.response && normalized.response.data == null && data != null) {
    normalized.response.data = data
  }
  if (normalized.response && normalized.response.data && typeof normalized.response.data === 'object' && !Array.isArray(normalized.response.data)) {
    if (!normalized.response.data.request_id && requestId) normalized.response.data.request_id = requestId
    if (!normalized.response.data.error_id && errorId) normalized.response.data.error_id = errorId
    normalized.response.data.detail = detail || normalized.response.data.detail
    normalized.response.data.validationErrors = validationErrors
    if (!normalized.response.data.message && normalized.message) normalized.response.data.message = normalized.message
  }
  return normalized
}

const api = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true, // Allow httpOnly cookies to be sent with requests
})

const isAiOperationPath = (url = '') => (
  /^\/conversations\/[^/]+\/messages(?:\/stream)?$/.test(url)
  || /^\/plugins\/[^/]+\/[^/]+$/.test(url)
  || url === '/office/plans'
)

const newOperationId = () => globalThis.crypto?.randomUUID?.()
  || `${Date.now()}-${Math.random().toString(36).slice(2)}`

api.interceptors.request?.use?.((config) => {
  if (
    String(config.method || 'get').toLowerCase() === 'post'
    && isAiOperationPath(config.url)
    && !config.headers?.['X-Idempotency-Key']
  ) {
    config.headers = config.headers || {}
    config.headers['X-Idempotency-Key'] = newOperationId()
  }
  return config
})

// Auth lives entirely in httpOnly cookies set by the backend — the SPA never
// reads or stores the access/refresh token in localStorage, so an XSS payload
// cannot exfiltrate a live session. `withCredentials: true` above is what
// actually authenticates every request; no Authorization header is needed.
// USE_LEGACY_TOKEN_FALLBACK is a dev-only opt-in escape hatch (off by default,
// and always off in production) for local setups where cookies don't work
// across ports.
if (typeof window !== 'undefined') {
  window.localStorage.removeItem('token')
  window.localStorage.removeItem('user')
}

// Response interceptor: on 401, attempt a single rotating-refresh, then retry the
// original request once. Concurrent 401s share one in-flight refresh (single-flight)
// so we never fire multiple /auth/refresh calls. If refresh fails, redirect to login.
let refreshPromise = null

// Paths for which a 401 must NOT trigger a refresh attempt (would loop / is terminal).
const NO_REFRESH_PATHS = ['/auth/refresh', '/auth/login', '/auth/register', '/auth/logout', '/demo/session']

const clearAuthState = () => {
  if (typeof window !== 'undefined') {
    window.localStorage.removeItem('token')
    window.localStorage.removeItem('user')
  }
}

const redirectToLogin = () => {
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

const refreshAuthSession = async () => {
  if (!refreshPromise) {
    refreshPromise = api.post('/auth/refresh').finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const normalizedError = normalizeApiError(error)
    const { response, config } = normalizedError
    if (!response || response.status !== 401 || !config) {
      return Promise.reject(normalizedError)
    }
    const url = config.url || ''
    if (config._retried) {
      // Already retried once; give up and return the user to login.
      if (config._suppressAuthRedirect) {
        clearAuthState()
      } else {
        redirectToLogin()
      }
      return Promise.reject(normalizedError)
    }
    if (NO_REFRESH_PATHS.some((p) => url.includes(p))) {
      // Auth endpoint failures should surface to the current form instead of
      // forcing a reload that clears the page-level error message.
      clearAuthState()
      return Promise.reject(normalizedError)
    }

    try {
      await refreshAuthSession()
    } catch (refreshError) {
      if (config._suppressAuthRedirect) {
        clearAuthState()
      } else {
        redirectToLogin()
      }
      return Promise.reject(normalizeApiError(refreshError))
    }

    // Refresh succeeded (new cookies set) — retry the original request once.
    config._retried = true
    if (typeof config.headers?.delete === 'function') {
      config.headers.delete('Authorization')
    } else if (config.headers) {
      delete config.headers.Authorization
    }
    return api(config)
  }
)

// Auth
export const getAppVersion = () =>
  api.get('/version', { _suppressAuthRedirect: true }).then((r) => r.data)

export const getMe = (config = {}) => api.get('/auth/me', config).then((r) => r.data)
export const createDemoSession = (data) => api.post('/demo/session', data).then((r) => r.data)

// Professional context is deliberately kept with the authenticated user. It is
// used to tailor assistance across conversations, not stored in the browser.
export const updateMe = (data) => api.patch('/auth/me', data).then((r) => r.data)

export const register = (data) =>
  api.post('/auth/register', data).then((r) => r.data)

export const signupWithPlan = (data) =>
  api.post('/auth/signup/plan', data).then((r) => r.data)

export const login = (data) =>
  api.post('/auth/login', data).then((r) => r.data)

export const exchangeOAuthCode = (code) =>
  api.post('/auth/oauth/exchange', { code }).then((r) => r.data)

export const loginMicrosoft = () => {
  window.location.href = `${BASE_URL}/auth/microsoft/login`
}

export const loginGoogle = () => {
  window.location.href = `${BASE_URL}/auth/google/login`
}

export const checkOAuthStatus = () =>
  api.get('/auth/me').then(() => true).catch(() => false)

export const logout = () => api.post('/auth/logout').then((r) => r.data)

export const forgotPassword = (email) =>
  api.post('/auth/forgot-password', { email }).then((r) => r.data)

export const resetPassword = (token, password) =>
  api.post('/auth/reset-password', { token, password }).then((r) => r.data)

// Conversations
export const getConversations = (params) =>
  api.get('/conversations', { params }).then((r) => r.data)

export const createConversation = (data) =>
  api.post('/conversations', typeof data === 'string' ? { title: data } : (data || {})).then((r) => r.data)

export const getConversation = (id) =>
  api.get(`/conversations/${id}`).then((r) => r.data)

export const updateConversation = (id, data) =>
  api.patch(`/conversations/${id}`, data).then((r) => r.data)

export const sendMessage = (conversationId, content, includePublic = true, usePremium = false, attachmentIds = []) =>
  api
    .post(`/conversations/${conversationId}/messages`, {
      content,
      include_public: includePublic,
      use_premium_llm: usePremium,
      attachment_ids: attachmentIds,
    })
    .then((r) => r.data)

const streamErrorFromResponse = async (response) => {
  const status = response?.status
  const statusText = response?.statusText
  const headers = response?.headers
  const raw = await response.text().catch(() => '')

  let data = raw
  const contentType = (response?.headers?.get('content-type') || '')
    .toLowerCase()
  if (raw && contentType.includes('json')) {
    try {
      data = JSON.parse(raw)
    } catch {
      data = raw
    }
  }

  const isJsonObject = data && typeof data === 'object' && !Array.isArray(data)
  const detail = isJsonObject && (data.detail || data.error || data.message)
    ? (data.detail || data.error || data.message)
    : raw || statusText || `HTTP error! status: ${status}`

  const baseError = new Error(typeof detail === 'string' ? detail : `HTTP error! status: ${status}`)
  baseError.response = {
    status,
    statusText,
    headers,
    data: isJsonObject ? data : { detail, raw },
  }
  const normalizedError = normalizeApiError(baseError)
  if (status === 504) {
    normalizedError.message = 'The AI service is warming up. Please try your query again in a moment.'
    if (normalizedError.response.data && typeof normalizedError.response.data === 'object' && !Array.isArray(normalizedError.response.data)) {
      normalizedError.response.data.detail = normalizedError.message
    }
  }
  return normalizedError
}

export const streamMessage = async function* (
  conversationId,
  content,
  includePublic = true,
  usePremium = false,
  attachmentIds = [],
  { signal, inactivityTimeoutMs = 120_000 } = {},
) {
  const body = JSON.stringify({
    content,
    include_public: includePublic,
    use_premium_llm: usePremium,
    attachment_ids: attachmentIds,
  })
  const operationId = newOperationId()
  const request = () => fetch(`${BASE_URL}/conversations/${conversationId}/messages/stream`, {
    method: 'POST',
    credentials: 'include',
    headers: buildLegacyAuthHeaders({
      'Content-Type': 'application/json',
      'X-Idempotency-Key': operationId,
    }),
    body,
    signal,
  })
  let response = await request()
  if (response.status === 401) {
    try {
      await refreshAuthSession()
      response = await request()
    } catch {
      redirectToLogin()
    }
  }

  if (!response.ok) {
    if (response.status === 401) {
      redirectToLogin()
    }
    throw await streamErrorFromResponse(response)
  }

  if (!response.body) {
    throw new Error('The assistant stream did not include a response body. Please retry.')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let sawTerminalEvent = false
  let reachedEndOfBody = false

  const readWithInactivityTimeout = async () => {
    const timeoutMs = Number(inactivityTimeoutMs)
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return reader.read()

    let timeoutId
    try {
      return await Promise.race([
        reader.read(),
        new Promise((_, reject) => {
          timeoutId = setTimeout(() => {
            reject(new Error(
              'The assistant stopped sending updates for too long. Please retry.',
            ))
          }, timeoutMs)
        }),
      ])
    } finally {
      clearTimeout(timeoutId)
    }
  }

  const decodeLine = (rawLine) => {
    const line = String(rawLine || '').replace(/\r$/, '')
    if (!line.startsWith('data:')) return null

    const data = line.slice(5).replace(/^ /, '')
    if (data.startsWith('[PROGRESS]')) {
      try {
        return { value: JSON.parse(data.slice('[PROGRESS]'.length)), terminal: false }
      } catch {
        // Ignore malformed progress metadata; token streaming should continue.
        return null
      }
    }
    if (data.startsWith('[TOKEN]')) {
      try {
        return { value: JSON.parse(data.slice('[TOKEN]'.length)), terminal: false }
      } catch {
        // Ignore malformed answer chunks without terminating the stream.
        return null
      }
    }
    if (data === '[STREAM_COMPLETE]') {
      return { value: data, terminal: true }
    }

    // The original frontend contract used `[ERROR]message`, while the backend
    // emits `[ERROR: message]`. Normalize both so an error cannot be mistaken
    // for answer text or an ordinary end-of-file.
    const bracketError = data.match(/^\[ERROR\]\s*(.*)$/s)
    const colonError = data.match(/^\[ERROR:\s*(.*?)\]\s*$/s)
    if (bracketError || colonError) {
      const message = (bracketError?.[1] || colonError?.[1] || '').trim()
      return {
        value: `[ERROR]${message || 'The assistant could not complete the response. Please retry.'}`,
        terminal: true,
      }
    }

    return data ? { value: data, terminal: false } : null
  }

  try {
    while (true) {
      const { done, value } = await readWithInactivityTimeout()
      if (done) {
        reachedEndOfBody = true
        buffer += decoder.decode()
        if (buffer) {
          const finalLines = buffer.split('\n')
          buffer = ''
          for (const line of finalLines) {
            const finalEvent = decodeLine(line)
            if (!finalEvent) continue
            sawTerminalEvent = sawTerminalEvent || finalEvent.terminal
            yield finalEvent.value
            if (finalEvent.terminal) return
          }
        }
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const event = decodeLine(line)
        if (!event) continue
        sawTerminalEvent = sawTerminalEvent || event.terminal
        yield event.value
        if (event.terminal) return
      }
    }

    if (!sawTerminalEvent) {
      throw new Error('The assistant stream ended before completion. Please retry.')
    }
  } finally {
    if (!reachedEndOfBody) {
      try {
        await reader.cancel()
      } catch {
        // The fetch may already have been aborted by conversation navigation.
      }
    }
    reader.releaseLock()
  }
}

// Global handler for orphaned postMessage responses (e.g. browser extensions, Vite HMR)
// that throw "Cannot respond. No request with id" — suppress them so they don't
// appear as uncaught promise rejections in the console.
if (typeof window !== 'undefined') {
  window.addEventListener('unhandledrejection', (event) => {
    const msg = event?.reason?.message || ''
    if (msg.includes('Cannot respond') && msg.includes('No request with id')) {
      event.preventDefault()
    }
  })
}

export const deleteConversation = (id) =>
  api.delete(`/conversations/${id}`).then((r) => r.data)

// Documents
export const getDocuments = () =>
  api.get('/documents').then((r) => r.data.documents)

export const uploadDocument = (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return api
    .post('/documents/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data)
}

export const deleteDocument = (id) =>
  api.delete(`/documents/${id}`).then((r) => r.data)

export const uploadChatAttachment = (conversationId, file) => {
  const formData = new FormData()
  formData.append('file', file)
  return api
    .post(`/conversations/${conversationId}/attachments`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data)
}

// Admin
export const getAdminUsers = () =>
  api.get('/admin/users').then((r) => r.data.users ?? r.data)

export const deactivateUser = (userId, force = false) =>
  api.delete(`/admin/users/${userId}`, { params: { force } })

export const reactivateUser = (userId) =>
  api.post(`/admin/users/${userId}/reactivate`).then((r) => r.data)

export const updateUser = (userId, data) =>
  api.patch(`/admin/users/${userId}`, data).then((r) => r.data)

export const setUserBillingRate = (userId, rate) =>
  api.patch(`/admin/users/${userId}/billing-rate`, null, { params: { default_billing_rate: rate } }).then((r) => r.data)

export const inviteUser = (data) =>
  api.post('/admin/users/invite', data).then((r) => r.data)

export const getUsageByUser = (days = 30) =>
  api.get('/admin/usage/by-user', { params: { days } }).then((r) => r.data)

export const getAlertConfig = () =>
  api.get('/admin/alerts/config').then((r) => r.data)

export const updateAlertConfig = (data) =>
  api.put('/admin/alerts/config', data).then((r) => r.data)

export const getAdminUsage = () =>
  api.get('/admin/usage').then((r) => r.data)

export const getAdminTenant = () =>
  api.get('/admin/tenant').then((r) => r.data)

// Roles (RBAC)
export const listRoles = () => api.get('/admin/roles').then((r) => r.data)
export const createRole = (body) => api.post('/admin/roles', body).then((r) => r.data)
export const updateRole = (id, body) =>
  api.put(`/admin/roles/${id}`, body).then((r) => r.data)
export const deleteRole = (id) => api.delete(`/admin/roles/${id}`).then((r) => r.data)
export const assignUserRoles = (userId, roleIds) =>
  api.put(`/admin/roles/assign/${userId}`, { role_ids: roleIds }).then((r) => r.data)
export const getAdminSettings = () =>
  api.get('/admin/settings').then((r) => r.data)
export const updateAdminSettings = (body) =>
  api.put('/admin/settings', body).then((r) => r.data)

// Onboarding
export const getOnboardingStatus = () =>
  api.get('/admin/onboarding/status').then((r) => r.data)
export const completeOnboarding = () =>
  api.post('/admin/onboarding/complete').then((r) => r.data)
export const skipOnboarding = () =>
  api.post('/admin/onboarding/skip').then((r) => r.data)
export const updateOnboardingStep = (step) =>
  api.post(`/admin/onboarding/step/${step}`).then((r) => r.data)

// Licensing
export const getLicensingInfo = () =>
  api.get('/admin/licensing').then((r) => r.data)
export const toggleUserLicense = (userId, licenseActive) =>
  api.put(`/admin/users/${userId}/license`, { license_active: licenseActive }).then((r) => r.data)
export const toggleUserPremium = (userId, premiumEnabled) =>
  api.put(`/admin/users/${userId}/premium`, { premium_ai_enabled: premiumEnabled }).then((r) => r.data)
export const updateSeatCount = (count) =>
  api.put('/admin/licensing/seats', { flat_seat_count: count }).then((r) => r.data)

// Integrations & Permissions
export const getIntegrationsHealth = () =>
  api.get('/admin/integrations/health').then((r) => r.data)
export const getIntegrationReadiness = () =>
  api.get('/admin/integrations/readiness').then((r) => r.data)
export const getAdminPermissions = () =>
  api.get('/admin/permissions').then((r) => r.data)
export const triggerUserSync = () =>
  api.post('/scheduler/agents/user-sync/run').then((r) => r.data)
export const retryCloudInit = () =>
  api.post('/integrations/cloud-init/retry').then((r) => r.data)

// External imports
export const uploadTabs3ImportBundle = ({ file, passphrase, accountingMode = 'tabs3_reference' }) => {
  const form = new FormData()
  form.append('file', file)
  form.append('accounting_mode', accountingMode)
  if (passphrase) form.append('passphrase', passphrase)
  return api.post('/imports/tabs3/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data)
}
export const getExternalImportRun = (runId) =>
  api.get(`/imports/${runId}`).then((r) => r.data)
export const getExternalImportTables = (runId) =>
  api.get(`/imports/${runId}/tables`).then((r) => r.data)
export const getExternalImportRows = (runId, sourceTable, limit = 25) =>
  api.get(`/imports/${runId}/tables/${sourceTable}/rows`, { params: { limit } }).then((r) => r.data)
export const reconcileExternalImport = (runId) =>
  api.get(`/imports/${runId}/reconcile`).then((r) => r.data)

// ── Microsoft Teams ───────────────────────────────────────────────────────────
export const getIntegrationStatus = () =>
  api.get('/integrations/status').then((r) => r.data)
export const getTeamsTeams = () =>
  api.get('/integrations/teams/teams').then((r) => r.data)
export const getTeamsChannels = (teamId) =>
  api.get(`/integrations/teams/teams/${teamId}/channels`).then((r) => r.data)
export const createTeamsChannel = (data) =>
  api.post('/integrations/teams/channels', data).then((r) => r.data)
export const getTeamsLinks = () =>
  api.get('/integrations/teams/links').then((r) => r.data)
export const createTeamsLink = (data) =>
  api.post('/integrations/teams/links', data).then((r) => r.data)
export const deleteTeamsLink = (id) =>
  api.delete(`/integrations/teams/links/${id}`).then((r) => r.data)
export const getTeamsNotificationSettings = () =>
  api.get('/integrations/teams/notification-settings').then((r) => r.data)
export const updateTeamsNotificationSettings = (settings) =>
  api.put('/integrations/teams/notification-settings', { settings }).then((r) => r.data)
export const sendTeamsTestMessage = (data) =>
  api.post('/integrations/teams/test-message', data).then((r) => r.data)

// ── Zoom ─────────────────────────────────────────────────────────────────────
export const getZoomStatus = () =>
  api.get('/integrations/zoom/status').then((r) => r.data)
export const connectZoomIntegration = (intent = 'user') => {
  window.location.href = `${BASE_URL}/integrations/zoom/connect?intent=${intent}`
}
export const disconnectZoomIntegration = () =>
  api.post('/integrations/zoom/disconnect').then((r) => r.data)
export const getZoomPhoneStatus = () =>
  api.get('/integrations/zoom-phone/status').then((r) => r.data)
export const saveZoomPhoneAppCredentials = (data) =>
  api.put('/integrations/zoom-phone/app-credentials', data).then((r) => r.data)
export const clearZoomPhoneAppCredentials = () =>
  api.delete('/integrations/zoom-phone/app-credentials').then((r) => r.data)
export const connectZoomPhoneIntegration = () => {
  window.location.href = `${BASE_URL}/integrations/zoom-phone/connect`
}
export const testZoomPhoneIntegration = () =>
  api.post('/integrations/zoom-phone/test').then((r) => r.data)
export const disconnectZoomPhoneIntegration = () =>
  api.post('/integrations/zoom-phone/disconnect').then((r) => r.data)

// Customer LLM
export const configureCustomerLLM = (config) =>
  api.post('/admin/customer-llm/configure', config).then((r) => r.data)
export const resetCustomerLLM = () =>
  api.delete('/admin/customer-llm/configure').then((r) => r.data)

// ── Plugin API ────────────────────────────────────────────────────────────────
export const getPlugins = () => api.get('/plugins').then((r) => r.data)
export const getPluginProfile = (plugin) => api.get(`/plugins/${plugin}/profile`).then((r) => r.data)
export const savePluginProfile = (plugin, data) => api.put(`/plugins/${plugin}/profile`, data).then((r) => r.data)
export const updatePluginEntitlement = (plugin, data) => api.put(`/plugins/${plugin}/entitlement`, data).then((r) => r.data)
export const getPluginSetup = (plugin) => api.get(`/plugins/${plugin}/setup`).then((r) => r.data)
export const savePluginSetup = (plugin, data) => api.put(`/plugins/${plugin}/setup`, data).then((r) => r.data)
export const runColdStart = (plugin, message, step) =>
  api.post(`/plugins/${plugin}/cold-start`, {
    input_text: message || '',
    context: { setup_step: step },
  }).then((r) => r.data)
// Browsers cannot read PDF/DOCX text, so extraction happens server-side.
export const extractSkillInput = (file) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/plugins/documents/extract', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data)
}
export const executeSkill = (plugin, skill, data = {}) => {
  const {
    text,
    input_text,
    matter_id,
    use_premium,
    use_premium_llm,
    ...context
  } = data
  return api.post(`/plugins/${plugin}/${skill}`, {
    skill,
    input_text: input_text ?? text ?? '',
    matter_id: matter_id || undefined,
    context,
    use_premium: Boolean(use_premium ?? use_premium_llm),
  }).then((r) => r.data)
}

// Matters
export const getMatters = () => api.get('/plugins/litigation/matters').then((r) => r.data)
export const createMatter = (data) => api.post('/plugins/litigation/matters', data).then((r) => r.data)
export const getMatter = (id) => api.get(`/plugins/litigation/matters/${id}`).then((r) => r.data)
export const updateMatter = (id, data) => api.patch(`/plugins/litigation/matters/${id}`, data).then((r) => r.data)
export const addMatterEvent = (id, data) => api.post(`/plugins/litigation/matters/${id}/events`, data).then((r) => r.data)
export const runMatterConflictCheck = (matterId) => api.post(`/plugins/litigation/matters/${matterId}/conflict-check`).then(r => r.data)

// Renewals
export const getRenewals = () => api.get('/plugins/commercial/renewals').then((r) => r.data)
export const createRenewal = (data) => api.post('/plugins/commercial/renewals', data).then((r) => r.data)
export const updateRenewal = (id, data) => api.patch(`/plugins/commercial/renewals/${id}`, data).then((r) => r.data)
export const deleteRenewal = (id) => api.delete(`/plugins/commercial/renewals/${id}`).then((r) => r.data)

// ── Trust & Estate ─────────────────────────────────────────────────────────

export const getEstates = () =>
  api.get('/plugins/trust-estate/estates').then(r => r.data)

export const createEstate = (data) =>
  api.post('/plugins/trust-estate/estates', data).then(r => r.data)

export const getEstate = (id) =>
  api.get(`/plugins/trust-estate/estates/${id}`).then(r => r.data)

export const updateEstate = (id, data) =>
  api.patch(`/plugins/trust-estate/estates/${id}`, data).then(r => r.data)

export const addEstateEvent = (id, data) =>
  api.post(`/plugins/trust-estate/estates/${id}/events`, data).then(r => r.data)

export const deleteEstate = (id) =>
  api.delete(`/plugins/trust-estate/estates/${id}`).then(r => r.data)

export const getEstateStats = () =>
  api.get('/plugins/trust-estate/estates/stats').then(r => r.data)

// Generic estate sub-resource helpers — resource is one of:
// fiduciaries | beneficiaries | assets | liabilities | distributions | deadlines | accounting
export const listEstateChildren = (id, resource) =>
  api.get(`/plugins/trust-estate/estates/${id}/${resource}`).then(r => r.data)

export const createEstateChild = (id, resource, data) =>
  api.post(`/plugins/trust-estate/estates/${id}/${resource}`, data).then(r => r.data)

export const updateEstateChild = (id, resource, childId, data) =>
  api.patch(`/plugins/trust-estate/estates/${id}/${resource}/${childId}`, data).then(r => r.data)

export const deleteEstateChild = (id, resource, childId) =>
  api.delete(`/plugins/trust-estate/estates/${id}/${resource}/${childId}`).then(r => r.data)

export const getEstateAccountingSummary = (id) =>
  api.get(`/plugins/trust-estate/estates/${id}/accounting/summary`).then(r => r.data)

export const getEstateReport = (id, kind) =>
  api.get(`/plugins/trust-estate/estates/${id}/reports/${kind}`).then(r => r.data)

// ── Domestic relations (family law) ─────────────────────────────────────────

const DOMESTIC = '/plugins/domestic'

export const getDomesticCases = () =>
  api.get(`${DOMESTIC}/cases`).then(r => r.data)

export const getDomesticStats = () =>
  api.get(`${DOMESTIC}/cases/stats`).then(r => r.data)

export const createDomesticCase = (data) =>
  api.post(`${DOMESTIC}/cases`, data).then(r => r.data)

export const getDomesticCase = (id) =>
  api.get(`${DOMESTIC}/cases/${id}`).then(r => r.data)

export const updateDomesticCase = (id, data) =>
  api.patch(`${DOMESTIC}/cases/${id}`, data).then(r => r.data)

export const deleteDomesticCase = (id) =>
  api.delete(`${DOMESTIC}/cases/${id}`).then(r => r.data)

// Generic sub-resource helpers: resource in
// parties|children|custody|orders|deadlines|events|calculations
export const listDomesticChildren = (id, resource) =>
  api.get(`${DOMESTIC}/cases/${id}/${resource}`).then(r => r.data)

export const createDomesticChild = (id, resource, data) =>
  api.post(`${DOMESTIC}/cases/${id}/${resource}`, data).then(r => r.data)

export const updateDomesticChild = (id, resource, childId, data) =>
  api.patch(`${DOMESTIC}/cases/${id}/${resource}/${childId}`, data).then(r => r.data)

export const deleteDomesticChild = (id, resource, childId) =>
  api.delete(`${DOMESTIC}/cases/${id}/${resource}/${childId}`).then(r => r.data)

// Payments (nested under an order)
export const listOrderPayments = (id, orderId) =>
  api.get(`${DOMESTIC}/cases/${id}/orders/${orderId}/payments`).then(r => r.data)

export const createOrderPayment = (id, orderId, data) =>
  api.post(`${DOMESTIC}/cases/${id}/orders/${orderId}/payments`, data).then(r => r.data)

export const deleteOrderPayment = (id, orderId, paymentId) =>
  api.delete(`${DOMESTIC}/cases/${id}/orders/${orderId}/payments/${paymentId}`).then(r => r.data)

// Child support calculator
export const getCsJurisdictions = () =>
  api.get(`${DOMESTIC}/jurisdictions`).then(r => r.data)

export const calculateChildSupport = (data) =>
  api.post(`${DOMESTIC}/calculate`, data).then(r => r.data)

export const saveChildSupportCalc = (id, data) =>
  api.post(`${DOMESTIC}/cases/${id}/calculations`, data).then(r => r.data)

export const getChildSupportCalc = (id, calcId) =>
  api.get(`${DOMESTIC}/cases/${id}/calculations/${calcId}`).then(r => r.data)

export const deleteChildSupportCalc = (id, calcId) =>
  api.delete(`${DOMESTIC}/cases/${id}/calculations/${calcId}`).then(r => r.data)

export const downloadWorksheetPdf = async (id, calcId) => {
  const res = await api.get(`${DOMESTIC}/cases/${id}/calculations/${calcId}/worksheet.pdf`, { responseType: 'blob' })
  const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `child_support_worksheet_${calcId.slice(0, 8)}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(url)
}

// ── Mediation ──────────────────────────────────────────────────────────────

export const getMediationCases = () =>
  api.get('/plugins/mediation/cases').then(r => r.data)

export const createMediationCase = (data) =>
  api.post('/plugins/mediation/cases', data).then(r => r.data)

export const getMediationCase = (id) =>
  api.get(`/plugins/mediation/cases/${id}`).then(r => r.data)

export const updateMediationCase = (id, data) =>
  api.patch(`/plugins/mediation/cases/${id}`, data).then(r => r.data)

export const advanceMediationCase = (id, data) =>
  api.post(`/plugins/mediation/cases/${id}/next-action`, data).then(r => r.data)

export const addMediationEvent = (id, data) =>
  api.post(`/plugins/mediation/cases/${id}/events`, data).then(r => r.data)

export const deleteMediationCase = (id) =>
  api.delete(`/plugins/mediation/cases/${id}`).then(r => r.data)

export const getMediationStats = () =>
  api.get('/plugins/mediation/cases/stats').then(r => r.data)

// Parties + invites
export const listMediationParties = (id) =>
  api.get(`/plugins/mediation/cases/${id}/parties`).then(r => r.data)

export const createMediationParty = (id, data) =>
  api.post(`/plugins/mediation/cases/${id}/parties`, data).then(r => r.data)

export const updateMediationParty = (id, partyId, data) =>
  api.patch(`/plugins/mediation/cases/${id}/parties/${partyId}`, data).then(r => r.data)

export const deleteMediationParty = (id, partyId) =>
  api.delete(`/plugins/mediation/cases/${id}/parties/${partyId}`).then(r => r.data)

export const inviteMediationParty = (id, partyId) =>
  api.post(`/plugins/mediation/cases/${id}/parties/${partyId}/invite`).then(r => r.data)

// Asset schedule (firm review + approval)
export const listMediationAssets = (id) =>
  api.get(`/plugins/mediation/cases/${id}/assets`).then(r => r.data)

export const createMediationAsset = (id, data) =>
  api.post(`/plugins/mediation/cases/${id}/assets`, data).then(r => r.data)

export const updateMediationAsset = (id, assetId, data) =>
  api.patch(`/plugins/mediation/cases/${id}/assets/${assetId}`, data).then(r => r.data)

export const deleteMediationAsset = (id, assetId) =>
  api.delete(`/plugins/mediation/cases/${id}/assets/${assetId}`).then(r => r.data)

export const approveMediationAsset = (id, assetId) =>
  api.post(`/plugins/mediation/cases/${id}/assets/${assetId}/approve`).then(r => r.data)

export const sendMediationAsset = (id, assetId) =>
  api.post(`/plugins/mediation/cases/${id}/assets/${assetId}/send`).then(r => r.data)

// Documents (vault)
export const listMediationDocuments = (id) =>
  api.get(`/plugins/mediation/cases/${id}/documents`).then(r => r.data)

export const uploadMediationDocument = (id, file, description) => {
  const form = new FormData()
  form.append('file', file)
  if (description) form.append('description', description)
  return api.post(`/plugins/mediation/cases/${id}/documents/upload`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export const downloadMediationDocumentUrl = (id, docId) =>
  `${BASE_URL}/plugins/mediation/cases/${id}/documents/${docId}/download`

// Proposals
export const listMediationProposals = (id) =>
  api.get(`/plugins/mediation/cases/${id}/proposals`).then(r => r.data)

export const createMediationProposal = (id, data) =>
  api.post(`/plugins/mediation/cases/${id}/proposals`, data).then(r => r.data)

// ── Mediation Portal (client / opposing party) ──────────────────────────────
const portalApi = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})

export const acceptPortalInvite = (token) =>
  portalApi.post('/portal/mediation/accept', { token }).then(r => r.data)

export const getPortalCase = (caseId) =>
  portalApi.get('/portal/mediation/case', { params: caseId ? { case_id: caseId } : {} }).then(r => r.data)

export const createPortalAsset = (data, caseId) =>
  portalApi.post('/portal/mediation/assets', data, { params: caseId ? { case_id: caseId } : {} }).then(r => r.data)

export const updatePortalAsset = (assetId, data, caseId) =>
  portalApi.patch(`/portal/mediation/assets/${assetId}`, data, { params: caseId ? { case_id: caseId } : {} }).then(r => r.data)

export const submitPortalAsset = (assetId, caseId) =>
  portalApi.post(`/portal/mediation/assets/${assetId}/submit`, {}, { params: caseId ? { case_id: caseId } : {} }).then(r => r.data)

export const decidePortalAsset = (assetId, data, caseId) =>
  portalApi.post(`/portal/mediation/assets/${assetId}/decision`, data, { params: caseId ? { case_id: caseId } : {} }).then(r => r.data)

export const uploadPortalDocument = (file, description, caseId) => {
  const form = new FormData()
  form.append('file', file)
  if (description) form.append('description', description)
  return portalApi.post('/portal/mediation/documents/upload', form, {
    params: caseId ? { case_id: caseId } : {},
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export const listPortalDocuments = (caseId) =>
  portalApi.get('/portal/mediation/documents', { params: caseId ? { case_id: caseId } : {} }).then(r => r.data)

export const listPortalProposals = (caseId) =>
  portalApi.get('/portal/mediation/proposals', { params: caseId ? { case_id: caseId } : {} }).then(r => r.data)

export const createPortalProposal = (data, caseId) =>
  portalApi.post('/portal/mediation/proposals', data, { params: caseId ? { case_id: caseId } : {} }).then(r => r.data)

export const downloadPortalDocumentUrl = (docId, caseId) =>
  `${BASE_URL}/portal/mediation/documents/${docId}/download${caseId ? `?case_id=${caseId}` : ''}`

// ── Client Portal (firm client, matter-scoped) ──────────────────────────────
// Separate axios instance so a portal 401 does not bounce through the firm-app
// login redirect in the shared interceptor.
const clientPortalApi = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})

export const acceptClientPortalInvite = (token) =>
  clientPortalApi.post('/portal/client/accept', { token }).then((r) => r.data)

export const getClientPortalMatter = () =>
  clientPortalApi.get('/portal/client/matter').then((r) => r.data)

export const listClientPortalMessages = () =>
  clientPortalApi.get('/portal/client/messages').then((r) => r.data)

export const sendClientPortalMessage = (data) =>
  clientPortalApi.post('/portal/client/messages', data).then((r) => r.data)

export const listClientPortalDocuments = () =>
  clientPortalApi.get('/portal/client/documents').then((r) => r.data)

export const uploadClientPortalDocument = (file, description) => {
  const form = new FormData()
  form.append('file', file)
  if (description) form.append('description', description)
  return clientPortalApi
    .post('/portal/client/documents/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data)
}

export const downloadClientPortalDocumentUrl = (docId) =>
  `${BASE_URL}/portal/client/documents/${docId}/download`

export const listClientPortalInvoices = () =>
  clientPortalApi.get('/portal/client/invoices').then((r) => r.data)

// Firm-side client portal invite management (firm login)
export const createMatterPortalInvite = (matterId, data) =>
  api.post(`/matters/${matterId}/portal/invite`, data).then((r) => r.data)

export const listMatterPortalInvites = (matterId) =>
  api.get(`/matters/${matterId}/portal/invites`).then((r) => r.data)

export const revokeMatterPortalInvite = (matterId, inviteId) =>
  api.delete(`/matters/${matterId}/portal/invites/${inviteId}`).then((r) => r.data)

// ── E-signature (firm side) ─────────────────────────────────────────────────
export const createSignatureRequest = (matterId, data) =>
  api.post(`/matters/${matterId}/signatures`, data).then((r) => r.data)

export const listSignatureRequests = (matterId) =>
  api.get(`/matters/${matterId}/signatures`).then((r) => r.data)

export const getSignatureRequest = (matterId, requestId) =>
  api.get(`/matters/${matterId}/signatures/${requestId}`).then((r) => r.data)

export const sendSignatureRequest = (matterId, requestId) =>
  api.post(`/matters/${matterId}/signatures/${requestId}/send`).then((r) => r.data)

export const voidSignatureRequest = (matterId, requestId, data) =>
  api.post(`/matters/${matterId}/signatures/${requestId}/void`, data).then((r) => r.data)

// E-signature (client portal side)
export const listClientPortalSignatures = () =>
  clientPortalApi.get('/portal/client/signatures').then((r) => r.data)

export const signClientPortalSignature = (requestId, data) =>
  clientPortalApi.post(`/portal/client/signatures/${requestId}/sign`, data).then((r) => r.data)

export const declineClientPortalSignature = (requestId, data) =>
  clientPortalApi.post(`/portal/client/signatures/${requestId}/decline`, data).then((r) => r.data)

// Billing
export const getBillingStatus = () => api.get('/billing/status').then((r) => r.data)
export const createCheckoutSession = () => api.post('/billing/checkout-session').then((r) => r.data)
export const createPortalSession = () => api.post('/billing/portal').then((r) => r.data)

// MCP
export const getMcpProductKeys = () => api.get('/mcp/product-keys').then((r) => r.data)
export const createMcpProductKey = (data) => api.post('/mcp/product-keys', data).then((r) => r.data)
export const revokeMcpProductKey = (keyId) => api.delete(`/mcp/product-keys/${keyId}`).then((r) => r.data)
export const getMcpUsage = (days = 30) => api.get('/mcp/usage', { params: { days } }).then((r) => r.data)
export const getLegalSourceHealth = () => api.get('/mcp/source-health').then((r) => r.data)

// Platform (uses platform key header — passed explicitly)
export const createPlatformSession = (bootstrapKey) =>
  axios.post(
    `${BASE_URL}/platform/auth/token`,
    {},
    { headers: { 'X-Platform-Key': bootstrapKey } },
  ).then((r) => r.data)

const platformApi = (platformToken) =>
  axios.create({
    baseURL: BASE_URL,
    headers: { Authorization: `Bearer ${platformToken}` },
  })

export const getPlatformTenants = (key, page = 1) =>
  platformApi(key).get(`/platform/tenants?page=${page}`).then((r) => r.data)

export const getPlatformTenant = (key, id) =>
  platformApi(key).get(`/platform/tenants/${id}`).then((r) => r.data)

export const updatePlatformTenant = (key, id, data) =>
  platformApi(key).put(`/platform/tenants/${id}`, data).then((r) => r.data)

export const getPlatformPlans = (key) =>
  platformApi(key).get('/platform/plans').then((r) => r.data)

export const getPlatformUsage = (key) =>
  platformApi(key).get('/platform/usage').then((r) => r.data)

export const getPlatformMcpOverview = (key) =>
  platformApi(key).get('/platform/mcp').then((r) => r.data)

export const getPlatformHealth = (key) =>
  platformApi(key).get('/platform/health').then((r) => r.data)

export const getPlatformIntegrationReadiness = (key) =>
  platformApi(key).get('/platform/integrations/readiness').then((r) => r.data)

export const getPlatformLLMProviders = (key) =>
  platformApi(key).get('/platform/llm-providers').then((r) => r.data)

export const getPlatformLLMConfig = (key) =>
  platformApi(key).get('/platform/llm-config').then((r) => r.data)

export const updatePlatformLLMConfig = (key, data) =>
  platformApi(key).put('/platform/llm-config', data).then((r) => r.data)

// ── Provider Route Builder (Task 1206) ────────────────────────────────────

export const getLLMProviderPresets = (key) =>
  platformApi(key).get('/platform/llm/providers').then((r) => r.data)

export const getLLMProviderKeys = (key) =>
  platformApi(key).get('/platform/llm/provider-keys').then((r) => r.data)

export const addLLMProviderKey = (key, data) =>
  platformApi(key).post('/platform/llm/provider-keys', data).then((r) => r.data)

export const deleteLLMProviderKey = (key, keyId) =>
  platformApi(key).delete(`/platform/llm/provider-keys/${keyId}`).then((r) => r.data)

export const syncEnvKeys = (key) =>
  platformApi(key).post('/platform/llm/provider-keys/sync-env').then((r) => r.data)

export const fetchProviderModels = (key, keyId) =>
  platformApi(key).post(`/platform/llm/provider-keys/${keyId}/fetch-models`).then((r) => r.data)

export const getLLMModelCatalog = (key) =>
  platformApi(key).get('/platform/llm/model-catalog').then((r) => r.data)

export const refreshLLMModelCatalog = (key) =>
  platformApi(key).post('/platform/llm/model-catalog/refresh').then((r) => r.data)

export const getLLMRoutes = (key) =>
  platformApi(key).get('/platform/llm/routes').then((r) => r.data)

export const recommendLLMRoutes = (key, data) =>
  platformApi(key).post('/platform/llm/routes/recommend', data).then((r) => r.data)

export const saveLLMRoutes = (key, data) =>
  platformApi(key).put('/platform/llm/routes', data).then((r) => r.data)

export const getLLMGatewayStatus = (key) =>
  platformApi(key).get('/platform/llm/gateway/status').then((r) => r.data)

export const reloadLLMRoutes = (key) =>
  platformApi(key).post('/platform/llm/routes/reload').then((r) => r.data)

export const testLLMRoute = (key, data) =>
  platformApi(key).post('/platform/llm/routes/test', data).then((r) => r.data)

export const getPlatformLogs = (key, params = {}) =>
  platformApi(key).get('/platform/logs', { params }).then((r) => r.data)

export const getPlatformLogsSummary = (key, params = {}) =>
  platformApi(key).get('/platform/logs/summary', { params }).then((r) => r.data)

export const getPlatformTenantLogs = (key, tenantId, params = {}) =>
  platformApi(key).get(`/platform/logs/tenant/${tenantId}`, { params }).then((r) => r.data)

export const getPlatformTenantLogsSummary = (key, tenantId, params = {}) =>
  platformApi(key).get(`/platform/logs/tenant/${tenantId}/summary`, { params }).then((r) => r.data)

export const getPlatformAccessLogs = (key, params = {}) =>
  platformApi(key).get('/platform/access-logs', { params }).then((r) => r.data)

export const getPlatformAccessLogsSummary = (key, params = {}) =>
  platformApi(key).get('/platform/access-logs/summary', { params }).then((r) => r.data)

// ── Contacts ───────────────────────────────────────────────────────────────

export const getContacts = (params = {}) =>
  api.get('/contacts', { params }).then(r => r.data)

export const createContact = (data) =>
  api.post('/contacts', data).then(r => r.data)

export const getContact = (id) =>
  api.get(`/contacts/${id}`).then(r => r.data)

export const updateContact = (id, data) =>
  api.patch(`/contacts/${id}`, data).then(r => r.data)

export const deleteContact = (id) =>
  api.delete(`/contacts/${id}`)

export const getContactMatters = (id) =>
  api.get(`/contacts/${id}/matters`).then(r => r.data)

export const getContactCommunications = (id, params = {}) =>
  api.get(`/contacts/${id}/communications`, { params }).then(r => r.data)

export const conflictCheck = (data) =>
  api.post('/contacts/conflict-check', data).then(r => r.data)

// ── Tasks ──────────────────────────────────────────────────────────────────

export const getTasks = (params = {}) =>
  api.get('/tasks', { params }).then(r => r.data)

export const getTaskBoard = (params = {}) =>
  api.get('/tasks/board', { params }).then(r => r.data)

export const getTaskBoardConfig = () =>
  api.get('/tasks/board/config').then(r => r.data)

export const recordTaskBoardTelemetry = (data) =>
  api.post('/tasks/board/telemetry', data).then(r => r.data)

export const createTask = (data) =>
  api.post('/tasks', data).then(r => r.data)

export const getTask = (id) =>
  api.get(`/tasks/${id}`).then(r => r.data)

export const updateTask = (id, data) =>
  api.patch(`/tasks/${id}`, data).then(r => r.data)

export const transitionTask = (id, data) =>
  api.post(`/tasks/${id}/transition`, data).then(r => r.data)

// Revise the subject/body of an assistant-drafted action before approving it.
// Recipients are intentionally not editable — they are resolved server-side
// from the matter's own parties.
export const updateTaskPendingAction = (id, data) =>
  api.patch(`/tasks/${id}/pending-action`, data).then(r => r.data)

const normalizeTaskActionApprovalError = (error) => {
  const normalized = normalizeApiError(error)
  const responseData = normalized?.response?.data
  const rawDetail = responseData && typeof responseData === 'object'
    ? (responseData.detail ?? responseData.message ?? responseData.error)
    : null
  const structuredDetail = rawDetail && typeof rawDetail === 'object' && !Array.isArray(rawDetail)
    ? rawDetail
    : null
  const structuredMessage = structuredDetail
    ? [structuredDetail.message, structuredDetail.detail, structuredDetail.error]
        .find((value) => typeof value === 'string' && value.trim())
    : null
  const safeMessage = structuredMessage || normalized.message || 'The task could not be approved.'

  normalized.message = String(safeMessage)
  normalized.detail = normalized.message
  if (responseData && typeof responseData === 'object' && !Array.isArray(responseData)) {
    normalized.response = {
      ...normalized.response,
      data: {
        ...responseData,
        // ActionProposalCard renders this value directly. FastAPI conflict
        // responses may put message/current_task inside `detail`, so flatten
        // only the display field while retaining the current task separately.
        detail: normalized.message,
        ...(structuredDetail?.current_task
          ? { current_task: structuredDetail.current_task }
          : {}),
      },
    }
  }
  if (structuredDetail) normalized.conflict_detail = structuredDetail
  if (structuredDetail?.current_task) normalized.current_task = structuredDetail.current_task
  return normalized
}

/**
 * Approve assistant-proposed work: optionally save an edited draft, then move
 * the task out of Review, which is what triggers the deterministic automation.
 *
 * The caller must supply the exact task version shown with the reviewed draft.
 * Never re-read the current version here: doing so would silently bless edits
 * made after the attorney reviewed the proposal. An optional edit is guarded by
 * that reviewed version, then its returned version guards the transition.
 */
export const approveProposedTask = async (
  id,
  {
    body,
    subject,
    expectedVersion,
    expected_version: expectedVersionSnake,
    acknowledgePriorDeliveryRisk,
    acknowledge_prior_delivery_risk: acknowledgePriorDeliveryRiskSnake,
  } = {},
) => {
  const reviewedVersion = expectedVersion ?? expectedVersionSnake
  if (!Number.isInteger(reviewedVersion) || reviewedVersion < 1) {
    throw new Error('The reviewed task version is required before approval. Refresh the proposal and review it again.')
  }

  let version = reviewedVersion
  if (body !== undefined || subject !== undefined) {
    let edited
    try {
      edited = await updateTaskPendingAction(id, {
        body,
        subject,
        expected_version: reviewedVersion,
      })
    } catch (error) {
      throw normalizeTaskActionApprovalError(error)
    }
    version = edited.version
    if (!Number.isInteger(version) || version < 1) {
      throw new Error('The edited task did not return a valid version, so it was not approved.')
    }
  }
  try {
    const acknowledgeRisk = acknowledgePriorDeliveryRisk
      ?? acknowledgePriorDeliveryRiskSnake
      ?? false
    return await transitionTask(id, {
      to_status: 'in_progress',
      expected_version: version,
      ...(acknowledgeRisk ? { acknowledge_prior_delivery_risk: true } : {}),
    })
  } catch (error) {
    throw normalizeTaskActionApprovalError(error)
  }
}

const TERMINAL_DELIVERY = new Set(['sent', 'failed'])

/**
 * Poll a task until its automation reaches a terminal delivery state.
 *
 * Delivery runs out-of-band after the approval commits, so the approval
 * response cannot tell us whether the client was actually contacted. Resolves
 * with the last delivery seen; a null result means we stopped waiting rather
 * than that nothing happened, and the caller must say so rather than claiming
 * success.
 */
export const waitForTaskDelivery = async (
  id,
  { attempts = 10, intervalMs = 1200, signal } = {},
) => {
  let last = null
  for (let i = 0; i < attempts; i += 1) {
    if (signal?.aborted) return last
    try {
      last = (await getTask(id))?.delivery || null
    } catch {
      // A transient read failure should not be reported as a delivery failure.
      last = last || null
    }
    if (last && TERMINAL_DELIVERY.has(last.status)) return last
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
  return last
}

export const getTaskEvents = (id, params = {}) =>
  api.get(`/tasks/${id}/events`, { params }).then(r => r.data)

export const deleteTask = (id) =>
  api.delete(`/tasks/${id}`)

export const getOverdueTasks = (params = {}) =>
  api.get('/tasks/overdue', { params }).then(r => r.data)

export const getUpcomingTasks = (params = {}) =>
  api.get('/tasks/upcoming', { params }).then(r => r.data)

export const sendTaskReminder = (taskId) =>
  api.post(`/tasks/${taskId}/remind`).then(r => r.data)

export const qualifyIntakeTask = (taskId, data) =>
  api.post(`/tasks/${taskId}/qualify-intake`, data).then(r => r.data)

export const markTaskViewed = (taskId) =>
  api.post(`/tasks/${taskId}/view`).then(r => r.data)

export const markTaskContacted = (taskId, data) =>
  api.post(`/tasks/${taskId}/contacted`, data).then(r => r.data)

// ── Communications ─────────────────────────────────────────────────────────

export const getCommunications = (params = {}) =>
  api.get('/communications', { params }).then(r => r.data)

export const createCommunication = (data) =>
  api.post('/communications', data).then(r => r.data)

export const getCommunication = (id) =>
  api.get(`/communications/${id}`).then(r => r.data)

export const updateCommunication = (id, data) =>
  api.patch(`/communications/${id}`, data).then(r => r.data)

export const deleteCommunication = (id) =>
  api.delete(`/communications/${id}`).then(r => r.data)

export const scanEmailInbox = (provider, maxEmails = 20) =>
  api.post('/email/scan', { provider, max_emails: maxEmails }).then(r => r.data)

// ── Matter Correspondence (archived emails) ─────────────────────────────────

export const getMatterCorrespondence = (matterId, params = {}) =>
  api.get(`/matters/${matterId}/correspondence`, { params }).then(r => r.data)

export const scanMatterCorrespondence = (matterId, provider, maxEmails = null) =>
  api
    .post(`/matters/${matterId}/correspondence/scan`, {
      provider,
      max_emails: maxEmails,
    })
    .then(r => r.data)

export const getCorrespondenceRules = (matterId) =>
  api.get(`/matters/${matterId}/correspondence/rules`).then(r => r.data)

export const updateCorrespondenceRules = (matterId, rules) =>
  api.put(`/matters/${matterId}/correspondence/rules`, rules).then(r => r.data)

export const matterCorrespondenceDownloadUrl = (matterId, commId) =>
  `${API_BASE_URL}/matters/${matterId}/correspondence/${commId}/download`

// ── Intake / Leads ─────────────────────────────────────────────────────────

export const getLeads = (params = {}) =>
  api.get('/intake', { params }).then(r => r.data)

export const createLead = (data) =>
  api.post('/intake', data).then(r => r.data)

export const getLead = (id) =>
  api.get(`/intake/${id}`).then(r => r.data)

export const updateLead = (id, data) =>
  api.patch(`/intake/${id}`, data).then(r => r.data)

export const convertLead = (id, data) =>
  api.post(`/intake/${id}/convert`, data).then(r => r.data)

// ── Intake Dashboard ───────────────────────────────────────────────────────

export const searchIntakeDashboard = (params = {}) =>
  api.get('/intake/dashboard/search', { params }).then(r => r.data)

export const getRecentIntakeDashboardCallers = (params = {}) =>
  api.get('/intake/dashboard/recent-callers', { params }).then(r => r.data)

export const getZoomPhoneIntakeCalls = (params = {}) =>
  api.get('/intake/dashboard/zoom-phone/calls', { params }).then(r => r.data)

export const syncZoomPhoneIntakeCalls = (params = {}) =>
  api.post('/intake/dashboard/zoom-phone/sync', null, { params }).then(r => r.data)

export const downloadIntakeDashboardCallsCsv = (params = {}) =>
  api.get('/intake/dashboard/calls/export', { params, responseType: 'blob' }).then(r => r.data)

export const getIntakeAssignmentAvailability = (params = {}) =>
  api.get('/intake/dashboard/assignment-availability', { params }).then(r => r.data)

export const createIntakeDashboardCall = (data) =>
  api.post('/intake/dashboard/calls', data).then(r => r.data)

export const getIntakeDrafts = () =>
  api.get('/intake/drafts').then((r) => r.data)

export const upsertIntakeDraft = (draftId, data) =>
  api.put(`/intake/drafts/${draftId}`, data).then((r) => r.data)

export const deleteIntakeDraft = (draftId) =>
  api.delete(`/intake/drafts/${draftId}`).then((r) => r.data)

export const assignNextPartner = (leadId) =>
  api.post(`/intake/dashboard/leads/${leadId}/assign-next`).then(r => r.data)

export const getRotationRules = () =>
  api.get('/intake/dashboard/rotation-rules').then(r => r.data)

export const updateRotationRules = (rules) =>
  api.put('/intake/dashboard/rotation-rules', { rules }).then(r => r.data)

export const getPartnerLog = (params = {}) =>
  api.get('/intake/dashboard/partner-log', { params }).then(r => r.data)

export const downloadPartnerLogCsv = (params = {}) =>
  api.get('/intake/dashboard/partner-log/export', { params, responseType: 'blob' }).then(r => r.data)

// ── Plan / Upsell ───────────────────────────────────────────────────────────

export const requestPlanUpgrade = (data = {}) =>
  api.post('/plan/upgrade-request', data).then(r => r.data)

// ── Matter Parties ──────────────────────────────────────────────────────────

export const getMatterParties = (matterId) =>
  api.get(`/matters/${matterId}/parties`).then(r => r.data)

export const addMatterParty = (matterId, data) =>
  api.post(`/matters/${matterId}/parties`, data).then(r => r.data)

export const updateMatterParty = (matterId, partyId, data) =>
  api.patch(`/matters/${matterId}/parties/${partyId}`, data).then(r => r.data)

export const removeMatterParty = (matterId, partyId) =>
  api.delete(`/matters/${matterId}/parties/${partyId}`).then(r => r.data)

// ── Matter Documents ────────────────────────────────────────────────────────

export const getMatterDocuments = (matterId) =>
  api.get(`/matters/${matterId}/documents`).then(r => r.data)

export const uploadMatterDocument = (matterId, formData) =>
  api.post(`/matters/${matterId}/documents/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)

export const updateMatterDocument = (matterId, docId, data) =>
  api.patch(`/matters/${matterId}/documents/${docId}`, data).then(r => r.data)

export const deleteMatterDocument = (matterId, docId) =>
  api.delete(`/matters/${matterId}/documents/${docId}`).then(r => r.data)

export const getMatterDocumentDownloadUrl = (matterId, docId) =>
  `${API_BASE_URL}/matters/${matterId}/documents/${docId}/download`

// Matter document revisions
export const createMatterDocumentRevision = (matterId, sourceDocumentId, data) =>
  api.post(`/matters/${matterId}/documents/${sourceDocumentId}/revisions`, data).then(r => r.data)

export const listMatterDocumentRevisions = (matterId, sourceDocumentId) =>
  api.get(`/matters/${matterId}/documents/${sourceDocumentId}/revisions`).then(r => r.data)

export const getMatterDocumentRevision = (matterId, revisionId) =>
  api.get(`/matters/${matterId}/document-revisions/${revisionId}`).then(r => r.data)

export const getMatterDocumentRevisionArtifactUrl = (matterId, revisionId) =>
  `${API_BASE_URL}/matters/${matterId}/document-revisions/${revisionId}/artifact`

export const approveMatterDocumentRevision = (matterId, revisionId, data) =>
  api.post(`/matters/${matterId}/document-revisions/${revisionId}/approve`, data).then(r => r.data)

export const rejectMatterDocumentRevision = (matterId, revisionId, data = {}) =>
  api.post(`/matters/${matterId}/document-revisions/${revisionId}/reject`, data).then(r => r.data)

export const prepareMatterDocumentRevisionESignReplacement = (matterId, revisionId, data) =>
  api.post(`/matters/${matterId}/document-revisions/${revisionId}/prepare-esign-replacement`, data).then(r => r.data)

export const getMatterCloudFolder = (matterId) =>
  api.get(`/matters/${matterId}/cloud-folder`).then(r => r.data)

export const provisionMatterCloudFolder = (matterId) =>
  api.post(`/matters/${matterId}/cloud-folder/provision`).then(r => r.data)

export const remapMatterCloudFolder = (matterId, provider, data) =>
  api.patch(`/matters/${matterId}/cloud-folder/${provider}/remap`, data).then(r => r.data)

export const renameMatterCloudFolder = (matterId, provider, data) =>
  api.patch(`/matters/${matterId}/cloud-folder/${provider}/rename`, data).then(r => r.data)

export const addMatterCloudContextFolder = (matterId, data) =>
  api.post(`/matters/${matterId}/cloud-folder/context`, data).then(r => r.data)

export const removeMatterCloudContextFolder = (matterId, contextFolderId) =>
  api.delete(`/matters/${matterId}/cloud-folder/context/${contextFolderId}`).then(r => r.data)

export const syncMatterCloudFolder = (matterId) =>
  api.post(`/matters/${matterId}/cloud-folder/sync`).then(r => r.data)

// ── Reports ─────────────────────────────────────────────────────────────────

export const getMatterStatusReport = () =>
  api.get('/reports/matters').then(r => r.data)

export const getIntakeFunnelReport = () =>
  api.get('/reports/intake').then(r => r.data)

export const getOverdueTasksReport = () =>
  api.get('/reports/overdue-tasks').then(r => r.data)

export const getReportsBundle = () =>
  api.get('/reports/bundle').then(r => r.data)

export const getRealizationReport = () =>
  api.get('/reports/billing/realization').then(r => r.data)

export const getWipReport = () =>
  api.get('/reports/billing/wip').then(r => r.data)

export const getAgingReport = () =>
  api.get('/reports/billing/aging').then(r => r.data)

export const downloadRealizationCsv = () =>
  api.get('/reports/billing/realization', { params: { format: 'csv' }, responseType: 'blob' }).then(r => r.data)

export const downloadWipCsv = () =>
  api.get('/reports/billing/wip', { params: { format: 'csv' }, responseType: 'blob' }).then(r => r.data)

export const downloadAgingCsv = () =>
  api.get('/reports/billing/aging', { params: { format: 'csv' }, responseType: 'blob' }).then(r => r.data)

export const triggerBlobDownload = (blob, filename) => {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(url)
}

// ── Calendar ────────────────────────────────────────────────────────────────

export const getCalendarEvents = (start, end) => {
  const params = {}
  if (start) params.start = start
  if (end) params.end = end
  return api.get('/calendar/events', { params }).then(r => r.data)
}

export const syncCalendarDeadlines = (provider = 'microsoft') =>
  api
    .post('/calendar/sync', { provider, sync_deadlines: true })
    .then(r => r.data)

export const getCalendarProviders = () =>
  api.get('/auth/calendar-providers').then(r => r.data)

export const connectCalendarIntegration = (provider) => {
  window.location.href = `${BASE_URL}/integrations/${provider}/connect?intent=user`
}

export const listScheduledEvents = (params = {}) =>
  api.get('/calendar/scheduled-events', { params }).then(r => r.data)

export const createScheduledEvent = (data) =>
  api.post('/calendar/scheduled-events', data).then(r => r.data)

export const updateScheduledEvent = (id, data) =>
  api.patch(`/calendar/scheduled-events/${id}`, data).then(r => r.data)

export const deleteScheduledEvent = (id) =>
  api.delete(`/calendar/scheduled-events/${id}`)

// ── Document Templates ──────────────────────────────────────────────────────

export const getTemplates = (params = {}) =>
  api.get('/templates', { params }).then(r => r.data)

export const createTemplate = (data) =>
  api.post('/templates', data).then(r => r.data)

export const analyzeTemplateUpload = (formData) =>
  api.post('/templates/intake/analyze', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)

export const createTemplateFromUpload = (formData) =>
  api.post('/templates/intake/create', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)

export const getTemplate = (id) =>
  api.get(`/templates/${id}`).then(r => r.data)

export const updateTemplate = (id, data) =>
  api.patch(`/templates/${id}`, data).then(r => r.data)

export const deleteTemplate = (id) =>
  api.delete(`/templates/${id}`).then(r => r.data)

export const renderTemplate = (id, data) =>
  api.post(`/templates/${id}/render`, data).then(r => r.data)

export const readBlobErrorDetail = async (blob) => {
  const text = await blob.text()
  try {
    const parsed = JSON.parse(text)
    return String(parsed?.detail || parsed?.message || text)
  } catch {
    return text
  }
}

export const renderTemplateFile = (id, data) =>
  api.post(`/templates/${id}/render-file`, data, { responseType: 'blob' }).then((r) => {
    const disposition = r.headers?.['content-disposition'] || ''
    const encodedMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i)
    const basicMatch = disposition.match(/filename="?([^";]+)"?/i)
    const filename = encodedMatch
      ? decodeURIComponent(encodedMatch[1])
      : basicMatch?.[1] || `generated-template-${id}.pdf`
    return {
      blob: r.data,
      filename,
      contentType: r.headers?.['content-type'] || r.data?.type || 'application/octet-stream',
      previewId: r.headers?.['x-clarity-preview-id'] || '',
      previewPurpose: r.headers?.['x-clarity-preview-purpose'] || '',
    }
  }).catch(async (error) => {
    const blob = error?.response?.data
    if (blob instanceof Blob) {
      try {
        const detail = await readBlobErrorDetail(blob)
        if (detail) {
          error.message = String(detail)
          if (error.response) error.response.data = { detail: String(detail) }
        }
      } catch {
        // Preserve the original transport error when the blob cannot be read.
      }
    }
    throw error
  })

export const discoverTemplateVariables = (id, data = {}) =>
  api.post(`/templates/${id}/smart-fill-preview`, data).then(r => r.data)

// ── Reports / Budget ──────────────────────────────────────────────────────────

export const getMatterBudget = (matterId) =>
  api.get(`/reports/matters/${matterId}/budget`).then(r => r.data)

// ── Matters V2 ─────────────────────────────────────────────────────────────────
export const getMattersV2 = (params) =>
  api.get('/matters', { params }).then(r => r.data)
export const getMatterFieldOptions = () =>
  api.get('/matters/field-options').then(r => r.data)
export const createMatterV2 = (data) =>
  api.post('/matters', data).then(r => r.data)
export const getMatterV2 = (id) =>
  api.get(`/matters/${id}`).then(r => r.data)
export const updateMatterV2 = (id, data) =>
  api.patch(`/matters/${id}`, data).then(r => r.data)
export const closeMatterV2 = (id) =>
  api.delete(`/matters/${id}`)
export const getMyMatters = () =>
  api.get('/matters/my').then(r => r.data)
export const getMatterStats = () =>
  api.get('/matters/stats').then(r => r.data)

// Assignments
export const getMatterAssignments = (id) =>
  api.get(`/matters/${id}/assignments`).then(r => r.data)
export const addMatterAssignment = (id, data) =>
  api.post(`/matters/${id}/assignments`, data).then(r => r.data)
export const removeMatterAssignment = (id, aid) =>
  api.delete(`/matters/${id}/assignments/${aid}`)
export const setAssignmentActive = (matterId, assignmentId, active) =>
  api.patch(`/matters/${matterId}/assignments/${assignmentId}/active`, null, { params: { active } }).then(r => r.data)

// Notes & Activity
export const getMatterNotes = (id, params) =>
  api.get(`/matters/${id}/notes`, { params }).then(r => r.data)
export const addMatterNote = (id, data) =>
  api.post(`/matters/${id}/notes`, data).then(r => r.data)
export const updateMatterNote = (id, nid, data) =>
  api.patch(`/matters/${id}/notes/${nid}`, data).then(r => r.data)
export const deleteMatterNote = (id, nid) =>
  api.delete(`/matters/${id}/notes/${nid}`)

// Timeline
export const getMatterTimeline = (id, params) =>
  api.get(`/matters/${id}/timeline`, { params }).then(r => r.data)

// Budget
export const getMatterBudgetV2 = (id) =>
  api.get(`/matters/${id}/budget`).then(r => r.data)
export const updateMatterBudget = (id, data) =>
  api.patch(`/matters/${id}/budget`, null, { params: data }).then(r => r.data)

// Retainers
export const getMatterRetainers = (id) =>
  api.get(`/matters/${id}/retainers`).then(r => r.data)
export const createRetainer = (id, data) =>
  api.post(`/matters/${id}/retainers`, data).then(r => r.data)
export const drawdownRetainer = (id, rid, data) =>
  api.post(`/matters/${id}/retainers/${rid}/drawdown`, data).then(r => r.data)

// Time entries (matter-scoped)
export const getMatterTimeEntries = (id, params) =>
  api.get(`/matters/${id}/time-entries`, { params }).then(r => r.data)

// Invoices (matter-scoped)
export const getMatterInvoices = (id, params) =>
  api.get(`/matters/${id}/invoices`, { params }).then(r => r.data)

// Memory
export const getMatterMemory = (id) =>
  api.get(`/matters/${id}/memory`).then(r => r.data)
export const updateMatterMemory = (id, content) =>
  api.put(`/matters/${id}/memory`, { content }).then(r => r.data)

// Dashboard summary
export const getMatterDashboard = (id) =>
  api.get(`/matters/${id}/dashboard-summary`).then(r => r.data)

// Cloud files for a matter
export const getMatterCloudFiles = (id) =>
  api.get(`/matters/${id}/cloud-files`).then(r => r.data)

// Email client
export const emailMatterClient = (id, data) =>
  api.post(`/matters/${id}/email-client`, data).then(r => r.data)

// Portfolio upcoming tasks
export const getPortfolioUpcoming = (days = 14) =>
  api.get('/portfolio/upcoming', { params: { days } }).then(r => r.data)

// ── Billing Extended ──────────────────────────────────────────────────────────
export const getTimeEntries = (params) =>
  api.get('/billing/time-entries', { params }).then(r => r.data)
export const createTimeEntry = (data) =>
  api.post('/billing/time-entries', data).then(r => r.data)
export const getTimeEntry = (id) =>
  api.get(`/billing/time-entries/${id}`).then(r => r.data)
export const updateTimeEntry = (id, data) =>
  api.patch(`/billing/time-entries/${id}`, data).then(r => r.data)
export const deleteTimeEntry = (id) =>
  api.delete(`/billing/time-entries/${id}`)

// Live timer
export const startTimer = (data) =>
  api.post('/billing/time-entries/timer/start', data).then(r => r.data)
export const stopTimer = (data = {}) =>
  api.post('/billing/time-entries/timer/stop', data).then(r => r.data)
export const getActiveTimer = () =>
  api.get('/billing/time-entries/timer').then(r => r.data)
export const cancelTimer = () =>
  api.delete('/billing/time-entries/timer')

// Billing settings
export const getBillingSettings = () =>
  api.get('/billing/settings').then(r => r.data)
export const updateBillingSettings = (data) =>
  api.put('/billing/settings', data).then(r => r.data)

export const getInvoices = (params) =>
  api.get('/billing/invoices', { params }).then(r => r.data)
export const getInvoice = (id) =>
  api.get(`/billing/invoices/${id}`).then(r => r.data)
export const generateInvoice = (data) =>
  api.post('/billing/invoices/generate', data).then(r => r.data)
export const updateInvoice = (id, data) =>
  api.patch(`/billing/invoices/${id}`, data).then(r => r.data)
export const recordPayment = (data) =>
  api.post('/billing/payments', data).then(r => r.data)
export const exportInvoice = (id, format = 'pdf') =>
  api.post(`/billing/invoices/${id}/export`, { format }, { responseType: 'blob' }).then(r => r.data)

// ── QuickBooks Online ────────────────────────────────────────────────────────

export const getQBOStatus = () =>
  api.get('/integrations/qbo/status').then(r => r.data)
export const connectQBO = () =>
  api.get('/integrations/qbo/connect').then(r => r.data)
export const disconnectQBO = () =>
  api.post('/integrations/qbo/disconnect').then(r => r.data)
export const getQBOItems = () =>
  api.get('/integrations/qbo/items').then(r => r.data)
export const getQBOMappings = () =>
  api.get('/integrations/qbo/mappings').then(r => r.data)
export const upsertQBOMapping = (data) =>
  api.put('/integrations/qbo/mappings', data).then(r => r.data)
export const syncInvoiceToQBO = (id) =>
  api.post(`/integrations/qbo/sync/invoice/${id}`).then(r => r.data)
export const syncAllToQBO = () =>
  api.post('/integrations/qbo/sync/all').then(r => r.data)

// ── Prompt Management (admin) ────────────────────────────────────────────────

export const getPromptList = () =>
  api.get('/admin/prompts').then(r => r.data)

export const getPromptDetail = (plugin, skill) =>
  api.get(`/admin/prompts/${plugin}/${skill}`).then(r => r.data)

export const savePromptOverride = (plugin, skill, data) =>
  api.put(`/admin/prompts/${plugin}/${skill}`, data).then(r => r.data)

export const resetPromptOverride = (plugin, skill) =>
  api.delete(`/admin/prompts/${plugin}/${skill}`).then(r => r.data)

export const testPrompt = (plugin, skill, data) =>
  api.post(`/admin/prompts/${plugin}/${skill}/test`, data).then(r => r.data)

// ── Cloud Search Admin ──────────────────────────────────────────────────────

export const getCloudSearchStatus = () =>
  api.get('/admin/cloud-search/status').then(r => r.data)

export const testCloudSearch = (data) =>
  api.post('/admin/cloud-search/test', data).then(r => r.data)

export const triggerCloudSync = () =>
  api.post('/admin/cloud-search/sync').then(r => r.data)

export const getCloudMetadata = (params) =>
  api.get('/admin/cloud-search/metadata', { params }).then(r => r.data)

export const invalidateCloudCache = () =>
  api.delete('/admin/cloud-search/cache').then(r => r.data)

// ── SharePoint Admin ────────────────────────────────────────────────────────

export const getSharePointBinding = () =>
  api.get('/admin/sharepoint/binding').then(r => r.data)

export const listSharePointSites = (q) =>
  api.get('/admin/sharepoint/sites', { params: q ? { q } : {} }).then(r => r.data)

export const listSharePointDrives = (siteId) =>
  api.get(`/admin/sharepoint/sites/${encodeURIComponent(siteId)}/drives`).then(r => r.data)

export const saveSharePointBinding = (data) =>
  api.put('/admin/sharepoint/binding', data).then(r => r.data)

// ── Users ─────────────────────────────────────────────────────────────────────

export const searchUsers = (q) =>
  api.get('/users/search', { params: { q } }).then(r => r.data)

// ── SMB File Shares Admin ──────────────────────────────────────────────────────

export const getSmbStatus = () =>
  api.get('/admin/smb/status').then(r => r.data)

export const getSmbActivity = (params) =>
  api.get('/admin/smb/activity', { params }).then(r => r.data)

export const getSmbAgents = () =>
  api.get('/v1/smb/agents').then(r => r.data)

export const generateSmbPairingCode = () =>
  api.post('/v1/smb/pairing-code').then(r => r.data)

export const updateSmbAgent = (agentId, data) =>
  api.patch(`/v1/smb/agents/${agentId}`, data).then(r => r.data)

export const deleteSmbAgent = (agentId) =>
  api.delete(`/v1/smb/agents/${agentId}`).then(r => r.data)

export const getSmbShares = () =>
  api.get('/v1/smb/shares').then(r => r.data)

export const getMatterSmbShares = (matterId) =>
  api.get(`/v1/smb/matters/${matterId}/smb-shares`).then(r => r.data)

export const addMatterSmbShare = (matterId, data) =>
  api.post(`/v1/smb/matters/${matterId}/smb-shares`, data).then(r => r.data)

export const removeMatterSmbShare = (matterId, bindingId) =>
  api.delete(`/v1/smb/matters/${matterId}/smb-shares/${bindingId}`).then(r => r.data)

export const createSmbShare = ({ agent_id, ...data }) =>
  api.post('/v1/smb/shares', data, { params: { agent_id } }).then(r => r.data)

export const deleteSmbShare = (shareId) =>
  api.delete(`/v1/smb/shares/${shareId}`).then(r => r.data)

export const searchSmbFiles = (params) =>
  api.get('/v1/smb/files/search', { params }).then(r => r.data)

// ── Trust Accounting ─────────────────────────────────────────────────────────
export const createTrustAccount = (body) =>
  api.post('/trust/accounts', body).then(r => r.data)

export const listTrustAccounts = (params) =>
  api.get('/trust/accounts', { params }).then(r => r.data)

export const getTrustAccount = (id) =>
  api.get(`/trust/accounts/${id}`).then(r => r.data)

export const updateTrustAccount = (id, body) =>
  api.patch(`/trust/accounts/${id}`, body).then(r => r.data)

export const closeTrustAccount = (id) =>
  api.post(`/trust/accounts/${id}/close`).then(r => r.data)

export const createTrustTransaction = (body) =>
  api.post('/trust/transactions', body).then(r => r.data)

export const listTrustTransactions = (params) =>
  api.get('/trust/transactions', { params }).then(r => r.data)

export const reconcileTrustAccount = (id, body) =>
  api.post(`/trust/accounts/${id}/reconcile`, body).then(r => r.data)

export const getTrustReconciliation = (id) =>
  api.get(`/trust/accounts/${id}/reconciliation`).then(r => r.data)

export const downloadTrustStatementPdf = (accountId, params = {}) =>
  api.get(`/trust/accounts/${accountId}/statement`, { params: { ...params, format: 'pdf' }, responseType: 'blob' }).then(r => r.data)

// ── Firm Branding ────────────────────────────────────────────────────────────

export const getFirmBranding = () =>
  api.get('/firm/branding').then(r => r.data)

export const updateFirmBranding = (body) =>
  api.put('/firm/branding', body).then(r => r.data)

export default api
