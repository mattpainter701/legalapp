import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiClient = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
}))

vi.mock('axios', () => ({
  default: { create: vi.fn(() => apiClient), post: vi.fn() },
}))

import {
  getClientPortalSignatureFields,
  signClientPortalSignature,
  uploadClientPortalSignedCopy,
} from './api'

describe('client portal signing wrappers', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('reads the field manifest for a request', async () => {
    apiClient.get.mockResolvedValueOnce({ data: { fields: [], fill_supported: true } })
    expect(await getClientPortalSignatureFields('req-1')).toEqual({ fields: [], fill_supported: true })
    expect(apiClient.get).toHaveBeenCalledWith('/portal/client/signatures/req-1/fields')
  })

  it('always sends field_values with the typed signature and consent', async () => {
    apiClient.post.mockResolvedValue({ data: { status: 'completed' } })
    await signClientPortalSignature('req-1', {
      typed_signature: 'Jane Smith',
      consent_to_electronic_signature: true,
      consent_text_version: 'clarity-esign-consent-v1',
      field_values: { 'acroform:client_name': 'Jane Smith', 'acroform:agree': 'true' },
    })
    expect(apiClient.post).toHaveBeenCalledWith('/portal/client/signatures/req-1/sign', {
      typed_signature: 'Jane Smith',
      consent_to_electronic_signature: true,
      consent_text_version: 'clarity-esign-consent-v1',
      field_values: { 'acroform:client_name': 'Jane Smith', 'acroform:agree': 'true' },
    })
    // The fallback path signs without any in-document fields; the server still
    // expects the map to be present.
    await signClientPortalSignature('req-1', { typed_signature: 'Jane Smith', consent_to_electronic_signature: true })
    expect(apiClient.post.mock.lastCall[1]).toMatchObject({ field_values: {} })
  })

  it('uploads the signed copy as multipart under the "file" field', async () => {
    apiClient.post.mockResolvedValueOnce({ data: { submitted_document_id: 'doc-9' } })
    const file = new File(['%PDF-1.4'], 'signed.pdf', { type: 'application/pdf' })
    expect(await uploadClientPortalSignedCopy('req-1', file)).toEqual({ submitted_document_id: 'doc-9' })
    const [path, body, config] = apiClient.post.mock.lastCall
    expect(path).toBe('/portal/client/signatures/req-1/upload')
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('file')).toBe(file)
    expect(config.headers['Content-Type']).toBe('multipart/form-data')
  })
})
