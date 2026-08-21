import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ClientsPage from './ClientsPage'
import { createClient, getClients, getClientSummary } from '../api'

vi.mock('../App', () => ({
  useAuth: () => ({ user: { id: 'user-1', role: 'admin', enabled_modules: ['contacts'] } }),
}))

vi.mock('../api', () => ({
  getClients: vi.fn(),
  getClientSummary: vi.fn(),
  createClient: vi.fn(),
  importClientsCsv: vi.fn(),
  exportClientsCsv: vi.fn(),
}))

const clientRecord = {
  id: 'client-1',
  display_name: 'Jordan Rivera',
  entity_type: 'person',
  client_status: 'active',
  client_number: 'CL-1042',
  email: 'jordan@example.com',
  phone: '+1 701 555 0100',
  sms_opt_in: true,
}

describe('ClientsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getClients.mockResolvedValue({ items: [clientRecord], total: 1 })
    getClientSummary.mockResolvedValue({
      total: 1, active: 1, prospects: 0, inactive: 0, former: 0, sms_opted_in: 1,
    })
    createClient.mockResolvedValue({ ...clientRecord, id: 'client-2', display_name: 'Avery Stone' })
  })

  afterEach(() => cleanup())

  it('renders the dedicated CRM workspace with labeled controls and no axe violations', async () => {
    const { container } = render(<MemoryRouter><ClientsPage /></MemoryRouter>)
    expect(await screen.findByRole('heading', { name: 'Clients & CRM' })).toBeInTheDocument()
    expect(await screen.findByText('Jordan Rivera')).toBeInTheDocument()
    expect(screen.getAllByText('SMS consent').length).toBeGreaterThan(0)
    expect(screen.getByRole('textbox', { name: 'Search clients' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Filter by client status' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Import CSV/i })).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it('creates a client from the dedicated quick-add dialog', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><ClientsPage /></MemoryRouter>)
    await screen.findByText('Jordan Rivera')
    await user.click(screen.getByRole('button', { name: /New client/i }))
    expect(screen.getByRole('dialog', { name: 'New client' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('First name'), 'Avery')
    await user.type(screen.getByLabelText('Last name'), 'Stone')
    await user.type(screen.getByLabelText('Email'), 'avery@example.com')
    await user.click(screen.getByRole('button', { name: 'Create client' }))
    await waitFor(() => expect(createClient).toHaveBeenCalledWith(expect.objectContaining({
      first_name: 'Avery', last_name: 'Stone', email: 'avery@example.com', client_status: 'active',
    })))
  })
})
