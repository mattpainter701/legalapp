import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ZoomPanel from './ZoomPanel'
import {
  connectZoomPhoneIntegration,
  getZoomPhoneStatus,
  getZoomStatus,
  saveZoomPhoneAppCredentials,
  testZoomPhoneIntegration,
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
  webhook_status: 'not_configured',
  webhook_verified: false,
  tenant_app_configured: false,
  required_scopes: [],
  missing_scopes: [],
  app_credentials: {},
}

describe('Zoom Phone tenant app setup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getZoomStatus.mockResolvedValue({ configured: false, connected: false })
    getZoomPhoneStatus.mockResolvedValue(emptyPhoneStatus)
    saveZoomPhoneAppCredentials.mockResolvedValue({ configured: true })
    testZoomPhoneIntegration.mockResolvedValue({ sample_count: 2 })
  })
  afterEach(() => cleanup())

  it('saves tenant app credentials without asking for a manual Zoom Account ID', async () => {
    const user = userEvent.setup()
    render(<ZoomPanel />)

    expect(await screen.findByText('Customer Zoom app')).toBeInTheDocument()
    expect(screen.getAllByText('Get account’s call history')).not.toHaveLength(0)
    expect(screen.getAllByText('Get call history detail and call element')).not.toHaveLength(0)
    expect(screen.getAllByText('phone:read:list_call_logs:admin')).not.toHaveLength(0)
    expect(screen.getAllByText('phone:read:call_log:admin')).not.toHaveLength(0)
    expect(screen.getByText('Start authorization from LawHand')).toBeInTheDocument()
    expect(screen.getByText(/Do not use the Add button in Zoom Marketplace’s private listing/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Zoom Account ID/i)).not.toBeInTheDocument()
    const saveButton = screen.getByRole('button', { name: 'Save Zoom app' })
    expect(saveButton).toBeDisabled()

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
    }))
  })

  it('directs tenant authorization through the LawHand connect action', async () => {
    getZoomPhoneStatus.mockResolvedValue({
      ...emptyPhoneStatus,
      configured: true,
      tenant_app_configured: true,
      webhook_secret_configured: true,
      app_credentials: { configured: true, client_id_hint: 'clie…123' },
    })
    const user = userEvent.setup()
    render(<ZoomPanel />)

    const connectButton = await screen.findByRole('button', { name: 'Connect Zoom Phone' })
    expect(connectButton).toBeEnabled()
    await user.click(connectButton)

    expect(connectZoomPhoneIntegration).toHaveBeenCalledTimes(1)
  })

  it('does not repopulate saved secrets and permits rotating only the webhook secret', async () => {
    getZoomPhoneStatus.mockResolvedValue({
      ...emptyPhoneStatus,
      configured: true,
      tenant_app_configured: true,
      webhook_secret_configured: true,
      app_credentials: {
        configured: true,
        client_id_hint: 'clie…123',
      },
    })
    const user = userEvent.setup()
    render(<ZoomPanel />)

    expect(await screen.findByText('Tenant app saved')).toBeInTheDocument()
    expect(screen.getByLabelText('Zoom OAuth client secret')).toHaveValue('')
    const webhookSecret = screen.getByPlaceholderText('Enter to replace saved webhook secret token')
    expect(webhookSecret).toHaveValue('')
    expect(screen.getByText('Webhook signing is configured.')).toBeInTheDocument()

    await user.type(webhookSecret, 'replacement-webhook')
    await user.click(screen.getByRole('button', { name: 'Save Zoom app' }))

    await waitFor(() => expect(saveZoomPhoneAppCredentials).toHaveBeenCalledWith({
      client_id: '',
      client_secret: '',
      webhook_secret_token: 'replacement-webhook',
    }))
  })

  it('keeps Phone API tests available while real-time webhook proof is pending', async () => {
    getZoomPhoneStatus.mockResolvedValue({
      ...emptyPhoneStatus,
      configured: true,
      connected: true,
      status: 'connected',
      health: 'healthy',
      webhook_status: 'pending',
      webhook_verified: false,
      tenant_app_configured: true,
      app_credentials: { configured: true },
    })
    const user = userEvent.setup()
    render(<ZoomPanel />)

    expect(await screen.findAllByText('Phone API connected')).toHaveLength(2)
    expect(screen.getByText('Real-time webhook pending')).toBeInTheDocument()
    expect(screen.getByText('Real-time call delivery is not verified yet')).toBeInTheDocument()
    expect(screen.getByText(/Test connection and call-history import are available now/i)).toBeInTheDocument()
    const testButton = screen.getByRole('button', { name: 'Test connection' })
    expect(testButton).toBeEnabled()

    await user.click(testButton)
    await waitFor(() => expect(testZoomPhoneIntegration).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/Sample calls found: 2/i)).toBeInTheDocument()
  })

  it('shows Phone API and verified real-time delivery as independent healthy states', async () => {
    getZoomPhoneStatus.mockResolvedValue({
      ...emptyPhoneStatus,
      configured: true,
      connected: true,
      status: 'connected',
      webhook_status: 'verified',
      webhook_verified: true,
      tenant_app_configured: true,
      app_credentials: { configured: true },
    })
    render(<ZoomPanel />)

    expect(await screen.findByText('Real-time calls verified')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled()
    expect(screen.queryByText('Real-time call delivery is not verified yet')).not.toBeInTheDocument()
  })

  it('keeps older connected status responses compatible', async () => {
    getZoomPhoneStatus.mockResolvedValue({
      ...emptyPhoneStatus,
      configured: true,
      connected: true,
      status: 'connected',
      webhook_status: undefined,
      webhook_verified: undefined,
      tenant_app_configured: true,
      app_credentials: { configured: true },
    })
    render(<ZoomPanel />)

    expect(await screen.findByText('Real-time calls verified')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled()
  })
})
