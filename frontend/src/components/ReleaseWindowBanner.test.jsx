import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReleaseWindowBanner, { dismissedReleaseWindowKey } from './ReleaseWindowBanner'
import { getReleaseWindow } from '../api'

vi.mock('../App', () => ({
  useAuth: () => ({ user: mockUser }),
}))

vi.mock('../api', () => ({
  getReleaseWindow: vi.fn(),
}))

let mockUser = null

const activeWindow = {
  active: true,
  window_id: 'abc123def456',
  message: 'We are giving LawHand a quick polish to keep everything running smoothly.',
}

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.clearAllMocks()
})

beforeEach(() => {
  mockUser = null
})

describe('ReleaseWindowBanner', () => {
  it('stays hidden when nobody is signed in', async () => {
    mockUser = null
    getReleaseWindow.mockResolvedValue(activeWindow)
    const { container } = render(<ReleaseWindowBanner />)
    await waitFor(() => expect(getReleaseWindow).not.toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the advisory to portal clients, not just firm staff', async () => {
    mockUser = { id: 'client-1', role: 'client' }
    getReleaseWindow.mockResolvedValue(activeWindow)
    render(<ReleaseWindowBanner />)
    expect(await screen.findByRole('status')).toHaveTextContent(activeWindow.message)
  })

  it('stays quiet when the endpoint reports no active window', async () => {
    mockUser = { id: 'user-1', role: 'admin' }
    getReleaseWindow.mockResolvedValue({ active: false, window_id: null, message: null })
    const { container } = render(<ReleaseWindowBanner />)
    await waitFor(() => expect(getReleaseWindow).toHaveBeenCalledTimes(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('stays quiet when the endpoint errors', async () => {
    mockUser = { id: 'user-1', role: 'admin' }
    getReleaseWindow.mockRejectedValue(new Error('network down'))
    const { container } = render(<ReleaseWindowBanner />)
    await waitFor(() => expect(getReleaseWindow).toHaveBeenCalledTimes(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('dismisses for the current window id and stays dismissed', async () => {
    mockUser = { id: 'user-1', role: 'admin' }
    getReleaseWindow.mockResolvedValue(activeWindow)
    const { container } = render(<ReleaseWindowBanner />)
    await screen.findByRole('status')

    await userEvent.click(screen.getByRole('button', { name: /dismiss maintenance notice/i }))

    expect(container).toBeEmptyDOMElement()
    expect(window.localStorage.getItem(dismissedReleaseWindowKey('user-1', 'abc123def456'))).toBe('1')
  })

  it('does not reshow a window id the user already dismissed', async () => {
    mockUser = { id: 'user-1', role: 'admin' }
    window.localStorage.setItem(dismissedReleaseWindowKey('user-1', 'abc123def456'), '1')
    getReleaseWindow.mockResolvedValue(activeWindow)
    const { container } = render(<ReleaseWindowBanner />)
    await waitFor(() => expect(getReleaseWindow).toHaveBeenCalledTimes(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('shows a fresh window id even when a previous one was dismissed', async () => {
    mockUser = { id: 'user-1', role: 'admin' }
    window.localStorage.setItem(dismissedReleaseWindowKey('user-1', 'oldwindow000'), '1')
    getReleaseWindow.mockResolvedValue(activeWindow)
    render(<ReleaseWindowBanner />)
    expect(await screen.findByRole('status')).toHaveTextContent(activeWindow.message)
  })
})
