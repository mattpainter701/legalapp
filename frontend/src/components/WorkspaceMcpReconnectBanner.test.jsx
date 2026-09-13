import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi, describe, expect, it, beforeEach, afterEach } from 'vitest'
import WorkspaceMcpReconnectBanner, { dismissedReconnectKey, reconnectSentence } from './WorkspaceMcpReconnectBanner'

let currentUser = null
vi.mock('../App', () => ({ useAuth: () => ({ user: currentUser }) }))

const renderBanner = () => render(<MemoryRouter><WorkspaceMcpReconnectBanner /></MemoryRouter>)

const withPending = (pending) => ({ id: 'u1', workspace_mcp_reconnect: pending })

describe('reconnect banner', () => {
  beforeEach(() => {
    window.localStorage.clear()
    currentUser = null
  })
  afterEach(() => cleanup())

  it('names every assistant that dropped, so nobody has to guess', () => {
    currentUser = withPending([
      { client_id: 'claude', client_name: 'Claude', disconnected_at: '2026-09-12T10:00:00+00:00' },
      { client_id: 'chatgpt', client_name: 'ChatGPT', disconnected_at: '2026-09-12T10:00:00+00:00' },
    ])
    renderBanner()
    expect(screen.getByRole('status')).toHaveTextContent(/Claude and ChatGPT were disconnected/i)
    expect(screen.getByRole('link', { name: /how to reconnect/i })).toHaveAttribute('href', '/profile')
  })

  it('says the disconnect was deliberate rather than a fault', () => {
    currentUser = withPending([{ client_id: 'claude', client_name: 'Claude', disconnected_at: '2026-09-12T10:00:00+00:00' }])
    renderBanner()
    expect(screen.getByRole('status')).toHaveTextContent(/reset your password/i)
    expect(screen.getByRole('status')).toHaveTextContent(/deliberate/i)
  })

  it('stays out of the way once dismissed', async () => {
    currentUser = withPending([{ client_id: 'claude', client_name: 'Claude', disconnected_at: '2026-09-12T10:00:00+00:00' }])
    const actor = userEvent.setup()
    renderBanner()

    await actor.click(screen.getByRole('button', { name: /dismiss/i }))

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(window.localStorage.getItem(dismissedReconnectKey('u1', '2026-09-12T10:00:00+00:00'))).toBe('1')
  })

  it('does not reappear on reload after being dismissed', () => {
    window.localStorage.setItem(dismissedReconnectKey('u1', '2026-09-12T10:00:00+00:00'), '1')
    currentUser = withPending([{ client_id: 'claude', client_name: 'Claude', disconnected_at: '2026-09-12T10:00:00+00:00' }])
    renderBanner()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('returns after a later reset, because that is a new disconnection', () => {
    window.localStorage.setItem(dismissedReconnectKey('u1', '2026-09-12T10:00:00+00:00'), '1')
    currentUser = withPending([{ client_id: 'claude', client_name: 'Claude', disconnected_at: '2026-09-20T08:00:00+00:00' }])
    renderBanner()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('shows nothing when every assistant is back', () => {
    currentUser = withPending([])
    renderBanner()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows nothing when signed out', () => {
    currentUser = null
    renderBanner()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('lists names in readable English', () => {
    expect(reconnectSentence(['Claude'])).toBe('Claude was disconnected')
    expect(reconnectSentence(['Claude', 'ChatGPT'])).toBe('Claude and ChatGPT were disconnected')
    expect(reconnectSentence(['Claude', 'ChatGPT', 'Codex'])).toBe('Claude, ChatGPT and Codex were disconnected')
  })
})
