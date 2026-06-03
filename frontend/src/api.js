import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true, // Allow httpOnly cookies to be sent with requests
})

// Request interceptor: httpOnly cookies are now handled automatically by the browser
// No longer injecting Authorization header — relies on browser's cookie auto-send
api.interceptors.request.use(
  (config) => {
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor: handle 401 by clearing state and redirecting
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      // Clear localStorage fallback (for backward compat during transition)
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      // Cookie will be cleared by the logout endpoint
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// Auth
export const getMe = () => api.get('/auth/me').then((r) => r.data)

export const register = (data) =>
  api.post('/auth/register', data).then((r) => r.data)

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
export const getConversations = () =>
  api.get('/conversations').then((r) => r.data)

export const createConversation = (title) =>
  api.post('/conversations', title ? { title } : {}).then((r) => r.data)

export const getConversation = (id) =>
  api.get(`/conversations/${id}`).then((r) => r.data)

export const sendMessage = (conversationId, content, includePublic = true, usePremium = false) =>
  api
    .post(`/conversations/${conversationId}/messages`, {
      content,
      include_public: includePublic,
      use_premium_llm: usePremium,
    })
    .then((r) => r.data)

export const streamMessage = async function* (conversationId, content, includePublic = true, usePremium = false) {
  const token = localStorage.getItem('token')
  const headers = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  const response = await fetch(`${BASE_URL}/conversations/${conversationId}/messages/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      content,
      include_public: includePublic,
      use_premium_llm: usePremium,
    }),
  })

  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const chunk = decoder.decode(value)
      const lines = chunk.split('\n')

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6)
          if (data && data !== '[STREAM_COMPLETE]' && !data.startsWith('[ERROR]')) {
            yield data
          } else if (data === '[STREAM_COMPLETE]' || data.startsWith('[ERROR]')) {
            yield data
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
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

// Admin
export const getAdminUsers = () =>
  api.get('/admin/users').then((r) => r.data.users ?? r.data)

export const getAdminUsage = () =>
  api.get('/admin/usage').then((r) => r.data)

export const getAdminTenant = () =>
  api.get('/admin/tenant').then((r) => r.data)

// ── Plugin API ────────────────────────────────────────────────────────────────
export const getPlugins = () => api.get('/plugins').then((r) => r.data)
export const getPluginProfile = (plugin) => api.get(`/plugins/${plugin}/profile`).then((r) => r.data)
export const savePluginProfile = (plugin, data) => api.put(`/plugins/${plugin}/profile`, data).then((r) => r.data)
export const runColdStart = (plugin, message, step) =>
  api.post(`/plugins/${plugin}/cold-start`, { message, step }).then((r) => r.data)
export const executeSkill = (plugin, skill, data) =>
  api.post(`/plugins/${plugin}/${skill}`, data).then((r) => r.data)

// Matters
export const getMatters = () => api.get('/plugins/litigation/matters').then((r) => r.data)
export const createMatter = (data) => api.post('/plugins/litigation/matters', data).then((r) => r.data)
export const getMatter = (id) => api.get(`/plugins/litigation/matters/${id}`).then((r) => r.data)
export const updateMatter = (id, data) => api.patch(`/plugins/litigation/matters/${id}`, data).then((r) => r.data)
export const addMatterEvent = (id, data) => api.post(`/plugins/litigation/matters/${id}/events`, data).then((r) => r.data)

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

// ── Mediation ──────────────────────────────────────────────────────────────

export const getMediationCases = () =>
  api.get('/plugins/mediation/cases').then(r => r.data)

export const createMediationCase = (data) =>
  api.post('/plugins/mediation/cases', data).then(r => r.data)

export const getMediationCase = (id) =>
  api.get(`/plugins/mediation/cases/${id}`).then(r => r.data)

export const updateMediationCase = (id, data) =>
  api.patch(`/plugins/mediation/cases/${id}`, data).then(r => r.data)

export const addMediationEvent = (id, data) =>
  api.post(`/plugins/mediation/cases/${id}/events`, data).then(r => r.data)

// Billing
export const getBillingStatus = () => api.get('/billing/status').then((r) => r.data)
export const createCheckoutSession = () => api.post('/billing/checkout-session').then((r) => r.data)
export const createPortalSession = () => api.post('/billing/portal').then((r) => r.data)

// MCP
export const getMcpInfo = () => api.get('/mcp/api-key').then((r) => r.data)
export const regenerateMcpApiKey = () => api.post('/mcp/api-key').then((r) => r.data)

// Platform (uses platform key header — passed explicitly)
const platformApi = (platformKey) =>
  axios.create({
    baseURL: BASE_URL,
    headers: { 'X-Platform-Key': platformKey },
  })

export const getPlatformTenants = (key, page = 1) =>
  platformApi(key).get(`/platform/tenants?page=${page}`).then((r) => r.data)

export const getPlatformTenant = (key, id) =>
  platformApi(key).get(`/platform/tenants/${id}`).then((r) => r.data)

export const updatePlatformTenant = (key, id, data) =>
  platformApi(key).put(`/platform/tenants/${id}`, data).then((r) => r.data)

export const getPlatformUsage = (key) =>
  platformApi(key).get('/platform/usage').then((r) => r.data)

export const getPlatformHealth = (key) =>
  platformApi(key).get('/platform/health').then((r) => r.data)

export default api
