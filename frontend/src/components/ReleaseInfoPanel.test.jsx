import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReleaseInfoPanel from './ReleaseInfoPanel'
import { getAppVersion } from '../api'

vi.mock('../api', () => ({
  getAppVersion: vi.fn(),
}))

describe('ReleaseInfoPanel', () => {
  beforeEach(() => {
    getAppVersion.mockReset()
  })

  it('shows deployed build metadata and customer-facing release history', async () => {
    getAppVersion.mockResolvedValue({
      version: 'abc1234',
      commit: 'abc1234567890',
      short_commit: 'abc123456789',
      build_time: '2026-08-20T18:00:00Z',
      release_notes: [{
        id: '2026.08.20',
        version: '2026.08.20',
        title: 'A clearer view of what changed',
        published_at: '2026-08-20',
        summary: 'See what changed without leaving your workspace.',
        highlights: [{
          title: 'Release updates in LawHand',
          description: 'Review version details and release notes.',
        }],
      }],
    })

    render(<ReleaseInfoPanel />)

    expect(await screen.findByText('A clearer view of what changed')).toBeInTheDocument()
    expect(screen.getByText('abc1234')).toBeInTheDocument()
    expect(screen.getByText('abc123456789')).toBeInTheDocument()
    expect(screen.getByText('Release updates in LawHand')).toBeInTheDocument()
    expect(screen.getByText('Latest')).toBeInTheDocument()
  })

  it('fails softly when version information cannot be loaded', async () => {
    getAppVersion.mockRejectedValue(new Error('offline'))

    render(<ReleaseInfoPanel />)

    expect(await screen.findByText('Version information is temporarily unavailable.')).toBeInTheDocument()
  })
})
