import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getPublicOperatingContract: vi.fn(),
  getPublicServiceStatus: vi.fn(),
}))

vi.mock('../api', () => ({
  API_BASE_URL: '/api',
  getPublicOperatingContract: api.getPublicOperatingContract,
  getPublicServiceStatus: api.getPublicServiceStatus,
}))

import TrustCenterPage from './TrustCenterPage'

describe('TrustCenterPage', () => {
  beforeEach(() => {
    api.getPublicOperatingContract.mockResolvedValue({
      version: '2026-08-29.1',
      truth_rule: 'Only implemented and verified controls are capabilities.',
      controls: [
        { id: 'support', title: 'Support and escalation', status: 'implemented', claim: 'Defined support workflow.', boundary: 'Objectives are not an SLA.' },
        { id: 'penetration-testing', title: 'Penetration testing', status: 'planned', claim: 'Cadence is recorded.', boundary: 'No completed test is claimed.' },
      ],
    })
    api.getPublicServiceStatus.mockResolvedValue({
      published_incident_state: 'none_active',
      service_health: 'not_asserted_by_incident_ledger',
    })
  })

  it('renders live claim boundaries and packet download without turning plans into attainment', async () => {
    render(<MemoryRouter><TrustCenterPage /></MemoryRouter>)
    expect(await screen.findByRole('heading', { name: 'LawHand trust center' })).toBeInTheDocument()
    expect(await screen.findByText('No active published incident')).toBeInTheDocument()
    expect(screen.getByText('penetration testing', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('planned')).toBeInTheDocument()
    expect(screen.getByText('No completed test is claimed.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /download security-review packet/i })).toHaveAttribute('href', '/api/public/security-review-packet')
  })
})
