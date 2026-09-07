import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getPublicSupportPolicy: vi.fn() }))

vi.mock('../api', () => ({
  getPublicSupportPolicy: api.getPublicSupportPolicy,
}))

import SupportPage from './SupportPage'

const POLICY = {
  version: '2026-08-29.1',
  coverage: {
    standard_hours: 'Monday-Friday, 08:00-17:00 America/Chicago',
    after_hours: 'S1 reports use the emergency channel identified in the customer order form.',
    exceptions: 'No automatic holiday exclusion is claimed.',
  },
  objective_boundary:
    'Acknowledgement and escalation targets are operating objectives, not an SLA, warranty, or service-credit promise unless incorporated into signed customer terms.',
  severities: [
    {
      severity: 'S1',
      definition: 'Production-wide unavailability with no safe workaround.',
      acknowledgement_objective_minutes: 60,
      initial_owner: 'incident commander',
      escalation: 'Route immediately to operations.',
    },
    {
      severity: 'S3',
      definition: 'Non-critical defect with a workaround.',
      acknowledgement_objective_minutes: 480,
      initial_owner: 'support',
      escalation: 'Escalate to support lead when the objective is missed.',
    },
  ],
}

function renderPage() {
  return render(<MemoryRouter><SupportPage /></MemoryRouter>)
}

afterEach(() => cleanup())

describe('SupportPage', () => {
  beforeEach(() => {
    api.getPublicSupportPolicy.mockReset()
    api.getPublicSupportPolicy.mockResolvedValue(POLICY)
  })

  it('publishes the support address and the live severity objectives', async () => {
    renderPage()

    expect(screen.getByRole('link', { name: /support@getlawhand\.com/ }))
      .toHaveAttribute('href', 'mailto:support@getlawhand.com')

    await waitFor(() => {
      expect(screen.getByText('Monday-Friday, 08:00-17:00 America/Chicago')).toBeInTheDocument()
    })

    expect(screen.getByRole('rowheader', { name: 'S1' })).toBeInTheDocument()
    expect(screen.getByText('1 covered hour')).toBeInTheDocument()
    expect(screen.getByText('8 covered hours')).toBeInTheDocument()
    expect(screen.getByText(/Policy version 2026-08-29\.1/)).toBeInTheDocument()
  })

  it('states the objectives-not-an-SLA boundary from the published policy', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText(POLICY.objective_boundary)).toBeInTheDocument()
    })
    expect(screen.getByRole('heading', { name: /operating objectives, not an SLA/i })).toBeInTheDocument()
  })

  it('keeps the email path and the boundary usable when the policy cannot load', async () => {
    api.getPublicSupportPolicy.mockRejectedValue(new Error('offline'))
    renderPage()

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())

    // The contact route and the claim boundary must never depend on the fetch.
    expect(screen.getByRole('link', { name: /support@getlawhand\.com/ }))
      .toHaveAttribute('href', 'mailto:support@getlawhand.com')
    expect(screen.getByText(/not an SLA, warranty, or service-credit promise/i)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('links onward to the requirements page and the incident record', async () => {
    renderPage()
    await waitFor(() => expect(api.getPublicSupportPolicy).toHaveBeenCalled())

    expect(screen.getByRole('link', { name: /Requirements and integrations/i }))
      .toHaveAttribute('href', '/requirements')
    expect(screen.getByRole('link', { name: /Incident history/i }))
      .toHaveAttribute('href', '/trust-center')
  })
})
