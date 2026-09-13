import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, expect, it, beforeEach, afterEach } from 'vitest'
import WorkspaceMcpGrantsPanel, { WORKSPACE_MCP_URL } from './WorkspaceMcpGrantsPanel'

vi.mock('../api', () => ({
  getWorkspaceMcpGrants: vi.fn().mockResolvedValue({ items: [] }),
  revokeWorkspaceMcpGrant: vi.fn(),
}))

const pending = [
  { client_id: 'claude', client_name: 'Claude', disconnected_at: '2026-09-12T10:00:00+00:00' },
]

const renderPanel = (pendingReconnect = pending) =>
  render(<WorkspaceMcpGrantsPanel pendingReconnect={pendingReconnect} />)

const card = async () =>
  within(await screen.findByRole('region', { name: /waiting to be reconnected/i }))

describe('reconnect card in the grants panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('names the assistant and says reconnecting happens in the assistant', async () => {
    renderPanel()
    const region = await card()
    expect(region.getByText('Claude')).toBeInTheDocument()
    expect(region.getByText(/reconnecting is done from the assistant/i)).toBeInTheDocument()
  })

  it('offers the server URL people would otherwise have to hunt for', async () => {
    // userEvent.setup() installs its own clipboard stub, so spy after it rather
    // than replacing navigator wholesale — a spread copy of navigator drops the
    // prototype getters userEvent relies on.
    const actor = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
    renderPanel()

    const region = await card()
    expect(region.getByText(WORKSPACE_MCP_URL)).toBeInTheDocument()
    await actor.click(region.getByRole('button', { name: /copy url/i }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(WORKSPACE_MCP_URL))
    expect(await region.findByRole('button', { name: /copied/i })).toBeInTheDocument()
  })

  it('survives a browser that refuses clipboard access', async () => {
    const actor = userEvent.setup()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    renderPanel()

    const region = await card()
    await actor.click(region.getByRole('button', { name: /copy url/i }))

    // The URL is still on screen to select by hand, and nothing has crashed.
    expect(region.getByText(WORKSPACE_MCP_URL)).toBeInTheDocument()
    expect(region.getByRole('button', { name: /copy url/i })).toBeInTheDocument()
  })

  it('shows no card when nothing is waiting to be reconnected', async () => {
    renderPanel([])
    await waitFor(() => expect(screen.queryByRole('region', { name: /waiting to be reconnected/i })).not.toBeInTheDocument())
  })
})
