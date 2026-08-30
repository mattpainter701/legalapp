import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiClient = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn(),
  interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
}))

vi.mock('axios', () => ({
  default: { create: vi.fn(() => apiClient), post: vi.fn() },
}))

import {
  createCustomerLifecycleReceipt,
  createOperatingSupportRequest,
  getPublicSecurityReviewPacket,
  getPublicServiceStatus,
  requestCustomerOffboarding,
} from './api'

describe('operating-trust API bindings', () => {
  beforeEach(() => vi.clearAllMocks())

  it('uses the public status and downloadable packet routes', async () => {
    apiClient.get.mockResolvedValue({ data: { published_incident_state: 'none_active' } })
    await getPublicServiceStatus()
    await getPublicSecurityReviewPacket()
    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/public/status')
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/public/security-review-packet')
  })

  it('keeps support, lifecycle acceptance, and offboarding as distinct workflows', async () => {
    apiClient.post.mockResolvedValue({ data: { id: 'evidence-id' } })
    const support = { severity: 'S1' }
    const receipt = { receipt_type: 'tenant_export' }
    const offboarding = { delete_categories: ['database:messages'] }
    await createOperatingSupportRequest(support)
    await createCustomerLifecycleReceipt(receipt)
    await requestCustomerOffboarding(offboarding)
    expect(apiClient.post).toHaveBeenNthCalledWith(1, '/compliance/operating/support', support)
    expect(apiClient.post).toHaveBeenNthCalledWith(2, '/compliance/operating/receipts', receipt)
    expect(apiClient.post).toHaveBeenNthCalledWith(3, '/compliance/operating/offboarding', offboarding)
  })
})
