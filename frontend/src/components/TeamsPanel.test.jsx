import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TeamsPanel from './TeamsPanel'
import {
  deleteTeamsLink,
  getIntegrationStatus,
  getMattersV2,
  getTeamsChannels,
  getTeamsEventTypes,
  getTeamsLinks,
  getTeamsNotificationSettings,
  getTeamsTeams,
  getTeamsVoiceStatus,
  updateTeamsNotificationSettings,
  updateTeamsVoiceSettings,
} from '../api'

vi.mock('../api', () => ({
  API_BASE_URL: 'http://api.test',
  createTeamsChannel: vi.fn(),
  createTeamsLink: vi.fn(),
  createTeamsVoiceSubscription: vi.fn(),
  deleteTeamsLink: vi.fn(),
  deleteTeamsVoiceSubscription: vi.fn(),
  getIntegrationStatus: vi.fn(),
  getMattersV2: vi.fn(),
  getTeamsChannels: vi.fn(),
  getTeamsEventTypes: vi.fn(),
  getTeamsLinks: vi.fn(),
  getTeamsNotificationSettings: vi.fn(),
  getTeamsTeams: vi.fn(),
  getTeamsVoiceStatus: vi.fn(),
  sendTeamsTestMessage: vi.fn(),
  syncTeamsVoiceCalls: vi.fn(),
  testTeamsVoiceConnection: vi.fn(),
  updateTeamsNotificationSettings: vi.fn(),
  updateTeamsVoiceSettings: vi.fn(),
}))

const connectedStatus = {
  microsoft: { connected: true, teams_connected: true, teams_missing_scopes: [] },
}

const voiceStatus = {
  feature_enabled: true,
  configured: false,
  enabled: false,
  entra_tenant_id: null,
  app_credentials_source: 'platform',
  required_application_permission: 'CallRecords.Read.All',
  subscription_active: false,
  webhook_url: 'http://api.test/api/integrations/teams/voice/webhook/tenant-1',
  admin_consent_url: null,
  captured_call_count: 0,
}

