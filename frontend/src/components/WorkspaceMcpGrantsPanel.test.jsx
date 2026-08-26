import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getWorkspaceMcpGrants, revokeWorkspaceMcpGrant } from '../api'
import WorkspaceMcpGrantsPanel from './WorkspaceMcpGrantsPanel'

vi.mock('../api', () => ({
  getWorkspaceMcpGrants: vi.fn(),
  revokeWorkspaceMcpGrant: vi.fn(),
}))

const activeGrant = {
  id: 'grant-active',
  client_id: 'claude-desktop',
  client_name: 'Claude Desktop',
  status: 'active',
  scopes: [{ name: 'matters:read', label: 'Read matters' }],
  created_at: '2026-08-20T12:00:00Z',
  expires_at: '2099-08-20T12:00:00Z',
  last_used_at: null,
  revoked_at: null,
}

describe('WorkspaceMcpGrantsPanel cleanup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    revokeWorkspaceMcpGrant.mockResolvedValue({ status: 'revoked' })
  })

  afterEach(() => cleanup())

  it('shows only active connections when historical rows are returned', async () => {
    getWorkspaceMcpGrants.mockResolvedValue({
      items: [
        activeGrant,
        { ...activeGrant, id: 'grant-revoked', client_name: 'Old Codex', status: 'revoked', revoked_at: '2026-08-21T12:00:00Z' },
        { ...activeGrant, id: 'grant-expired', client_name: 'Old ChatGPT', status: 'expired' },
      ],
    })

    render(<WorkspaceMcpGrantsPanel />)

    expect(await screen.findByText('Claude Desktop')).toBeInTheDocument()
    expect(screen.queryByText('Old Codex')).not.toBeInTheDocument()
    expect(screen.queryByText('Old ChatGPT')).not.toBeInTheDocument()
  })

  it('removes a connection immediately after a successful revoke', async () => {
    const user = userEvent.setup()
    getWorkspaceMcpGrants.mockResolvedValue({ items: [activeGrant] })
    render(<WorkspaceMcpGrantsPanel />)

    await user.click(await screen.findByRole('button', { name: 'Revoke' }))
    await user.click(screen.getByRole('button', { name: 'Revoke access' }))

    await waitFor(() => expect(revokeWorkspaceMcpGrant).toHaveBeenCalledWith(activeGrant.id))
    expect(screen.queryByText('Claude Desktop')).not.toBeInTheDocument()
    expect(screen.getByText('No active Workspace MCP assistants are connected.')).toBeInTheDocument()
  })

  it('keeps the active connection visible when revoke fails', async () => {
    const user = userEvent.setup()
    getWorkspaceMcpGrants.mockResolvedValue({ items: [activeGrant] })
    revokeWorkspaceMcpGrant.mockRejectedValue({ response: { data: { detail: 'Cleanup failed' } } })
    render(<WorkspaceMcpGrantsPanel />)

    await user.click(await screen.findByRole('button', { name: 'Revoke' }))
    await user.click(screen.getByRole('button', { name: 'Revoke access' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Cleanup failed')
    expect(screen.getByText('Claude Desktop')).toBeInTheDocument()
  })
})
