import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import QBOPanel from './QBOPanel'

const api = vi.hoisted(() => ({
  connectQBO: vi.fn(),
  disconnectQBO: vi.fn(),
  getQBOAccounts: vi.fn(),
  getQBOItems: vi.fn(),
  getQBOMappings: vi.fn(),
  getQBOStatus: vi.fn(),
  syncAllToQBO: vi.fn(),
  updateQBOSettings: vi.fn(),
  upsertQBOMapping: vi.fn(),
}))

vi.mock('../api', () => api)
vi.mock('./dialog/ConfirmProvider', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}))

const connectedStatus = {
  connected: true,
  sandbox_mode: false,
  qbo_realm_id: 'realm-1',
  qbo_ar_account_id: null,
}

describe('QBOPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getQBOStatus.mockResolvedValue(connectedStatus)
    api.getQBOMappings.mockResolvedValue([])
    api.getQBOItems.mockResolvedValue([{ id: '7', name: 'Legal Services' }])
    api.getQBOAccounts.mockResolvedValue([{ id: '84', name: 'Accounts Receivable' }])
    window.history.replaceState({}, '', '/admin?tab=qbo')
  })

  afterEach(cleanup)

  it('shows a successful connection return and cleans its one-time URL flag', async () => {
    window.history.replaceState({}, '', '/admin?tab=qbo&qbo=connected')

    render(<QBOPanel />)

    expect(await screen.findByText('QuickBooks Online connected successfully.')).toBeInTheDocument()
    await waitFor(() => expect(window.location.search).toBe('?tab=qbo'))
  })

  it('keeps account settings available when only the item catalogue fails', async () => {
    api.getQBOItems.mockRejectedValue(new Error('throttled'))

    render(<QBOPanel />)

    expect(await screen.findByText(
      'Failed to fetch QuickBooks service items. Account settings are still available.'
    )).toBeInTheDocument()
    expect(await screen.findByRole('option', { name: 'Accounts Receivable' })).toBeInTheDocument()
  })
})
