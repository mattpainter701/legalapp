import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import IntakePage from './IntakePage'
import { convertLead, getMatterFieldOptions } from '../api'

vi.mock('../App', () => ({ useAuth: () => ({ user: { id: 'user-1' } }) }))

vi.mock('../api', () => ({
  getLeads: vi.fn(() => Promise.resolve([{
    id: 'lead-1',
    status: 'engaged',
    practice_area: 'divorce',
    source: 'referral',
    created_at: '2026-07-20T12:00:00Z',
    contact_id: 'contact-1',
    contact: { display_name: 'Jane Doe' },
  }])),
  createLead: vi.fn(),
  updateLead: vi.fn(),
  convertLead: vi.fn(),
  getMatterFieldOptions: vi.fn(),
  getSmsReviewItems: vi.fn(() => Promise.resolve([])),
  decideSmsReviewItem: vi.fn(),
  getSmsReconciliationItems: vi.fn(() => Promise.resolve([])),
  reconcileSmsMessage: vi.fn(),
}))

describe('IntakePage matter conversion', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getMatterFieldOptions.mockResolvedValue({
      matter_types: ['Divorce', 'Guardianship'],
      roles: ['Petitioner'],
      jurisdictions: ['North Dakota'],
      counterparties: ['John Doe'],
    })
    convertLead.mockResolvedValue({ matter_id: 'matter-1' })
  })

  it('selects firm-used values and allows a new value', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><IntakePage /></MemoryRouter>)

    await user.click(await screen.findByRole('button', { name: 'Convert to Matter' }))
    await waitFor(() => expect(getMatterFieldOptions).toHaveBeenCalledOnce())

    const jurisdiction = screen.getByRole('combobox', { name: /Jurisdiction/ })
    await user.click(jurisdiction)
    await user.click(screen.getByRole('option', { name: 'North Dakota' }))

    const counterparty = screen.getByRole('combobox', { name: 'Counterparty' })
    await user.click(counterparty)
    await user.click(screen.getByRole('option', { name: 'John Doe' }))

    const matterType = screen.getByRole('combobox', { name: 'Matter Type' })
    await user.clear(matterType)
    await user.type(matterType, 'Space Law')
    await user.click(screen.getByRole('option', { name: /Use new value:.*Space Law/ }))

    await user.click(screen.getByRole('button', { name: 'Create Matter' }))
    await waitFor(() => expect(convertLead).toHaveBeenCalledWith('lead-1', expect.objectContaining({
      matter_type: 'Space Law',
      jurisdiction: 'North Dakota',
      counterparty: 'John Doe',
    })))
  })
})