describe('TeamsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getIntegrationStatus.mockResolvedValue(connectedStatus)
    getTeamsTeams.mockResolvedValue([{ id: 'team-1', display_name: 'Litigation' }])
    getTeamsChannels.mockResolvedValue([{ id: 'chan-1', display_name: 'General' }])
    getTeamsLinks.mockResolvedValue([])
    getTeamsEventTypes.mockResolvedValue([
      {
        event_type: 'deadline_approaching',
        label: 'Deadline approaching',
        description: 'A matter deadline falls due within 14 days.',
      },
    ])
    getTeamsNotificationSettings.mockResolvedValue([])
    getTeamsVoiceStatus.mockResolvedValue(voiceStatus)
    getMattersV2.mockResolvedValue([{ id: 'matter-1', matter_name: 'Acme v. Globex' }])
  })
  afterEach(() => cleanup())

  it('gates on a missing Microsoft connection', async () => {
    getIntegrationStatus.mockResolvedValue({ microsoft: { connected: false } })
    render(<TeamsPanel />)
    expect(await screen.findByText('Connect Microsoft 365 first')).toBeInTheDocument()
  })

  it('names the missing scopes when Teams consent is incomplete', async () => {
    getIntegrationStatus.mockResolvedValue({
      microsoft: {
        connected: true,
        teams_connected: false,
        teams_missing_scopes: ['ChannelMessage.Send'],
      },
    })
    render(<TeamsPanel />)
    expect(await screen.findByText('Enable Microsoft Teams')).toBeInTheDocument()
    expect(
      screen.getByText(/Missing permissions: ChannelMessage\.Send/),
    ).toBeInTheDocument()
  })

  it('surfaces a Graph failure instead of showing an empty team list', async () => {
    getTeamsTeams.mockRejectedValue({
      response: {
        data: {
          detail: {
            error: 'teams_token_unavailable',
            message: 'Microsoft Graph denied access to your Teams list (403).',
          },
        },
      },
    })
    render(<TeamsPanel />)
    expect(
      await screen.findByText(/Microsoft Graph denied access to your Teams list/),
    ).toBeInTheDocument()
  })

  it('shows the matter name on a linked channel, not a raw id', async () => {
    getTeamsLinks.mockResolvedValue([
      {
        id: 'link-1',
        matter_id: '11111111-2222-3333-4444-555555555555',
        matter_name: 'Acme v. Globex',
        team_id: 'team-1',
        team_display_name: 'Litigation',
        channel_id: 'chan-1',
        channel_display_name: 'General',
        is_active: true,
      },
    ])
    render(<TeamsPanel />)
    const linked = await screen.findByText(/Linked matters/)
    const card = linked.closest('div')
    expect(within(card).getByText('Acme v. Globex')).toBeInTheDocument()
    expect(within(card).getByText(/Litigation · General/)).toBeInTheDocument()
    // The old panel rendered a truncated UUID here, which named nothing.
    expect(screen.queryByText(/11111111/)).not.toBeInTheDocument()
  })

  it('asks before unlinking a matter', async () => {
    const user = userEvent.setup()
    getTeamsLinks.mockResolvedValue([
      {
        id: 'link-1',
        matter_id: 'matter-1',
        matter_name: 'Acme v. Globex',
        team_id: 'team-1',
        channel_id: 'chan-1',
        is_active: true,
      },
    ])
    deleteTeamsLink.mockResolvedValue({})
    render(<TeamsPanel />)

    const unlink = await screen.findByRole('button', { name: /Unlink/ })
    await user.click(unlink)
    // First click arms the action; nothing is deleted yet.
    expect(deleteTeamsLink).not.toHaveBeenCalled()
    expect(
      await screen.findByRole('button', { name: /Confirm unlink/ }),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Confirm unlink/ }))
    await waitFor(() => expect(deleteTeamsLink).toHaveBeenCalledWith('link-1'))
  })

  it('routes an event to a channel from the server catalogue', async () => {
    const user = userEvent.setup()
    updateTeamsNotificationSettings.mockResolvedValue([])
    render(<TeamsPanel />)

    await user.click(await screen.findByRole('button', { name: /Notifications/ }))
    expect(await screen.findByText('Deadline approaching')).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox'))
    const selects = screen.getAllByRole('combobox')
    await user.selectOptions(selects[0], 'team-1')
    await waitFor(() => expect(getTeamsChannels).toHaveBeenCalledWith('team-1'))
    await user.selectOptions(screen.getAllByRole('combobox')[1], 'chan-1')
    await user.click(screen.getByRole('button', { name: /Save routing/ }))

    await waitFor(() =>
      expect(updateTeamsNotificationSettings).toHaveBeenCalledWith([
        expect.objectContaining({
          event_type: 'deadline_approaching',
          team_id: 'team-1',
          channel_id: 'chan-1',
          matter_id: null,
          is_enabled: true,
        }),
      ]),
    )
  })

  it('refuses to save a routing row with no channel picked', async () => {
    const user = userEvent.setup()
    render(<TeamsPanel />)

    await user.click(await screen.findByRole('button', { name: /Notifications/ }))
    await user.click(await screen.findByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /Save routing/ }))

    expect(
      await screen.findByText('Pick a team and channel for every enabled event.'),
    ).toBeInTheDocument()
    expect(updateTeamsNotificationSettings).not.toHaveBeenCalled()
  })

  it('preserves matter-specific routes when saving firm-wide defaults', async () => {
    const user = userEvent.setup()
    getTeamsNotificationSettings.mockResolvedValue([
      {
        id: 'setting-1',
        event_type: 'deadline_approaching',
        team_id: 'team-9',
        channel_id: 'chan-9',
        matter_id: 'matter-1',
        matter_name: 'Acme v. Globex',
        is_enabled: true,
      },
    ])
    updateTeamsNotificationSettings.mockResolvedValue([])
    render(<TeamsPanel />)

    await user.click(await screen.findByRole('button', { name: /Notifications/ }))
    expect(await screen.findByText('Matter-specific overrides')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Save routing/ }))

    await waitFor(() =>
      expect(updateTeamsNotificationSettings).toHaveBeenCalledWith([
        expect.objectContaining({ matter_id: 'matter-1', channel_id: 'chan-9' }),
      ]),
    )
  })

  it('walks the voice setup and reports the directory requirement', async () => {
    const user = userEvent.setup()
    updateTeamsVoiceSettings.mockResolvedValue({
      ...voiceStatus,
      configured: true,
      entra_tenant_id: 'contoso-guid',
      admin_consent_url:
        'https://login.microsoftonline.com/contoso-guid/adminconsent?client_id=abc',
    })
    render(<TeamsPanel />)

    await user.click(await screen.findByRole('button', { name: /Voice/ }))
    expect(await screen.findByText('Teams Phone call capture')).toBeInTheDocument()
    // The permission is named on screen so an admin knows what they are consenting to.
    expect(screen.getAllByText(/CallRecords\.Read\.All/).length).toBeGreaterThan(0)

    await user.type(
      screen.getByPlaceholderText('00000000-0000-0000-0000-000000000000'),
      'contoso-guid',
    )
    await user.click(screen.getByRole('button', { name: /Save directory/ }))

    await waitFor(() =>
      expect(updateTeamsVoiceSettings).toHaveBeenCalledWith({
        entra_tenant_id: 'contoso-guid',
      }),
    )
    expect(
      await screen.findByRole('link', { name: /Microsoft consent screen/ }),
    ).toBeInTheDocument()
  })

  it('reports live-notification state in the status strip', async () => {
    getTeamsVoiceStatus.mockResolvedValue({
      ...voiceStatus,
      configured: true,
      enabled: true,
      subscription_active: true,
      captured_call_count: 12,
    })
    render(<TeamsPanel />)
    expect(await screen.findByText('Voice capture live')).toBeInTheDocument()
  })

  it('distinguishes enabled-but-unsubscribed voice capture', async () => {
    getTeamsVoiceStatus.mockResolvedValue({
      ...voiceStatus,
      configured: true,
      enabled: true,
      subscription_active: false,
    })
    render(<TeamsPanel />)
    // Capture still works via the hourly sweep; saying "live" would overstate it.
    expect(await screen.findByText('Voice capture on (hourly)')).toBeInTheDocument()
  })

  it('surfaces a failed background voice run', async () => {
    getTeamsVoiceStatus.mockResolvedValue({
      ...voiceStatus,
      configured: true,
      enabled: true,
      last_sync_status: 'subscription_error',
      last_sync_error: 'Microsoft Graph denied access while creating the subscription.',
    })
    const user = userEvent.setup()
    render(<TeamsPanel />)
    await user.click(await screen.findByRole('button', { name: /Voice/ }))
    expect(
      await screen.findByText(/denied access while creating the subscription/),
    ).toBeInTheDocument()
  })
})

describe('call source labelling', () => {
  it('names both captured providers and falls back to manual', async () => {
    const { callSourceBadge, callSourceLabel, isCapturedSource } = await import(
      './intake/callSource'
    )
    expect(callSourceLabel('teams_voice')).toBe('Microsoft Teams')
    expect(callSourceLabel('zoom_phone')).toBe('Zoom Phone')
    expect(callSourceLabel('manual')).toBe('Manual')
    expect(callSourceLabel(undefined)).toBe('Manual')
    expect(callSourceBadge('teams_voice')).toBe('Teams')
    expect(callSourceBadge('manual')).toBeNull()
    expect(isCapturedSource('teams_voice')).toBe(true)
    expect(isCapturedSource('manual')).toBe(false)
  })
})
