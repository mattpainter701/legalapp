import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getPublicSupportPolicy: vi.fn(),
  createOperatingSupportRequest: vi.fn(),
  listOperatingSupportRequests: vi.fn(),
}))

vi.mock('../../api', () => ({
  getPublicSupportPolicy: api.getPublicSupportPolicy,
  createOperatingSupportRequest: api.createOperatingSupportRequest,
  listOperatingSupportRequests: api.listOperatingSupportRequests,
}))

import SupportTab from './SupportTab'

const POLICY = {
  version: '2026-08-29.1',
  coverage: { standard_hours: 'Monday-Friday, 08:00-17:00 America/Chicago' },
  objective_boundary: 'Targets are operating objectives, not an SLA.',
  severities: [
    {
      severity: 'S3',
      definition: 'Non-critical defect with a workaround.',
      acknowledgement_objective_minutes: 480,
      initial_owner: 'support',
      escalation: 'Escalate to support lead when the objective is missed.',
    },
  ],
}

afterEach(() => cleanup())

describe('admin SupportTab', () => {
  beforeEach(() => {
    api.getPublicSupportPolicy.mockReset().mockResolvedValue(POLICY)
    api.listOperatingSupportRequests.mockReset().mockResolvedValue({ items: [] })
    api.createOperatingSupportRequest.mockReset()
  })

  it('files a classified request and reports the acknowledgement clock', async () => {
    const user = userEvent.setup()
    api.createOperatingSupportRequest.mockResolvedValue({
      id: 'req-1',
      severity: 'S3',
      status: 'open',
      acknowledgement_objective_minutes: 480,
      acknowledgement_due_at: '2026-09-08T15:00:00Z',
      policy_version: '2026-08-29.1',
    })

    render(<SupportTab />)
    await waitFor(() => expect(screen.getByText(/Non-critical defect/)).toBeInTheDocument())

    await user.type(screen.getByLabelText(/Subject/), 'Document upload fails')
    await user.type(screen.getByLabelText(/Summary/), 'Uploads return an error for three users since this morning.')
    await user.click(screen.getByRole('button', { name: /File request/i }))

    await waitFor(() => {
      expect(api.createOperatingSupportRequest).toHaveBeenCalledWith({
        severity: 'S3',
        channel: 'workspace',
        subject: 'Document upload fails',
        safe_summary: 'Uploads return an error for three users since this morning.',
      })
    })

    expect(await screen.findByText(/Request S3 filed/)).toBeInTheDocument()
    expect(screen.getByText(/Acknowledgement objective 480 minutes/)).toBeInTheDocument()
    expect(screen.getByText(/policy version 2026-08-29\.1/)).toBeInTheDocument()
    // The list is refreshed rather than optimistically patched.
    expect(api.listOperatingSupportRequests).toHaveBeenCalledTimes(2)
  })

  it('surfaces the rejection reason when the backend refuses unsafe content', async () => {
    const user = userEvent.setup()
    api.createOperatingSupportRequest.mockRejectedValue({
      response: { data: { detail: 'evidence contains a secret-like value' } },
    })

    render(<SupportTab />)
    await waitFor(() => expect(api.getPublicSupportPolicy).toHaveBeenCalled())

    await user.type(screen.getByLabelText(/Subject/), 'Cannot sign in')
    await user.type(screen.getByLabelText(/Summary/), 'password is hunter2')
    await user.click(screen.getByRole('button', { name: /File request/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('evidence contains a secret-like value')
  })

  it('warns against pasting credentials before anything is submitted', async () => {
    render(<SupportTab />)

    expect(screen.getByText(/Do not paste passwords, API keys, tokens, authorization codes/i)).toBeInTheDocument()
  })

  it('renders prior requests with their acknowledgement state', async () => {
    api.listOperatingSupportRequests.mockResolvedValue({
      items: [{
        id: 'req-9',
        severity: 'S1',
        status: 'acknowledged',
        subject: 'Workspace unavailable',
        acknowledgement_due_at: '2026-09-07T18:00:00Z',
        created_at: '2026-09-07T17:00:00Z',
      }],
    })

    render(<SupportTab />)

    expect(await screen.findByText('Workspace unavailable')).toBeInTheDocument()
    expect(screen.getByText('acknowledged')).toBeInTheDocument()
  })
})
