import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi, describe, expect, it, beforeEach, afterEach } from 'vitest'
import ProfilePage from './ProfilePage'
import { revokeAllSessions } from '../api'

const user = { id: 'u1', full_name: 'Test Attorney', email: 'a@firm.com', role: 'admin', license_active: true, is_active: true }

vi.mock('../App', () => ({ useAuth: () => ({ user, refreshUser: vi.fn() }) }))
vi.mock('../api', () => ({
  getMyMatters: vi.fn().mockResolvedValue([]),
  getTimeEntries: vi.fn().mockResolvedValue([]),
  getWorkspaceMcpGrants: vi.fn().mockResolvedValue({ items: [] }),
  revokeAllSessions: vi.fn().mockResolvedValue({ user_id: 'u1' }),
  updateMe: vi.fn().mockResolvedValue({}),
}))
vi.mock('../components/ReleaseInfoPanel', () => ({ default: () => null }))
vi.mock('../components/WorkspaceMcpGrantsPanel', () => ({ default: () => null }))

const renderPage = () => render(<MemoryRouter><ProfilePage /></MemoryRouter>)

describe('signing out everywhere else', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    revokeAllSessions.mockResolvedValue({ user_id: 'u1' })
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('confirms first, since it signs other people out of their work', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))
    const actor = userEvent.setup()
    renderPage()

    await actor.click(screen.getByRole('button', { name: /sign out everywhere else/i }))

    expect(window.confirm).toHaveBeenCalled()
    expect(revokeAllSessions).not.toHaveBeenCalled()
  })

  it('reports that this device stays signed in', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    const actor = userEvent.setup()
    renderPage()

    await actor.click(screen.getByRole('button', { name: /sign out everywhere else/i }))

    await waitFor(() => expect(revokeAllSessions).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/this device stays signed in/i)).toBeInTheDocument()
  })

  it('surfaces a failure rather than implying the sessions ended', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    revokeAllSessions.mockRejectedValueOnce(new Error('Network Error'))
    const actor = userEvent.setup()
    renderPage()

    await actor.click(screen.getByRole('button', { name: /sign out everywhere else/i }))

    expect(await screen.findByText(/could not be signed out/i)).toBeInTheDocument()
  })
})
