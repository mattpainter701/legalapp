import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReleaseAnnouncement, { seenReleaseKey } from './ReleaseAnnouncement'
import { getAppVersion } from '../api'

const authUser = { id: 'user-1', role: 'user' }

vi.mock('../App', () => ({
  useAuth: () => ({ user: authUser }),
}))

vi.mock('../api', () => ({
  getAppVersion: vi.fn(),
}))

const recentRelease = {
  id: '2026.08.20',
  version: '2026.08.20',
  title: 'A clearer view of what changed',
  summary: 'See what changed without leaving your workspace.',
  is_recent: true,
  highlights: [
    { title: 'Release updates in LawHand', description: 'Review version details and release notes.' },
  ],
}

describe('ReleaseAnnouncement', () => {
  beforeEach(() => {
    window.localStorage.clear()
    getAppVersion.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it('shows a recent unseen release and remembers dismissal per user', async () => {
    getAppVersion.mockResolvedValue({ latest_release: recentRelease })

    const { container } = render(
      <MemoryRouter>
        <ReleaseAnnouncement />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('dialog', { name: recentRelease.title })).toBeInTheDocument()
    expect(screen.getByText('Release updates in LawHand')).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()

    fireEvent.click(screen.getByRole('button', { name: 'Got it' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(window.localStorage.getItem(seenReleaseKey(authUser.id, recentRelease.id))).toBe('1')
  })

  it('does not show a release already seen by this user', async () => {
    window.localStorage.setItem(seenReleaseKey(authUser.id, recentRelease.id), '1')
    getAppVersion.mockResolvedValue({ latest_release: recentRelease })

    render(
      <MemoryRouter>
        <ReleaseAnnouncement />
      </MemoryRouter>,
    )

    await waitFor(() => expect(getAppVersion).toHaveBeenCalledOnce())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not interrupt login for an older release or a failed version request', async () => {
    getAppVersion.mockResolvedValueOnce({
      latest_release: { ...recentRelease, is_recent: false },
    })

    render(
      <MemoryRouter>
        <ReleaseAnnouncement />
      </MemoryRouter>,
    )
    await waitFor(() => expect(getAppVersion).toHaveBeenCalledOnce())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    cleanup()
    getAppVersion.mockRejectedValueOnce(new Error('offline'))
    render(
      <MemoryRouter>
        <ReleaseAnnouncement />
      </MemoryRouter>,
    )
    await waitFor(() => expect(getAppVersion).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
