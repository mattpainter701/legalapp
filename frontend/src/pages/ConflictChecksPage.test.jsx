import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ConflictChecksPage from './ConflictChecksPage'
import {
  closeConflictCheck,
  createConflictCheck,
  getMyMatters,
  listConflictChecks,
} from '../api'

vi.mock('../api', () => ({
  listConflictChecks: vi.fn(),
  createConflictCheck: vi.fn(),
  closeConflictCheck: vi.fn(),
  downloadConflictCheckReport: vi.fn(),
  getMyMatters: vi.fn(),
}))

const openRecord = {
  id: 'check-1',
  matter_id: null,
  label: 'Smith intake',
  query: { names: ['Alice Smith'], organizations: [], emails: [] },
  matches: [],
  match_count: 0,
  restricted_matter_count: 0,
  status: 'open',
  decision: 'needs_review',
  notes: null,
  created_at: '2026-08-27T12:00:00Z',
  closed_at: null,
}

describe('ConflictChecksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listConflictChecks.mockResolvedValue({ items: [], total: 0 })
    getMyMatters.mockResolvedValue([])
    createConflictCheck.mockResolvedValue(openRecord)
    closeConflictCheck.mockResolvedValue({
      ...openRecord,
      status: 'closed',
      decision: 'no_conflict_found',
      notes: 'Reviewed the contact and representation history.',
      closed_at: '2026-08-27T12:10:00Z',
    })
  })

  afterEach(cleanup)

  it('runs a saved search and requires an acknowledged review before closing', async () => {
    const user = userEvent.setup()
    render(<ConflictChecksPage />)

    expect(await screen.findByRole('heading', { name: 'Conflict Search' })).toBeInTheDocument()
    await user.type(screen.getByLabelText(/Search label/), 'Smith intake')
    await user.type(screen.getByLabelText(/People and known aliases/), 'Alice Smith')
    await user.click(screen.getByRole('button', { name: /Run and save search/ }))

    await waitFor(() => expect(createConflictCheck).toHaveBeenCalledWith({
      label: 'Smith intake',
      names: ['Alice Smith'],
      organization_names: [],
      emails: [],
      matter_id: null,
    }))
    expect(await screen.findByText(/No potential matches were returned/)).toBeInTheDocument()

    await user.type(
      screen.getByPlaceholderText(/Document sources reviewed/),
      'Reviewed the contact and representation history.',
    )
    const closeButton = screen.getByRole('button', { name: /Close and lock record/ })
    expect(closeButton).toBeDisabled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(closeButton)

    await waitFor(() => expect(closeConflictCheck).toHaveBeenCalledWith(
      'check-1',
      expect.objectContaining({
        decision: 'no_conflict_found',
        acknowledge_attorney_review: true,
      }),
    ))
    expect(await screen.findByText(/This record is immutable/)).toBeInTheDocument()
  })
})
