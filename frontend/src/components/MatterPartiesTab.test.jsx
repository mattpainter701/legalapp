import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MatterPartiesTab from './MatterPartiesTab'
import {
  addMatterParty,
  getContacts,
  getMatterParties,
} from '../api'

vi.mock('../api', () => ({
  addMatterParty: vi.fn(),
  getContacts: vi.fn(),
  getMatterParties: vi.fn(),
  removeMatterParty: vi.fn(),
}))

describe('MatterPartiesTab caption roles', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getMatterParties.mockResolvedValue({
      items: [],
      total: 0,
      role_definitions: [
        {
          value: 'plaintiff',
          label: 'Plaintiff',
          description: 'A party asserting claims in a civil action.',
        },
        {
          value: 'defendant',
          label: 'Defendant',
          description: 'A party defending against claims in a civil action.',
        },
        {
          value: 'client',
          label: 'Client',
          description: 'The contact represented by the firm.',
        },
      ],
    })
    getContacts.mockResolvedValue({
      items: [{ id: 'contact-1', display_name: 'Dana Defendant' }],
    })
    addMatterParty.mockResolvedValue({
      id: 'party-1',
      matter_id: 'matter-1',
      contact_id: 'contact-1',
      contact_display_name: 'Dana Defendant',
      role: 'defendant',
      is_primary: true,
      notes: null,
    })
  })

  afterEach(() => cleanup())

  it('defines plaintiff and defendant separately from the client relationship', async () => {
    const user = userEvent.setup()
    render(<MatterPartiesTab matterId="matter-1" />)

    expect(await screen.findByText(/Client describes the firm's relationship/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add Party' }))
    expect(screen.getByRole('option', { name: 'Plaintiff' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Defendant' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Role'), 'defendant')
    expect(screen.getByText('A party defending against claims in a civil action.')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Contact'), 'contact-1')
    await user.click(screen.getByRole('checkbox', { name: 'Primary for this role' }))
    await user.click(screen.getAllByRole('button', { name: 'Add Party' })[1])

    await waitFor(() => expect(addMatterParty).toHaveBeenCalledWith('matter-1', {
      matter_id: 'matter-1',
      contact_id: 'contact-1',
      role: 'defendant',
      is_primary: true,
      notes: null,
    }))
    expect(await screen.findByText('Dana Defendant')).toBeInTheDocument()
  })
})
