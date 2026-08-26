import { useState } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getAdminWorkspaceMcpGrants,
  revokeAdminWorkspaceMcpGrant,
} from '../../api'
import { ConfirmProvider } from '../../components/dialog/ConfirmProvider'
import {
  getUserMcpAccessState,
  UserMcpAccessCell,
  UserMcpAccessDrawer,
} from './UserMcpAccess'

vi.mock('../../api', () => ({
  getAdminWorkspaceMcpGrants: vi.fn(),
  revokeAdminWorkspaceMcpGrant: vi.fn(),
}))

const baseUser = {
  id: 'user-1',
  email: 'alex@example.test',
  full_name: 'Alex Smith',
  is_active: true,
  license_active: true,
  privacy_mode: false,
  workspace_mcp_enabled: true,
  workspace_mcp_active_grant_count: 0,
}

const activeGrant = {
  id: 'grant-1',
  client_id: 'claude-desktop',
  client_name: 'Claude Desktop',
  scopes: ['matters:read', 'tasks:read'],
  status: 'active',
  created_at: '2026-08-20T12:00:00Z',
  expires_at: '2099-08-20T12:00:00Z',
  last_used_at: '2026-08-24T12:00:00Z',
}

function renderDrawer(overrides = {}) {
  const props = {
    user: { ...baseUser, workspace_mcp_active_grant_count: 1 },
    workspace: { deployment_enabled: true, tenant_enabled: true },
    onToggle: vi.fn().mockResolvedValue(true),
    onClose: vi.fn(),
    onConnectionsChanged: vi.fn(),
    onNavigateMcp: vi.fn(),
    ...overrides,
  }
  render(<ConfirmProvider><UserMcpAccessDrawer {...props} /></ConfirmProvider>)
  return props
}

function DrawerHarness() {
  const [open, setOpen] = useState(false)
  return (
    <ConfirmProvider>
      <button type="button" onClick={() => setOpen(true)}>Manage Alex MCP</button>
      {open && (
        <UserMcpAccessDrawer
          user={{ ...baseUser, workspace_mcp_active_grant_count: 1 }}
          workspace={{ deployment_enabled: true, tenant_enabled: true }}
          onToggle={vi.fn().mockResolvedValue(true)}
          onClose={() => setOpen(false)}
          onConnectionsChanged={vi.fn()}
          onNavigateMcp={vi.fn()}
        />
      )}
    </ConfirmProvider>
  )
}

describe('getUserMcpAccessState', () => {
  it.each([
    [{ is_active: false }, null, 'Inactive account'],
    [{ license_active: false }, null, 'Unlicensed'],
    [{}, { deployment_enabled: false, tenant_enabled: true }, 'Platform unavailable'],
    [{}, { deployment_enabled: true, tenant_enabled: false }, 'Disabled by firm'],
    [{ workspace_mcp_enabled: false }, null, 'Disabled for user'],
    [{ privacy_mode: true }, null, 'Paused by Privacy Mode'],
    [{}, { status_available: false }, 'Status unavailable'],
    [{ workspace_mcp_active_grant_count: 2 }, null, 'Connected (2)'],
    [{}, null, 'Ready to connect'],
  ])('returns the effective state for %j', (userPatch, workspace, label) => {
    expect(getUserMcpAccessState({ ...baseUser, ...userPatch }, workspace).label).toBe(label)
  })

  it('prioritizes inactive account status over every lower-level gate', () => {
    const state = getUserMcpAccessState({
      ...baseUser,
      is_active: false,
      license_active: false,
      privacy_mode: true,
      workspace_mcp_enabled: false,
      workspace_mcp_active_grant_count: 3,
    }, { deployment_enabled: false, tenant_enabled: false })

    expect(state.key).toBe('inactive')
  })
})

describe('UserMcpAccessCell', () => {
  it('separates the effective connection state from the firm policy switch', async () => {
    const onToggle = vi.fn()
    const onManage = vi.fn()
    const user = { ...baseUser, workspace_mcp_active_grant_count: 2 }
    const actor = userEvent.setup()

    render(
      <UserMcpAccessCell
        user={user}
        workspace={{ deployment_enabled: true, tenant_enabled: true }}
        onToggle={onToggle}
        onManage={onManage}
      />
    )

    expect(screen.getByText('Connected (2)')).toBeInTheDocument()
    expect(screen.getByText('2 connected clients')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: `Allow Workspace MCP access for ${user.email}` })).toHaveAttribute('aria-checked', 'true')

    await actor.click(screen.getByRole('button', { name: `Manage MCP access for ${user.email}` }))
    expect(onManage).toHaveBeenCalledWith(user)
  })

  it('only surfaces Privacy Mode when it blocks effective access', () => {
    render(
      <UserMcpAccessCell
        user={{ ...baseUser, privacy_mode: true }}
        workspace={null}
        onToggle={vi.fn()}
        onManage={vi.fn()}
      />
    )

    expect(screen.getByText('Paused by Privacy Mode')).toBeInTheDocument()
    expect(screen.getByText('User must turn it off in Profile')).toBeInTheDocument()
    expect(screen.queryByText('Privacy Mode: Off')).not.toBeInTheDocument()
  })
})

