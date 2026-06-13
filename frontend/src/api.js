import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || '/api'
export const API_BASE_URL = BASE_URL

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

// Response interceptor: on 401, attempt a single rotating-refresh, then retry the
// original request once. Concurrent 401s share one in-flight refresh (single-flight)
// so we never fire multiple /auth/refresh calls. If refresh fails, clear state and
// redirect to login.
let refreshPromise = null

// Paths for which a 401 must NOT trigger a refresh attempt (would loop / is terminal).
const NO_REFRESH_PATHS = ['/auth/refresh', '/auth/login', '/auth/register', '/auth/logout']

const redirectToLogin = () => {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  window.location.href = '/login'
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const { response, config } = error
    if (!response || response.status !== 401 || !config) {
      return Promise.reject(error)
    }
    const url = config.url || ''
    if (config._retried || NO_REFRESH_PATHS.some((p) => url.includes(p))) {
      // Already retried once, or this IS an auth endpoint — give up.
      redirectToLogin()
      return Promise.reject(error)
    }

    try {
      // Single-flight: the first 401 starts the refresh; others await it.
      if (!refreshPromise) {
        refreshPromise = api.post('/auth/refresh').finally(() => {
          refreshPromise = null
        })
      }
      await refreshPromise
    } catch (refreshError) {
      redirectToLogin()
      return Promise.reject(refreshError)
    }

    // Refresh succeeded (new cookies set) — retry the original request once.
    config._retried = true
    return api(config)
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
export const getConversations = (params) =>
  api.get('/conversations', { params }).then((r) => r.data)

export const createConversation = (data) =>
  api.post('/conversations', typeof data === 'string' ? { title: data } : (data || {})).then((r) => r.data)

export const getConversation = (id) =>
  api.get(`/conversations/${id}`).then((r) => r.data)

export const sendMessage = (conversationId, content, includePublic = true, usePremium = false, attachmentIds = []) =>
  api
    .post(`/conversations/${conversationId}/messages`, {
      content,
      include_public: includePublic,
      use_premium_llm: usePremium,
      attachment_ids: attachmentIds,
    })
    .then((r) => r.data)

export const streamMessage = async function* (conversationId, content, includePublic = true, usePremium = false, attachmentIds = []) {
  const response = await fetch(`${BASE_URL}/conversations/${conversationId}/messages/stream`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      content,
      include_public: includePublic,
      use_premium_llm: usePremium,
      attachment_ids: attachmentIds,
    }),
  })

  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    // 504 Gateway Timeout is common on cold starts — surface a clear message
    if (response.status === 504) {
      throw new Error('The AI service is warming up. Please try your query again in a moment.')
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
export const updateSeatCount = (count) =>
  api.put('/admin/licensing/seats', { flat_seat_count: count }).then((r) => r.data)

// Integrations & Permissions
export const getIntegrationsHealth = () =>
  api.get('/admin/integrations/health').then((r) => r.data)
export const getAdminPermissions = () =>
  api.get('/admin/permissions').then((r) => r.data)
export const triggerUserSync = () =>
  api.post('/scheduler/agents/user-sync/run').then((r) => r.data)
export const retryCloudInit = () =>
  api.post('/integrations/cloud-init/retry').then((r) => r.data)

// ── Microsoft Teams ───────────────────────────────────────────────────────────
export const getIntegrationStatus = () =>
  api.get('/integrations/status').then((r) => r.data)
export const getTeamsTeams = () =>
  api.get('/integrations/teams/teams').then((r) => r.data)
export const getTeamsChannels = (teamId) =>
  api.get(`/integrations/teams/teams/${teamId}/channels`).then((r) => r.data)
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

export const voidSignatureRequest = (matterId, requestId) =>
  api.post(`/matters/${matterId}/signatures/${requestId}/void`).then((r) => r.data)

// E-signature (client portal side)
export const listClientPortalSignatures = () =>
  clientPortalApi.get('/portal/client/signatures').then((r) => r.data)

export const signClientPortalSignature = (requestId, data) =>
  clientPortalApi.post(`/portal/client/signatures/${requestId}/sign`, data).then((r) => r.data)

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

export const saveLLMRoutes = (key, data) =>
  platformApi(key).put('/platform/llm/routes', data).then((r) => r.data)

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

export const createTask = (data) =>
  api.post('/tasks', data).then(r => r.data)

export const getTask = (id) =>
  api.get(`/tasks/${id}`).then(r => r.data)

export const updateTask = (id, data) =>
  api.patch(`/tasks/${id}`, data).then(r => r.data)

export const deleteTask = (id) =>
  api.delete(`/tasks/${id}`)

export const getOverdueTasks = (params = {}) =>
  api.get('/tasks/overdue', { params }).then(r => r.data)

export const getUpcomingTasks = (params = {}) =>
  api.get('/tasks/upcoming', { params }).then(r => r.data)

export const sendTaskReminder = (taskId) =>
  api.post(`/tasks/${taskId}/remind`).then(r => r.data)

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
  `/api/matters/${matterId}/documents/${docId}/download`

export const getMatterCloudFolder = (matterId) =>
  api.get(`/matters/${matterId}/cloud-folder`).then(r => r.data)

export const provisionMatterCloudFolder = (matterId) =>
  api.post(`/matters/${matterId}/cloud-folder/provision`).then(r => r.data)

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
    .post('/email/calendar', { provider, sync_deadlines: true })
    .then(r => r.data)

export const getCalendarProviders = () =>
  api.get('/auth/calendar-providers').then(r => r.data)

export const connectCalendarIntegration = (provider) => {
  window.location.href = `${BASE_URL}/integrations/${provider}/connect?intent=user`
}

// ── Document Templates ──────────────────────────────────────────────────────

export const getTemplates = () =>
  api.get('/templates').then(r => r.data)

export const createTemplate = (data) =>
  api.post('/templates', data).then(r => r.data)

export const getTemplate = (id) =>
  api.get(`/templates/${id}`).then(r => r.data)

export const updateTemplate = (id, data) =>
  api.patch(`/templates/${id}`, data).then(r => r.data)

export const deleteTemplate = (id) =>
  api.delete(`/templates/${id}`).then(r => r.data)

export const renderTemplate = (id, data) =>
  api.post(`/templates/${id}/render`, data).then(r => r.data)

// ── Reports / Budget ──────────────────────────────────────────────────────────

export const getMatterBudget = (matterId) =>
  api.get(`/reports/matters/${matterId}/budget`).then(r => r.data)

// ── Matters V2 ─────────────────────────────────────────────────────────────────
export const getMattersV2 = (params) =>
  api.get('/matters', { params }).then(r => r.data)
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

export default api
