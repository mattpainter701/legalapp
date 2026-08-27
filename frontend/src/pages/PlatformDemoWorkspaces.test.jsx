import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../components/dialog/ConfirmProvider'
import {
  getPlatformDemoWorkspaces,
  terminatePlatformDemoWorkspace,
} from '../api'
import { DemoWorkspacesTab } from './PlatformPage'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getPlatformDemoWorkspaces: vi.fn(),
  terminatePlatformDemoWorkspace: vi.fn(),
}))

const workspace = {
  tenant_id: '10000000-0000-0000-0000-000000000001',
  session_id: '20000000-0000-0000-0000-000000000002',
  domain: 'demo-active.demo.invalid',
  prospect_name: 'Active Prospect',
  prospect_email: 'active@example.invalid',
  status: 'active',
  counts_toward_capacity: true,
  quota: 20,
  used: 7,
  reserved: 0,
  created_at: '2026-08-27T12:00:00Z',
  expires_at: '2026-08-28T12:00:00Z',
}

describe('platform demo workspaces', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getPlatformDemoWorkspaces.mockResolvedValue({
      capacity: { limit: 10, active: 5, available: 5 },
      workspaces: [workspace],
    })
    terminatePlatformDemoWorkspace.mockResolvedValue({
      status: 'terminated',
      tenant_id: workspace.tenant_id,
      session_id: workspace.session_id,
      deleted_rows: 42,
    })
  })

  afterEach(cleanup)

  it('shows live capacity and demo identity', async () => {
    render(
      <ConfirmProvider>
        <DemoWorkspacesTab platformKey="platform-token" />
      </ConfirmProvider>,
    )

    expect(await screen.findByText('Active Prospect')).toBeInTheDocument()
    expect(screen.getByText('active@example.invalid')).toBeInTheDocument()
    expect(screen.getByText('demo-active.demo.invalid')).toBeInTheDocument()
    expect(screen.getByText('maximum concurrent demos')).toBeInTheDocument()
    expect(screen.getByText('ready for new demos')).toBeInTheDocument()
    expect(screen.getByText('7 / 20')).toBeInTheDocument()
  })

  it('confirms the exact workspace and refreshes after termination', async () => {
    const user = userEvent.setup()
    render(
      <ConfirmProvider>
        <DemoWorkspacesTab platformKey="platform-token" />
      </ConfirmProvider>,
    )
    await screen.findByText('Active Prospect')

    await user.click(screen.getByRole('button', { name: 'Terminate' }))
    expect(screen.getByRole('alertdialog')).toHaveTextContent(
      'Every other active demo continues uninterrupted',
    )
    await user.click(screen.getByRole('button', { name: 'Terminate workspace' }))

    await waitFor(() => {
      expect(terminatePlatformDemoWorkspace).toHaveBeenCalledWith(
        'platform-token',
        workspace.tenant_id,
        workspace.session_id,
        'Terminated from the platform demo workspace panel',
      )
    })
    expect(getPlatformDemoWorkspaces).toHaveBeenCalledTimes(2)
  })
})