describe('UserMcpAccessDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getAdminWorkspaceMcpGrants.mockResolvedValue({ items: [activeGrant] })
    revokeAdminWorkspaceMcpGrant.mockResolvedValue({ id: activeGrant.id, status: 'revoked' })
  })

  afterEach(() => cleanup())

  it('loads and displays actual connected clients in an accessible dialog', async () => {
    getAdminWorkspaceMcpGrants.mockResolvedValue({
      items: [
        activeGrant,
        { ...activeGrant, id: 'invalid-grant', client_name: 'Invalid client', expires_at: 'not-a-date' },
      ],
    })
    renderDrawer()

    expect(screen.getByRole('dialog', { name: 'MCP access' })).toHaveAttribute('aria-modal', 'true')
    expect(await screen.findByText('Claude Desktop')).toBeInTheDocument()
    expect(screen.queryByText('Invalid client')).not.toBeInTheDocument()
    expect(screen.getByText('Find matters and read bounded matter context')).toBeInTheDocument()
    expect(screen.getByText('User-approved OAuth grants only')).toBeInTheDocument()
    expect(getAdminWorkspaceMcpGrants).toHaveBeenCalledWith(baseUser.id)
  })

  it('revokes a connection and updates the row summary callback', async () => {
    const actor = userEvent.setup()
    const props = renderDrawer()

    await actor.click(await screen.findByRole('button', { name: 'Revoke' }))
    await actor.click(screen.getByRole('button', { name: 'Revoke connection' }))

    await waitFor(() => expect(revokeAdminWorkspaceMcpGrant).toHaveBeenCalledWith(baseUser.id, activeGrant.id))
    expect(screen.queryByText('Claude Desktop')).not.toBeInTheDocument()
    expect(screen.getAllByText('No active connections')).toHaveLength(2)
    expect(screen.getByRole('status')).toHaveTextContent('Claude Desktop access was revoked.')
    expect(props.onConnectionsChanged).toHaveBeenCalledWith(baseUser.id)
  })

  it('keeps a connection visible and announces a revoke failure', async () => {
    const actor = userEvent.setup()
    revokeAdminWorkspaceMcpGrant.mockRejectedValue({ response: { data: { detail: 'Runtime cleanup failed' } } })
    renderDrawer()

    await actor.click(await screen.findByRole('button', { name: 'Revoke' }))
    await actor.click(screen.getByRole('button', { name: 'Revoke connection' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Runtime cleanup failed')
    expect(screen.getByText('Claude Desktop')).toBeInTheDocument()
  })

  it('supports Escape close without allowing table setup content into the drawer', async () => {
    const actor = userEvent.setup()
    const props = renderDrawer()
    await screen.findByText('Claude Desktop')

    expect(screen.queryByText('Official MCP URL')).not.toBeInTheDocument()
    await actor.keyboard('{Escape}')
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it('traps keyboard focus and wraps at both ends of the drawer', async () => {
    const actor = userEvent.setup()
    renderDrawer()
    await screen.findByText('Claude Desktop')
    const close = screen.getByRole('button', { name: 'Close MCP access drawer' })
    const last = screen.getByRole('button', { name: /MCP Servers/ })

    expect(close).toHaveFocus()
    await actor.keyboard('{Shift>}{Tab}{/Shift}')
    expect(last).toHaveFocus()
    await actor.keyboard('{Tab}')
    expect(close).toHaveFocus()
  })

  it('lets Escape cancel a revoke confirmation without closing the drawer', async () => {
    const actor = userEvent.setup()
    const props = renderDrawer()
    await actor.click(await screen.findByRole('button', { name: 'Revoke' }))
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()

    await actor.keyboard('{Escape}')

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'MCP access' })).toBeInTheDocument()
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it('restores focus to the Manage trigger when the drawer closes', async () => {
    const actor = userEvent.setup()
    render(<DrawerHarness />)
    const trigger = screen.getByRole('button', { name: 'Manage Alex MCP' })
    await actor.click(trigger)
    await screen.findByRole('dialog', { name: 'MCP access' })

    await actor.click(screen.getByRole('button', { name: 'Close MCP access drawer' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'MCP access' })).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
  })
})
