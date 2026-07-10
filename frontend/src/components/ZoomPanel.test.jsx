import React from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ZoomPanel from './ZoomPanel'
import {
  getZoomPhoneStatus,
  getZoomStatus,
  saveZoomPhoneAppCredentials,
  connectZoomPhoneIntegration,
} from '../api'

vi.mock('../api', () => ({
  clearZoomPhoneAppCredentials: vi.fn(),
  connectZoomPhoneIntegration: vi.fn(),
  connectZoomIntegration: vi.fn(),
  disconnectZoomPhoneIntegration: vi.fn(),
  disconnectZoomIntegration: vi.fn(),
  getZoomPhoneStatus: vi.fn(),
  getZoomStatus: vi.fn(),
  saveZoomPhoneAppCredentials: vi.fn(),
  testZoomPhoneIntegration: vi.fn(),
}))

const emptyPhoneStatus = {
  configured: false,
  connected: false,
  status: 'not_configured',
  tenant_app_configured: false,
  required_scopes: [],
  missing_scopes: [],
  app_credentials: {},
}

describe('Zoom Phone tenant account setup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getZoomStatus.mockResolvedValue({ configured: false, connected: false })
    getZoomPhoneStatus.mockResolvedValue(emptyPhoneStatus)
    saveZoomPhoneAppCredentials.mockResolvedValue({ configured: true })
  })
  afterEach(() => cleanup())

  it('requires and submits an explicit Zoom Account ID with new app credentials', async () => {
    const user = userEvent.setup()
    render(<ZoomPanel />)

    const accountId = await screen.findByLabelText(/Zoom Account ID/)
    const saveButton = screen.getByRole('button', { name: 'Save Zoom app' })
    expect(saveButton).toBeDisabled()
    expect(accountId).toHaveAttribute('minlength', '8')
    expect(screen.getByText('Required before this tenant can connect Zoom Phone.')).toBeInTheDocument()

    await user.type(accountId, 'zoom-account-123')
    await user.type(screen.getByLabelText('Zoom OAuth client ID'), 'client-123')
    await user.type(screen.getByLabelText('Zoom OAuth client secret'), 'secret-123')
    expect(saveButton).toBeDisabled()
    await user.type(screen.getByLabelText('Zoom webhook secret token'), 'webhook-123')
    expect(saveButton).toBeEnabled()
    await user.click(saveButton)

    await waitFor(() => expect(saveZoomPhoneAppCredentials).toHaveBeenCalledWith({
      client_id: 'client-123',
      client_secret: 'secret-123',
      webhook_secret_token: 'webhook-123',
      zoom_account_id: 'zoom-account-123',
    }))
  })

  it('shows the saved non-secret account binding without repopulating secrets', async () => {
    getZoomPhoneStatus.mockResolvedValue({
      ...emptyPhoneStatus,
      configured: true,
      tenant_app_configured: true,
      app_credentials: {
        client_id_hint: 'clie…123',
        zoom_account_id: 'zoom-account-456',
        zoom_account_id_configured: true,
      },
    })
    render(<ZoomPanel />)

    expect(await screen.findByLabelText(/Zoom Account ID/)).toHaveValue('zoom-account-456')
    expect(screen.getByLabelText('Zoom OAuth client secret')).toHaveValue('')
    expect(screen.getByLabelText('Zoom webhook secret token')).toHaveValue('')
    expect(screen.getByText('Account binding saved. A signed v3 event plus a successful provider fetch of that exact call must prove this Account ID before imports are enabled.')).toBeInTheDocument()
  })

  it('blocks provider tests but keeps re-authorization available while a grant awaits end-to-end proof', async () => {
    getZoomPhoneStatus.mockResolvedValue({
      ...emptyPhoneStatus,
      configured: true,
      status: 'account_verification_required',
      health: 'account_verification_required',
      tenant_app_configured: true,
      app_credentials: {
        configured: true,
        zoom_account_id: 'zoom-account-456',
      },
    })
    const user = userEvent.setup()
    render(<ZoomPanel />)

    expect(await screen.findByText('Phone account proof pending')).toBeInTheDocument()
    expect(screen.getByText('Place a Zoom Phone test call')).toBeInTheDocument()
    const reauthorize = screen.getByRole('button', { name: 'Re-authorize Phone' })
    expect(reauthorize).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Test connection' })).not.toBeInTheDocument()
    expect(screen.getByText(/call import and Test connection stay blocked/i)).toBeInTheDocument()
    expect(screen.getByText(/pending grant must successfully fetch that exact call/i)).toBeInTheDocument()
    await user.click(reauthorize)
    expect(connectZoomPhoneIntegration).toHaveBeenCalledTimes(1)
  })

  it('lets an existing app add its missing account binding without exposing or re-entering secrets', async () => {
    getZoomPhoneStatus.mockResolvedValue({
      ...emptyPhoneStatus,
      app_credentials: {
        configured: true,
        client_id_hint: 'clie…123',
      },
      webhook_secret_configured: true,
    })
    const user = userEvent.setup()
    render(<ZoomPanel />)

    expect(await screen.findByText('Account ID required')).toBeInTheDocument()
    const accountId = screen.getByLabelText(/Zoom Account ID/)
    await user.type(accountId, 'zoom-account-789')
    await user.click(screen.getByRole('button', { name: 'Save Zoom app' }))

    await waitFor(() => expect(saveZoomPhoneAppCredentials).toHaveBeenCalledWith({
      client_id: '',
      client_secret: '',
      webhook_secret_token: '',
      zoom_account_id: 'zoom-account-789',
    }))
  })
})
